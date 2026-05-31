"""``CliToolLoader`` — the single static entry point for the CLI tool slot.

We register exactly one entry point in ``pyproject.toml``:
``cli-tools = jarvis.clis.loader:CliToolLoader``. The brain launcher recognises
the ``CliToolLoader`` instance via ``is_virtual_loader=True`` and calls
``expand() -> list[Tool]``.

Shared-registry resolution (the production fix):

``expand()`` resolves the **shared** registry via
``jarvis.clis.shared.get_active_registry()`` first. The UI server constructs
that registry, attaches the bus, bootstraps it, and publishes it via
``set_active_registry()`` (see ``jarvis/ui/web/server.py``). The brain and the
safety layer (``risk_integration.make_cli_patterns_fn``) therefore see the SAME
connected CLIs on the SAME bus — no split-brain registry.

Only when no shared registry exists (headless ``jarvis-ask`` / voice without the
web server) does the loader fall back to a lazily-bootstrapped private registry.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from jarvis.clis.registry import CliToolRegistry
from jarvis.clis.tool import CliTool
from jarvis.core.protocols import ExecutionContext, ToolResult

log = logging.getLogger(__name__)


class CliToolLoader:
    is_virtual_loader: bool = True

    name: str = "cli_tools_loader"
    description: str = (
        "Virtual CLI-tool loader. Never called by the brain directly — "
        "the launcher expands it into N CliTool instances."
    )
    risk_tier: str = "block"
    schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    def __init__(self) -> None:
        # Private fallback registry. Only built when no shared registry exists.
        # Stays None until first needed so we don't double the catalog probe
        # in the common case where the UI server already published one.
        self._private_registry: CliToolRegistry | None = None

    def _resolve_shared_registry(self) -> Any:
        """Return the shared active registry, or ``None`` when not published.

        Swallows import/attribute errors so a partially-initialised shared
        module never breaks the brain build — we just fall back to private.
        """
        try:
            from jarvis.clis.shared import get_active_registry

            return get_active_registry()
        except Exception as exc:  # noqa: BLE001
            log.debug("cli-loader: shared registry lookup failed: %s", exc)
            return None

    def _ensure_private_registry(self) -> CliToolRegistry:
        if self._private_registry is None:
            self._private_registry = CliToolRegistry()
        return self._private_registry

    def expand(self) -> list[CliTool]:
        """Expand to one ``CliTool`` per connected & usable CLI.

        Resolution order:

        1. **Shared registry** (UI server / async context): if a shared
           registry is published, return its active tools. When it is not yet
           bootstrapped (the server schedules ``bootstrap()`` as a background
           task), this returns ``[]`` for now — the live-reload bridge
           (``BrainToolsChanged`` on ``CliStatusChanged``) re-runs ``expand()``
           once the probe finishes or a CLI connects, so no restart is needed.
        2. **Private registry** (headless ``jarvis-ask`` / voice without web):
           lazily build + synchronously bootstrap a private registry. In an
           async context without a shared registry we cannot run a blocking
           bootstrap, so we return ``[]`` and rely on a later refresh.
        """
        shared = self._resolve_shared_registry()
        if shared is not None:
            try:
                return list(shared.active_tools())
            except Exception as exc:  # noqa: BLE001
                log.debug("cli-loader: shared.active_tools() failed: %s", exc)
                return []

        # No shared registry — fall back to a private one.
        registry = self._ensure_private_registry()
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # Sync context (no running loop): safe to bootstrap synchronously.
            if not registry.is_bootstrapped():
                try:
                    asyncio.run(registry.bootstrap())
                except Exception as exc:  # noqa: BLE001
                    log.exception("cli-loader: private bootstrap failed: %s", exc)
                    return []
            return list(registry.active_tools())

        # Async context, but no shared registry was published. We must not
        # block the running loop with ``asyncio.run``; return what we have.
        if not registry.is_bootstrapped():
            log.warning(
                "cli-loader.expand(): async context, no shared registry, private "
                "registry not bootstrapped — returning empty tool list. The UI "
                "server normally publishes a shared registry; in a pure-headless "
                "async path call CliToolRegistry.bootstrap() before building the brain."
            )
            return []
        return list(registry.active_tools())

    def registry(self) -> CliToolRegistry:
        """Return the registry backing this loader (private fallback).

        Prefers the shared registry when one is published so callers that reach
        for ``loader.registry()`` observe the same instance the brain expands
        from. Falls back to the private registry only in headless mode.
        """
        shared = self._resolve_shared_registry()
        if shared is not None:
            return shared
        return self._ensure_private_registry()

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        return ToolResult(
            success=False,
            output=None,
            error=(
                "CliToolLoader is a virtual loader and should be expanded by "
                "the brain launcher, not executed."
            ),
        )


__all__ = ["CliToolLoader"]
