"""A catalog sweep runs a few probes at a time, not the whole catalog at once.

Every probe is up to two child processes, and most of the catalog is a Node or
Python CLI that spends a second of CPU printing its version. All 22 at once on
a machine that had booted a minute earlier meant forty interpreter starts in a
second while the wake-word models were still loading — 14 probes were still
running at the sweep ceiling and the app stalled behind the load (2026-08-27,
BUG-189). ``PROBE_CONCURRENCY`` lanes turn that burst into a queue; the
partial-result contract of ``probe_all`` must survive the lanes unchanged.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis.clis import prober as prober_mod
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


class _CountingProber(CliStatusProber):
    """Reports how many probes overlapped in time."""

    def __init__(self, *, hanging: set[str] | None = None) -> None:
        self.in_flight = 0
        self.peak = 0
        self._hanging = hanging or set()

    async def probe(self, spec: CliSpec) -> CliStatus:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            if spec.name in self._hanging:
                await asyncio.sleep(3600)
            await asyncio.sleep(0.01)
            return CliStatus(
                installed=True,
                version="1.2.3",
                binary_path=f"/usr/bin/{spec.name}",
                auth_status="connected",
            )
        finally:
            self.in_flight -= 1


@pytest.mark.asyncio
async def test_a_sweep_never_exceeds_the_lane_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prober_mod, "PROBE_CONCURRENCY", 3)
    prober = _CountingProber()
    specs = [_spec(f"cli{i}") for i in range(10)]

    statuses = await asyncio.wait_for(prober.probe_all(specs), timeout=5.0)

    assert len(statuses) == 10
    assert all(status.installed for status in statuses.values()), "a probe lost its result"
    assert prober.peak <= 3, f"{prober.peak} probes ran at once"
    assert prober.peak >= 2, "the lanes were not used in parallel at all"


@pytest.mark.asyncio
async def test_a_probe_still_queued_at_the_ceiling_is_unknown_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Waiting for a lane that a wedged probe never gives back is not a fault."""
    monkeypatch.setattr(prober_mod, "PROBE_CONCURRENCY", 1)
    monkeypatch.setattr(prober_mod, "PROBE_ALL_TIMEOUT_S", 0.05)
    prober = _CountingProber(hanging={"gcloud"})

    statuses = await asyncio.wait_for(
        prober.probe_all([_spec("gcloud"), _spec("gh")]), timeout=5.0
    )

    assert set(statuses) == {"gcloud", "gh"}
    for name in ("gcloud", "gh"):
        assert statuses[name].error is None, f"{name} was reported broken, not unfinished"
        assert statuses[name].installed is False
