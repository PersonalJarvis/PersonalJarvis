"""Unit tests for ``CliToolLoader.expand()`` — the shared-registry resolution.

The production bug was a split-brain registry: the loader built its own private
registry while the UI server published a different, bootstrapped one. These tests
pin the fix: ``expand()`` resolves the shared registry first, and only falls back
to a private registry when none is published.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import jarvis.clis.shared as shared
from jarvis.clis.loader import CliToolLoader
from jarvis.clis.registry import CliToolRegistry
from jarvis.clis.spec import AuthConfig, CliSpec, CliStatus, InstallMethods, RiskConfig
from jarvis.clis.usage_log import UsageLog


@pytest.fixture(autouse=True)
def _reset_shared_registry():
    """Reset the module-global shared registry around each test."""
    previous = shared.get_active_registry()
    shared.set_active_registry(None)
    yield
    shared.set_active_registry(previous)


@pytest.fixture(autouse=True)
def _isolate_global_capability_registry():
    """Isolate the global CapabilityRegistry singleton around each test.

    The sync-context fallback test bootstraps a PRIVATE registry against the
    real seed catalog on the real machine — with AD-CLI3 that registers real
    ``cli.*`` capabilities into the global singleton, which would leak into
    later test files in the same process (observed: routing-heuristic tests
    changed verdicts once real CLI capabilities were visible)."""
    import jarvis.core.capabilities as cap_mod

    previous = cap_mod._registry_instance
    cap_mod._registry_instance = None
    yield
    cap_mod._registry_instance = previous


class _FakeCatalog:
    def __init__(self, specs: dict[str, CliSpec]) -> None:
        self._specs = specs

    def all(self) -> dict[str, CliSpec]:
        return dict(self._specs)

    def get(self, name: str) -> CliSpec | None:
        return self._specs.get(name)


class _FakeProber:
    def __init__(self, statuses: dict[str, CliStatus]) -> None:
        self.statuses = statuses

    async def probe(self, spec: CliSpec) -> CliStatus:
        return self.statuses.get(spec.name, CliStatus())

    async def probe_all(self, specs: list[CliSpec]) -> dict[str, CliStatus]:
        return {s.name: await self.probe(s) for s in specs}


def _spec(name: str, auth_type: str = "none") -> CliSpec:
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
        risk=RiskConfig(default_tier="monitor"),
    )


def _fake_registry(tmp_path: Path) -> CliToolRegistry:
    return CliToolRegistry(
        catalog=_FakeCatalog({"docker": _spec("docker")}),  # type: ignore[arg-type]
        prober=_FakeProber({"docker": CliStatus(installed=True)}),  # type: ignore[arg-type]
        usage_log=UsageLog(db_path=tmp_path / "u.db"),
    )


@pytest.mark.asyncio
async def test_expand_uses_shared_registry_when_present(tmp_path: Path) -> None:
    reg = _fake_registry(tmp_path)
    await reg.bootstrap()
    shared.set_active_registry(reg)

    loader = CliToolLoader()
    tools = loader.expand()
    assert {t.name for t in tools} == {"cli_docker"}
    # And loader.registry() returns the same shared instance.
    assert loader.registry() is reg


@pytest.mark.asyncio
async def test_expand_returns_empty_when_shared_not_yet_bootstrapped(tmp_path: Path) -> None:
    """Mirrors the server lifecycle: the registry is published before its
    background bootstrap finishes. expand() must return [] (not crash, not
    fall back to a private registry) so the live-reload bridge can re-expand."""
    reg = _fake_registry(tmp_path)
    shared.set_active_registry(reg)  # published but not bootstrapped

    loader = CliToolLoader()
    assert loader.expand() == []
    # The loader must NOT have built a private registry that hides the shared one.
    assert loader.registry() is reg


@pytest.mark.asyncio
async def test_expand_empty_when_no_shared_registry_in_async_context() -> None:
    """No shared registry + running loop -> empty list (cannot block the loop
    with a synchronous bootstrap). Headless sync callers get the private path."""
    loader = CliToolLoader()
    assert loader.expand() == []


def test_expand_falls_back_to_private_in_sync_context() -> None:
    """No shared registry + no running loop -> private registry is built and
    bootstrapped synchronously (headless jarvis-ask path)."""
    loader = CliToolLoader()
    tools = loader.expand()
    # Returns a list (whatever real CLIs are installed); must not crash.
    assert isinstance(tools, list)
    assert all(t.name.startswith("cli_") for t in tools)
