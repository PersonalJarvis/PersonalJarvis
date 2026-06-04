"""Unit tests for ``CliToolRegistry`` — usability gate, risk-pattern prefixing,
live-reload event emission, and tool add/remove on status refresh.

Uses injected fake catalog + fake prober (per repo convention: fakes, not mocks)
so no real CLI needs to be installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.clis.registry import CliToolRegistry
from jarvis.clis.spec import AuthConfig, CliSpec, CliStatus, InstallMethods, RiskConfig
from jarvis.clis.usage_log import UsageLog
from jarvis.core.bus import EventBus
from jarvis.core.events import BrainToolsChanged, CliStatusChanged


class _Recorder:
    """Async event recorder — bus handlers must be coroutine functions."""

    def __init__(self) -> None:
        self.events: list = []

    async def __call__(self, ev) -> None:
        self.events.append(ev)


class _FakeCatalog:
    def __init__(self, specs: dict[str, CliSpec]) -> None:
        self._specs = specs

    def all(self) -> dict[str, CliSpec]:
        return dict(self._specs)

    def get(self, name: str) -> CliSpec | None:
        return self._specs.get(name)


class _FakeProber:
    """Returns a scripted status per spec name; mutable between calls."""

    def __init__(self, statuses: dict[str, CliStatus]) -> None:
        self.statuses = statuses

    async def probe(self, spec: CliSpec) -> CliStatus:
        return self.statuses.get(spec.name, CliStatus())

    async def probe_all(self, specs: list[CliSpec]) -> dict[str, CliStatus]:
        return {s.name: await self.probe(s) for s in specs}


def _spec(
    name: str,
    *,
    auth_type: str = "oauth_cli",
    whitelist: tuple[str, ...] = (),
    blacklist: tuple[str, ...] = (),
) -> CliSpec:
    return CliSpec(
        name=name,
        display_name=name.upper(),
        description="d",
        homepage="",
        binary_name=name,
        check_command=(name, "--version"),
        version_parse_regex=r"(\d+)",
        install=InstallMethods(manual_url="https://x"),
        auth=AuthConfig(type=auth_type),  # type: ignore[arg-type]
        risk=RiskConfig(
            default_tier="monitor",
            whitelist_patterns=whitelist,
            blacklist_patterns=blacklist,
        ),
    )


def _registry(
    specs: dict[str, CliSpec],
    statuses: dict[str, CliStatus],
    *,
    bus=None,
    tmp_path: Path | None = None,
) -> CliToolRegistry:
    usage = UsageLog(db_path=(tmp_path / "u.db") if tmp_path else None)
    return CliToolRegistry(
        catalog=_FakeCatalog(specs),  # type: ignore[arg-type]
        prober=_FakeProber(statuses),  # type: ignore[arg-type]
        usage_log=usage,
        bus=bus,
    )


# --- _is_usable ----------------------------------------------------------


def test_is_usable_not_installed_is_false() -> None:
    spec = _spec("gh")
    assert CliToolRegistry._is_usable(spec, CliStatus(installed=False)) is False


def test_is_usable_none_auth_only_needs_installed() -> None:
    spec = _spec("docker", auth_type="none")
    status = CliStatus(installed=True, auth_status="unknown")
    assert CliToolRegistry._is_usable(spec, status) is True


def test_is_usable_config_file_auth_only_needs_installed() -> None:
    spec = _spec("kubectl", auth_type="config_file")
    status = CliStatus(installed=True, auth_status="not_connected")
    assert CliToolRegistry._is_usable(spec, status) is True


def test_is_usable_oauth_needs_connected() -> None:
    spec = _spec("gh", auth_type="oauth_cli")
    assert (
        CliToolRegistry._is_usable(spec, CliStatus(installed=True, auth_status="not_connected"))
        is False
    )
    assert (
        CliToolRegistry._is_usable(spec, CliStatus(installed=True, auth_status="connected")) is True
    )


# --- bootstrap + active_tools -------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_only_exposes_usable_clis(tmp_path: Path) -> None:
    specs = {
        "gh": _spec("gh", auth_type="oauth_cli"),
        "docker": _spec("docker", auth_type="none"),
        "az": _spec("az", auth_type="oauth_cli"),
    }
    statuses = {
        "gh": CliStatus(installed=True, auth_status="connected"),
        "docker": CliStatus(installed=True),
        "az": CliStatus(installed=True, auth_status="not_connected"),  # not usable
    }
    reg = _registry(specs, statuses, tmp_path=tmp_path)
    await reg.bootstrap()
    names = {t.name for t in reg.active_tools()}
    assert names == {"cli_gh", "cli_docker"}


@pytest.mark.asyncio
async def test_bootstrap_publishes_brain_tools_changed(tmp_path: Path) -> None:
    bus = EventBus()
    rec = _Recorder()
    bus.subscribe(BrainToolsChanged, rec)

    specs = {"docker": _spec("docker", auth_type="none")}
    statuses = {"docker": CliStatus(installed=True)}
    reg = _registry(specs, statuses, bus=bus, tmp_path=tmp_path)
    await reg.bootstrap()
    assert len(rec.events) == 1
    assert "cli_connected" in rec.events[0].reason


# --- risk_patterns prefixing --------------------------------------------


def test_risk_patterns_prefix_with_tool_name(tmp_path: Path) -> None:
    specs = {
        "gcloud": _spec(
            "gcloud",
            whitelist=("gcloud * list *",),
            blacklist=("gcloud * delete *",),
        ),
    }
    reg = _registry(specs, {}, tmp_path=tmp_path)
    whitelist, blacklist = reg.risk_patterns()
    assert "cli_gcloud gcloud * list *" in whitelist
    assert "cli_gcloud gcloud * delete *" in blacklist


def test_risk_patterns_includes_all_catalog_not_just_connected(tmp_path: Path) -> None:
    specs = {
        "gcloud": _spec("gcloud", whitelist=("gcloud * list *",)),
        "gh": _spec("gh", blacklist=("gh * delete *",)),
    }
    # No bootstrap -> nothing "connected", but patterns must still come out.
    reg = _registry(specs, {}, tmp_path=tmp_path)
    whitelist, blacklist = reg.risk_patterns()
    assert any("cli_gcloud" in p for p in whitelist)
    assert any("cli_gh" in p for p in blacklist)


# --- refresh_status: add/remove tool + events ---------------------------


@pytest.mark.asyncio
async def test_refresh_status_adds_tool_and_emits_events(tmp_path: Path) -> None:
    bus = EventBus()
    tools_changed = _Recorder()
    status_changed = _Recorder()
    bus.subscribe(BrainToolsChanged, tools_changed)
    bus.subscribe(CliStatusChanged, status_changed)

    specs = {"gh": _spec("gh", auth_type="oauth_cli")}
    statuses = {"gh": CliStatus(installed=True, auth_status="not_connected")}
    reg = _registry(specs, statuses, bus=bus, tmp_path=tmp_path)
    await reg.bootstrap()  # gh not connected -> no tool, no BrainToolsChanged

    assert reg.active_tools() == []
    assert tools_changed.events == []

    # Now gh connects.
    reg._prober.statuses["gh"] = CliStatus(installed=True, auth_status="connected")
    await reg.refresh_status("gh")

    assert {t.name for t in reg.active_tools()} == {"cli_gh"}
    assert len(tools_changed.events) == 1
    assert tools_changed.events[0].reason == "cli_connected:gh"
    assert len(status_changed.events) >= 1


@pytest.mark.asyncio
async def test_refresh_status_removes_tool_on_disconnect(tmp_path: Path) -> None:
    bus = EventBus()
    tools_changed = _Recorder()
    bus.subscribe(BrainToolsChanged, tools_changed)

    specs = {"gh": _spec("gh", auth_type="oauth_cli")}
    statuses = {"gh": CliStatus(installed=True, auth_status="connected")}
    reg = _registry(specs, statuses, bus=bus, tmp_path=tmp_path)
    await reg.bootstrap()
    assert {t.name for t in reg.active_tools()} == {"cli_gh"}
    tools_changed.events.clear()

    reg._prober.statuses["gh"] = CliStatus(installed=True, auth_status="not_connected")
    await reg.refresh_status("gh")

    assert reg.active_tools() == []
    assert len(tools_changed.events) == 1
    assert tools_changed.events[0].reason == "cli_disconnected:gh"


@pytest.mark.asyncio
async def test_refresh_status_no_event_when_tool_set_unchanged(tmp_path: Path) -> None:
    bus = EventBus()
    tools_changed = _Recorder()
    bus.subscribe(BrainToolsChanged, tools_changed)

    specs = {"docker": _spec("docker", auth_type="none")}
    statuses = {"docker": CliStatus(installed=True, version="1.0")}
    reg = _registry(specs, statuses, bus=bus, tmp_path=tmp_path)
    await reg.bootstrap()
    tools_changed.events.clear()

    # Same usability, same version -> no BrainToolsChanged.
    await reg.refresh_status("docker")
    assert tools_changed.events == []
