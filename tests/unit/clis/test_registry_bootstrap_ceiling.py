"""Regression test: ``CliToolRegistry.bootstrap()`` must never hang forever.

``CliStatusProber`` already bounds each individual probe (CHECK_TIMEOUT_S /
AUTH_TIMEOUT_S / KILL_WAIT_TIMEOUT_S). This is a defence-in-depth backstop
around the ``probe_all`` gather call site: if a prober implementation still
wedges (e.g. a future bug reintroduces an unbounded await), the outer
ceiling must still let ``bootstrap()`` return with an honest "unknown"
status instead of hanging the caller — notably the SYNCHRONOUS
``asyncio.run(registry.bootstrap())`` path in ``jarvis/clis/loader.py``,
which would otherwise wedge the whole process.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jarvis.clis import registry as registry_mod
from jarvis.clis.registry import CliToolRegistry
from jarvis.clis.spec import AuthConfig, CliSpec, CliStatus, InstallMethods, RiskConfig
from jarvis.clis.usage_log import UsageLog


class _FakeCatalog:
    def __init__(self, specs: dict[str, CliSpec]) -> None:
        self._specs = specs

    def all(self) -> dict[str, CliSpec]:
        return dict(self._specs)

    def get(self, name: str) -> CliSpec | None:
        return self._specs.get(name)


class _HangingProber:
    """Simulates a wedged prober whose ``probe_all`` never returns."""

    async def probe_all(self, specs: list[CliSpec]) -> dict[str, CliStatus]:
        await asyncio.sleep(3600)
        return {}  # pragma: no cover

    async def probe(self, spec: CliSpec) -> CliStatus:
        await asyncio.sleep(3600)
        return CliStatus()  # pragma: no cover


def _spec(name: str) -> CliSpec:
    return CliSpec(
        name=name,
        display_name=name.upper(),
        description="d",
        homepage="",
        binary_name=name,
        check_command=(name, "--version"),
        version_parse_regex=r"(\d+)",
        install=InstallMethods(manual_url="https://x"),
        auth=AuthConfig(type="none"),
        risk=RiskConfig(default_tier="monitor"),
    )


@pytest.mark.asyncio
async def test_bootstrap_returns_unknown_statuses_when_probe_all_hangs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(registry_mod, "_BOOTSTRAP_CEILING_S", 0.05)

    specs = {"gh": _spec("gh"), "docker": _spec("docker")}
    reg = CliToolRegistry(
        catalog=_FakeCatalog(specs),  # type: ignore[arg-type]
        prober=_HangingProber(),  # type: ignore[arg-type]
        usage_log=UsageLog(db_path=tmp_path / "u.db"),
    )

    # Outer safety net: if the ceiling regresses, this fails loudly instead
    # of hanging the whole test run forever.
    await asyncio.wait_for(reg.bootstrap(), timeout=2.0)

    statuses = reg.all_status()
    assert set(statuses) == {"gh", "docker"}
    for status in statuses.values():
        assert status.installed is False
        assert status.error == "probe_all timed out"
    # A timed-out bootstrap must not crash or expose any usable tool.
    assert reg.active_tools() == []
    assert reg.is_bootstrapped() is True
