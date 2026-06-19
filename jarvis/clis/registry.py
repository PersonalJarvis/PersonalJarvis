"""``CliToolRegistry`` — aggregates CLI specs and runtime status into tool instances."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from jarvis.clis.auth import CliAuthManager
from jarvis.clis.catalog import CliCatalog
from jarvis.clis.installer import CliInstaller
from jarvis.clis.prober import CliStatusProber
from jarvis.clis.spec import CliSpec, CliStatus
from jarvis.clis.tool import CliTool
from jarvis.clis.usage_log import UsageLog

log = logging.getLogger(__name__)


class CliToolRegistry:
    def __init__(
        self,
        *,
        catalog: CliCatalog | None = None,
        prober: CliStatusProber | None = None,
        auth: CliAuthManager | None = None,
        usage_log: UsageLog | None = None,
        installer: CliInstaller | None = None,
        bus: Any = None,
    ) -> None:
        self._catalog = catalog or CliCatalog()
        self._prober = prober or CliStatusProber()
        self._auth = auth or CliAuthManager(prober=self._prober)
        self._usage = usage_log or UsageLog()
        self._installer = installer or CliInstaller()
        self._bus = bus
        self._tools: dict[str, CliTool] = {}
        self._status_cache: dict[str, CliStatus] = {}
        self._bootstrapped = False

    async def bootstrap(self) -> None:
        specs = list(self._catalog.all().values())
        self._status_cache = await self._prober.probe_all(specs)
        self._tools = {}
        for spec in specs:
            status = self._status_cache.get(spec.name)
            if not status or not self._is_usable(spec, status):
                continue
            tool = CliTool(spec, auth=self._auth, usage_log=self._usage)
            self._tools[tool.name] = tool
        self._bootstrapped = True
        self._sync_capabilities()
        log.info(
            "cli-registry: %d von %d CLIs als Tools exponiert",
            len(self._tools),
            len(specs),
        )
        # Live-reload bridge: the UI server schedules bootstrap() as a
        # background task that usually finishes AFTER the brain was built
        # (which expanded ``cli-tools`` to an empty list). Publish
        # ``BrainToolsChanged`` so the live brain re-expands and picks up the
        # CLIs that probed as connected during this bootstrap pass.
        if self._tools:
            await self._publish_brain_tools_changed(
                cli_name="*",
                connected=True,
            )

    def active_tools(self) -> list[CliTool]:
        return list(self._tools.values())

    def status_of(self, cli_name: str) -> CliStatus | None:
        return self._status_cache.get(cli_name)

    def all_status(self) -> dict[str, CliStatus]:
        return dict(self._status_cache)

    def is_bootstrapped(self) -> bool:
        return self._bootstrapped

    def catalog(self) -> CliCatalog:
        return self._catalog

    def auth(self) -> CliAuthManager:
        return self._auth

    def usage_log(self) -> UsageLog:
        return self._usage

    def installer(self) -> CliInstaller:
        return self._installer

    def bus(self) -> Any:
        return self._bus

    def risk_patterns(self) -> tuple[list[str], list[str]]:
        """Flatten all whitelist/blacklist patterns from all catalog specs.

        Returns ``(whitelist, blacklist)`` as two flat lists. Called by
        ``RiskTierEvaluator`` via ``extra_patterns_fn`` to merge the per-CLI
        declared patterns (``spec.risk.{whitelist,blacklist}_patterns``) into
        the global safety evaluation.

        Important: we export patterns for **all** catalog entries, not just
        connected ones. Rationale: the CliTool name is ``cli_<spec.name>`` and
        fnmatch only matches when the tool is actually invoked — disconnected
        CLIs do not generate false-positive matches on other tools.

        The pattern is **extended with the CliTool prefix**: spec pattern
        ``gcloud * delete *`` becomes ``cli_gcloud gcloud * delete *`` — it
        must match the tool-name prefix that the evaluator checks against
        ``f"{tool.name} {serialized_args}"``.
        """
        from jarvis.clis.tool import TOOL_NAME_PREFIX

        whitelist: list[str] = []
        blacklist: list[str] = []
        for spec in self._catalog.all().values():
            tool_prefix = f"{TOOL_NAME_PREFIX}{spec.name} "
            for p in spec.risk.whitelist_patterns:
                whitelist.append(f"{tool_prefix}{p}")
            for p in spec.risk.blacklist_patterns:
                blacklist.append(f"{tool_prefix}{p}")
        return whitelist, blacklist

    async def refresh_status(self, cli_name: str) -> CliStatus | None:
        spec = self._catalog.get(cli_name)
        if not spec:
            return None
        old_status = self._status_cache.get(cli_name)
        status = await self._prober.probe(spec)
        self._status_cache[cli_name] = status
        tool_name = f"cli_{cli_name}"
        had_tool = tool_name in self._tools
        if self._is_usable(spec, status):
            if not had_tool:
                self._tools[tool_name] = CliTool(
                    spec,
                    auth=self._auth,
                    usage_log=self._usage,
                )
        else:
            self._tools.pop(tool_name, None)
        tool_set_changed = (tool_name in self._tools) != had_tool

        await self._maybe_publish_status_change(cli_name, old_status, status, spec)
        # Live-reload bridge: when this CLI just became usable (a new
        # ``cli_<name>`` tool appeared) or stopped being usable (its tool was
        # removed), the live brain must re-expand its tool set. ``BrainToolsChanged``
        # is what ``BrainManager.refresh_tools()`` subscribes to. Without this,
        # connecting a CLI in the UI would not surface ``cli_<name>`` to the
        # running brain until the next app restart.
        if tool_set_changed:
            self._sync_capabilities()
            await self._publish_brain_tools_changed(cli_name, tool_name in self._tools)
        return status

    async def _publish_brain_tools_changed(self, cli_name: str, connected: bool) -> None:
        if self._bus is None:
            return
        from jarvis.core.events import BrainToolsChanged

        verb = "cli_connected" if connected else "cli_disconnected"
        event = BrainToolsChanged(
            source_layer="clis.registry",
            reason=f"{verb}:{cli_name}",
        )
        try:
            if asyncio.iscoroutinefunction(self._bus.publish):
                await self._bus.publish(event)
            else:
                self._bus.publish(event)
        except Exception as exc:  # noqa: BLE001
            log.debug("BrainToolsChanged publish failed: %s", exc)

    async def _maybe_publish_status_change(
        self,
        cli_name: str,
        old: CliStatus | None,
        new: CliStatus,
        spec: CliSpec,
    ) -> None:
        if self._bus is None:
            return
        from jarvis.core.events import CliStatusChanged

        def label(s: CliStatus | None) -> str:
            if s is None:
                return "checking"
            if s.error:
                return "error"
            if not s.installed:
                return "not_installed"
            if spec.auth.type in ("none", "config_file"):
                return "connected"
            return "connected" if s.auth_status == "connected" else "disconnected"

        old_label, new_label = label(old), label(new)
        if old_label == new_label and (old and new and old.version == new.version):
            return
        event = CliStatusChanged(
            source_layer="clis.registry",
            cli_name=cli_name,
            old_status=old_label,
            new_status=new_label,
            version=new.version,
            error=new.error,
        )
        try:
            if asyncio.iscoroutinefunction(self._bus.publish):
                await self._bus.publish(event)
            else:
                self._bus.publish(event)
        except Exception as exc:  # noqa: BLE001
            log.debug("bus publish failed: %s", exc)

    def _sync_capabilities(self) -> None:
        """Mirror the usable-CLI set into the global CapabilityRegistry.

        Defensive: a capabilities-module failure must never break the CLI
        lifecycle (bootstrap/refresh), only disable intent resolution
        (AD-CLI3 — registered while usable, withdrawn on disconnect).
        """
        try:
            from jarvis.clis.capability_provider import sync_registry
            from jarvis.core.capabilities import get_registry

            sync_registry(self, get_registry())
        except Exception:  # noqa: BLE001
            log.debug("cli capability sync skipped", exc_info=True)

    @staticmethod
    def _is_usable(spec: CliSpec, status: CliStatus) -> bool:
        if not status.installed:
            return False
        if spec.auth.type in ("none", "config_file"):
            return True
        return status.auth_status == "connected"


__all__ = ["CliToolRegistry"]
