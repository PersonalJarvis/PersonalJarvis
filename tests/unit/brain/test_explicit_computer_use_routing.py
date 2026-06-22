"""Explicit "use Computer-Use" requests must reach the screenshot harness.

Live repro (user, 2026-06-21): "Kannst du für mich bitte mit Computer-Use in
Spotify das Lied Perfect von Ed Sheeran abspielen?" was only ACKNOWLEDGED
("Ich schau gerade in Spotify nach dem Lied …") — the Computer-Use action never
dispatched or executed.

Root cause: the deterministic ``match_local_action`` gate had no pattern for an
EXPLICITLY-named Computer-Use request that lacks an open verb. The phrase
returned ``None``, fell through to the LLM talker, and the talker only emitted a
free-text acknowledgement (the gate's own comments already warn "never depend on
the LLM talker calling computer_use" — ``local_action_gate.py``). The strongest
deterministic CU signal of all — the user NAMING the harness — must route to the
harness without depending on the LLM.

These tests pin BOTH halves with no LLM:
  1. routing — ``match_local_action`` returns a COMPUTER_USE plan for the repro
     phrase and other explicit-CU phrasings (de + en);
  2. dispatch — driving the real gate through
     ``BrainManager._run_local_action_fast_path`` actually CALLS the tool
     executor with the screenshot harness and announces a completion (genuinely
     dispatched + executed, not merely acknowledged).

Precision guards keep how-to / explain / negated mentions OFF the harness.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.brain.local_action_gate import (
    HARNESS_NAME,
    LocalActionMode,
    match_local_action,
)

# The exact live utterance that broke, plus adjacent explicit-CU phrasings that
# carry NO open verb (so the open-app fallback can never save them).
REPRO_PHRASE = (
    "Kannst du für mich bitte mit Computer-Use in Spotify das Lied Perfect "
    "von Ed Sheeran abspielen?"
)

_EXPLICIT_CU_UTTERANCES = [
    REPRO_PHRASE,
    "Spiel mit Computer-Use das nächste Lied",
    "Mach das per Computer-Use",
    "Erledige das über Computer-Use",
    "Nutze Computer-Use und like den obersten Post",
    "Benutze die Computer-Use Funktion dafür",
    "Verwende Computer-Use um durch mein Spotify zu blättern",
    "Use computer use to skip to the next track in Spotify",
    "Do it with computer use",
    "Using computer use, pause the music",
]


@pytest.mark.parametrize("utterance", _EXPLICIT_CU_UTTERANCES)
def test_explicit_computer_use_request_routes_to_harness(utterance: str) -> None:
    """An explicitly-named Computer-Use request routes DETERMINISTICALLY to the
    screenshot harness — never falls through to the (possibly tool-less) talker.

    ``_registry=None`` keeps the gate hermetic (no production capability-registry
    singleton touch). Before the fix the repro phrase returned ``None``.
    """
    plan = match_local_action(utterance, _registry=None)
    assert plan is not None, (
        f"{utterance!r} fell through to the brain — explicit Computer-Use "
        "request was not routed to the harness"
    )
    assert plan.mode is LocalActionMode.COMPUTER_USE, (
        f"{utterance!r} → {plan.mode}, want COMPUTER_USE"
    )
    assert plan.harness == HARNESS_NAME, f"{utterance!r} → harness {plan.harness!r}"
    # The full request (including the song / action) must reach the harness, not
    # a stripped fragment.
    assert plan.prompt == utterance.strip()


@pytest.mark.parametrize(
    "utterance",
    [
        # How-to / explain mentions are NOT a command to drive the screen.
        "Was ist Computer-Use?",
        "Wie funktioniert Computer-Use?",
        "Erklär mir Computer-Use",
        "What is computer use",
        # No harness mention at all — unaffected normal answers.
        "schreib mir ein Gedicht über den Herbst",
        "was ist die Hauptstadt von Frankreich",
    ],
)
def test_non_command_computer_use_mentions_do_not_route_to_harness(
    utterance: str,
) -> None:
    """Precision guard: a question/explanation that merely names Computer-Use, or
    a phrase that does not name it at all, must NOT be hijacked onto the harness.
    """
    plan = match_local_action(utterance, _registry=None)
    if plan is not None:
        assert plan.mode is not LocalActionMode.COMPUTER_USE, (
            f"{utterance!r} wrongly routed to COMPUTER_USE: {plan}"
        )


# ---------------------------------------------------------------------------
# End-to-end dispatch: driving the REAL gate through the fast path must actually
# call the tool executor with the screenshot harness AND announce a completion —
# proving the action is genuinely dispatched + executed, not merely acknowledged.
# Mirrors the harness in tests/unit/brain/test_computer_use_offload.py.
# ---------------------------------------------------------------------------


class _FakeBus:
    def __init__(self) -> None:
        self.published: list = []

    async def publish(self, event) -> None:  # noqa: ANN001
        self.published.append(event)


class _RecordingHarnessExecutor:
    """tool_executor stand-in that records the harness it was dispatched to."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, tool, args, *, user_utterance, trace_id):  # noqa: ANN001
        self.calls.append(dict(args))
        return SimpleNamespace(success=True, output="[cu] done (verified)", error=None)


def _make_manager(executor, bus):
    from jarvis.brain.manager import BrainManager

    mgr = BrainManager.__new__(BrainManager)
    mgr._config = SimpleNamespace(
        local_action=SimpleNamespace(
            enabled=True, harness_timeout_s=30.0, direct_timeout_s=3.0
        )
    )
    mgr._bus = bus
    mgr._tool_executor = executor
    mgr._local_action_tools = {"dispatch_to_harness": object()}
    mgr._cost_meter = None
    mgr._reply_language = "auto"
    return mgr


@pytest.mark.asyncio
async def test_repro_phrase_actually_dispatches_and_executes_harness(
    monkeypatch,
) -> None:
    """The live repro phrase, driven through the REAL gate + fast path, must
    actually dispatch and execute the screenshot harness and announce the
    completion — not merely return an acknowledgement.
    """
    from jarvis.brain.local_action_gate import (
        match_local_action as real_match_local_action,
    )

    # Use the REAL routing logic, pinned hermetic (no capability-registry singleton).
    monkeypatch.setattr(
        "jarvis.brain.manager.match_local_action",
        lambda text: real_match_local_action(text, _registry=None),
    )

    bus = _FakeBus()
    executor = _RecordingHarnessExecutor()
    mgr = _make_manager(executor, bus)

    ack = await mgr._run_local_action_fast_path(REPRO_PHRASE)

    # The turn ACKs immediately (not None — None would re-route to the talker).
    assert ack, "explicit Computer-Use request must yield an immediate dispatch ACK"

    # Drain the background mission spawned by the fast path.
    await asyncio.gather(*getattr(mgr, "_cu_background_tasks", set()))

    # GENUINELY dispatched + executed: the executor was called with the
    # screenshot harness and the user's request as the prompt.
    assert executor.calls, "the harness was never dispatched (acknowledged but never acted)"
    assert executor.calls[0]["harness"] == HARNESS_NAME, (
        f"dispatched to {executor.calls[0]['harness']!r}, want {HARNESS_NAME!r}"
    )
    assert "Perfect" in executor.calls[0]["prompt"], (
        "the user's request must reach the harness verbatim"
    )

    # The outcome is announced (AD-OE6 zero silent drops).
    completions = [
        e for e in bus.published if getattr(e, "kind", None) == "completion"
    ]
    assert completions, "the Computer-Use outcome must be announced as a completion"
