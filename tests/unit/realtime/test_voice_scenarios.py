"""Every scripted spoken turn behaves — checked on every test run.

The scenarios live in ``tests/fixtures/voice_scenarios/scenarios.yaml`` as plain
data, so a new case is a few lines of YAML rather than a new test function. Each
one is driven through the REAL :class:`RealtimeVoiceSession`; no model runs, so
this is deterministic and costs nothing.

This is the guard that replaces phoning Jarvis to find out whether a turn
behaved (maintainer 2026-08-20: "das ganz manuelle Testen ist sehr nervig").
"""  # i18n-allow: quoted maintainer utterance

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fakes.voice_scenario import load_scenarios, run_scenario

SCENARIO_FILE = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "voice_scenarios"
    / "scenarios.yaml"
)

SCENARIOS = load_scenarios(SCENARIO_FILE)


def test_the_scenario_file_is_not_silently_empty() -> None:
    """A load fault must fail loudly, not present as "all green"."""
    assert len(SCENARIOS) >= 5


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
async def test_scenario_behaves(scenario) -> None:
    result = await run_scenario(scenario)
    problems = result.failures()
    assert not problems, (
        f"{scenario.name}: {'; '.join(problems)}\n"
        f"  spoken   : {result.spoken_text!r}\n"
        f"  continues: {result.asked_to_finish}\n"
        f"  tools    : {result.tool_calls}"
    )
