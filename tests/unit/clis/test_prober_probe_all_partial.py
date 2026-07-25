"""Regression tests: one wedged CLI probe must not blank out ALL the others.

Root cause this pins (observed 2026-07-25 09:06:10 in the desktop log):

    cli-registry: probe_all exceeded the 30s bootstrap ceiling — keeping
    22 CLI(s) at 'unknown' status instead of hanging boot.
    cli-registry: 0 von 22 CLIs als Tools exponiert

``bootstrap()`` wrapped ``probe_all`` in a single ``asyncio.wait_for``. When a
handful of Windows ``.cmd`` shims (firebase/wrangler/gcloud/twilio, plus the
vercel auth probe) burned their full per-probe budget, the whole gather was
cancelled — discarding the results of every CLI that had long since finished.
Every entry was then replaced by ``CliStatus(error=...)``, so the CLIs view
rendered 22 red "error" rows (0 connected, 0 installed) and the brain got
ZERO cli_* tools, for the rest of the session.

The ceiling was also structurally too tight to ever be a backstop: a single
legitimate probe may take CHECK_TIMEOUT_S + KILL_WAIT_TIMEOUT_S +
AUTH_TIMEOUT_S + KILL_WAIT_TIMEOUT_S == 29s, only 1s under the 30s ceiling —
so normal Windows shim behaviour, not a bug, tripped it.
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis.clis import prober as prober_mod
from jarvis.clis import registry as registry_mod
from jarvis.clis.prober import CliStatusProber
from jarvis.clis.spec import AuthConfig, CliSpec, CliStatus, InstallMethods, RiskConfig


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
        auth=AuthConfig(type="oauth_cli", status_command=(name, "auth", "status")),
        risk=RiskConfig(default_tier="monitor"),
    )


class _PartiallyWedgedProber(CliStatusProber):
    """Probes every spec instantly except the ones named in ``hanging``."""

    def __init__(self, hanging: set[str]) -> None:
        self._hanging = hanging

    async def probe(self, spec: CliSpec) -> CliStatus:
        if spec.name in self._hanging:
            await asyncio.sleep(3600)
        return CliStatus(
            installed=True,
            version="1.2.3",
            binary_path=f"/usr/bin/{spec.name}",
            auth_status="connected",
        )


@pytest.mark.asyncio
async def test_probe_all_keeps_finished_results_when_one_probe_wedges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single wedged probe must cost only ITS OWN row, not everyone else's."""
    monkeypatch.setattr(prober_mod, "PROBE_ALL_TIMEOUT_S", 0.05)

    specs = [_spec("gh"), _spec("docker"), _spec("gcloud")]
    prober = _PartiallyWedgedProber(hanging={"gcloud"})

    statuses = await asyncio.wait_for(prober.probe_all(specs), timeout=5.0)

    assert set(statuses) == {"gh", "docker", "gcloud"}
    # The healthy CLIs survive the wedge with their real, probed result.
    for name in ("gh", "docker"):
        assert statuses[name].installed is True, f"{name} lost its probe result"
        assert statuses[name].version == "1.2.3"
        assert statuses[name].auth_status == "connected"


@pytest.mark.asyncio
async def test_wedged_probe_is_unknown_not_an_error_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A probe that ran out of time is UNKNOWN, not a broken CLI.

    ``cli_routes._status_string`` maps any non-empty ``status.error`` to the
    red "error" label. A CLI we simply could not finish checking must not
    claim to be faulty — it stays unknown ("checking") so the recheck button
    and the next boot can still resolve it honestly.
    """
    monkeypatch.setattr(prober_mod, "PROBE_ALL_TIMEOUT_S", 0.05)

    prober = _PartiallyWedgedProber(hanging={"gcloud"})
    statuses = await asyncio.wait_for(prober.probe_all([_spec("gcloud")]), timeout=5.0)

    assert statuses["gcloud"].error is None
    assert statuses["gcloud"].installed is False


@pytest.mark.asyncio
async def test_probe_all_still_reports_per_probe_exceptions() -> None:
    """The pre-existing contract: a raising probe becomes an error row."""

    class _RaisingProber(CliStatusProber):
        async def probe(self, spec: CliSpec) -> CliStatus:
            raise RuntimeError("boom")

    statuses = await asyncio.wait_for(
        _RaisingProber().probe_all([_spec("gh")]), timeout=5.0
    )
    assert statuses["gh"].error is not None
    assert "boom" in statuses["gh"].error


@pytest.mark.asyncio
async def test_probe_all_handles_an_empty_catalog() -> None:
    """``asyncio.wait`` rejects an empty task set — guard that edge."""
    assert await CliStatusProber().probe_all([]) == {}


def test_bootstrap_ceiling_stays_above_the_probe_budget() -> None:
    """Structural guard: the backstop must never be tighter than normal work.

    The 30s ceiling sat 1s above a single legitimate worst-case probe, so
    routine Windows shim timeouts tripped it and blanked the whole catalog.
    Both bounds must stay ordered: one probe < the probe_all budget < the
    registry's defence-in-depth ceiling.
    """
    single_probe_worst_case = (
        prober_mod.CHECK_TIMEOUT_S
        + prober_mod.AUTH_TIMEOUT_S
        + 2 * prober_mod.KILL_WAIT_TIMEOUT_S
    )
    assert prober_mod.PROBE_ALL_TIMEOUT_S > single_probe_worst_case
    assert registry_mod._BOOTSTRAP_CEILING_S > prober_mod.PROBE_ALL_TIMEOUT_S
