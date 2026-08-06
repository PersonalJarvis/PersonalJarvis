"""Env-gated REAL ChatGPT-Live call through the production codex path.

Never runs in CI and never by accident: it needs the explicit
``JARVIS_CODEX_LIVE=1`` opt-in (each run bills the maintainer's ChatGPT
subscription realtime usage), a logged-in Codex voice profile, aiortc, and
the desktop app STOPPED (the profile lock refuses the probe otherwise —
that refusal is itself fail-closed behavior, reported as a skip here).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.e2e]

if os.environ.get("JARVIS_CODEX_LIVE") != "1":  # noqa: SIM108 - collection-time gate
    pytest.skip(
        "live codex probe needs the explicit JARVIS_CODEX_LIVE=1 opt-in "
        "(it bills the ChatGPT subscription)",
        allow_module_level=True,
    )

from jarvis.diagnostics.realtime_probe import (  # noqa: E402
    ProbeEnvironmentError,
    load_scenario,
    run_scenario,
)


async def test_three_turns_de_live(tmp_path: Path) -> None:
    try:
        summary = await run_scenario(
            load_scenario("three_turns_de"),
            out_dir=tmp_path / "three_turns_de",
        )
    except ProbeEnvironmentError as exc:
        pytest.skip(str(exc))
    failed = {
        name: entry
        for name, entry in summary["asserts"].items()
        if entry["status"] == "fail"
    }
    assert not failed, f"live scenario failed: {failed}"
