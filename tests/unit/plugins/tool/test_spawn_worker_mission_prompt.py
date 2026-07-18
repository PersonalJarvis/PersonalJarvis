"""Tests for the mission-prompt construction in spawn_worker.

Live incident 2026-05-29 (mission 019e70a9): the user's request reached the
worker as a VAD-cut fragment. STT captured only 'die Detailwürfelspiele.html'
(1.7s of speech); the brain still understood the intent and emitted
action='eine HTML-Seite namens Würfelspiel.html baut', but the mission prompt
used the verbatim `utterance` field alone, so the worker built from the
fragment. The tool schema deliberately keeps `utterance` verbatim (no detail
loss), so the fix enriches the mission prompt with the interpreted `action`
(+ target + context_hints) while preserving the verbatim words.
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.protocols import ExecutionContext
from jarvis.plugins.tool.spawn_worker import (
    SpawnWorkerTool,
    _build_mission_prompt,
)


class _RecordingManager:
    def __init__(self) -> None:
        self.dispatched: list[str] = []

    async def dispatch(self, *, prompt: str, language: str, source_actor: str) -> str:
        self.dispatched.append(prompt)
        return f"mission_{len(self.dispatched)}"


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid4(), user_utterance="", config={}, memory_read=None
    )


def test_fragment_utterance_is_enriched_with_action() -> None:
    """A fragment utterance + a rich interpreted action must yield a mission
    prompt that carries the action's intent (the real filename), not just the
    fragment."""
    prompt = _build_mission_prompt(
        utterance="die Detailwürfelspiele.html",
        action="eine HTML-Seite namens Würfelspiel.html baut",
        target="Desktop-App Outputs",
    )
    assert "Würfelspiel.html" in prompt, (
        f"interpreted filename missing from mission prompt: {prompt!r}"
    )
    # The verbatim words are preserved as context (schema's no-detail-loss rule).
    assert "die Detailwürfelspiele.html" in prompt
    # The target is carried too.
    assert "Desktop-App Outputs" in prompt


def test_context_hints_are_included() -> None:
    prompt = _build_mission_prompt(
        utterance="bau das",
        action="eine Flask-App baut",
        target="",
        context_hints=["Port 8000", "mit /health-Endpoint"],
    )
    assert "Flask-App" in prompt
    assert "Port 8000" in prompt
    assert "/health-Endpoint" in prompt


def test_no_action_falls_back_to_raw_utterance() -> None:
    """Force-spawn path passes action='' — there is no interpretation, so the
    mission prompt carries the raw utterance verbatim (no Aufgabe/action
    enrichment), led by the standing quality directive."""
    prompt = _build_mission_prompt(
        utterance="lies die Datei x und fasse sie zusammen", action=""
    )
    assert "lies die Datei x und fasse sie zusammen" in prompt
    assert "Aufgabe:" not in prompt  # no interpretation enrichment


def test_no_action_keeps_bounded_recent_context_for_references() -> None:
    """A forced follow-up must not reach a stateless worker without its topic."""
    long_hint = "bounded-marker " + ("x" * 1_000)
    prompt = _build_mission_prompt(
        utterance="Where is it? I also installed it as a plugin.",
        action="",
        context_hints=[
            "Conversation context (recent turns, newest last):",
            (
                "Earlier turn — User: 'Please inspect my Gmail plugin.' | "
                "Assistant: 'The lookup did not finish.'"
            ),
            long_hint,
        ],
    )

    assert "Please inspect my Gmail plugin" in prompt
    assert "The lookup did not finish" in prompt
    assert "Supporting context from the recent conversation" in prompt
    assert "bounded-marker" in prompt
    assert long_hint not in prompt


def test_empty_no_action_stays_empty_even_when_context_exists() -> None:
    assert _build_mission_prompt(
        utterance="", action="", context_hints=["old context"],
    ) == ""


def test_generic_default_action_is_not_treated_as_interpretation() -> None:
    """The generic ACK filler ('einer komplexen Aufgabe nachgeht') is NOT a
    real interpretation — it must not become the worker's task; fall back to
    the verbatim utterance (still led by the quality directive)."""
    prompt = _build_mission_prompt(
        utterance="mach das für mich",
        action="einer komplexen Aufgabe nachgeht",
    )
    assert "mach das für mich" in prompt
    assert "Aufgabe:" not in prompt
    assert "komplexen Aufgabe nachgeht" not in prompt


def test_empty_everything_returns_empty() -> None:
    assert _build_mission_prompt(utterance="", action="") == ""


def test_mission_prompt_carries_quality_directive() -> None:
    """Live incident 2026-05-31 (mission 019e7e04): the router's brief told the
    worker to build a 'Grundgerüst', the worker (Opus) obeyed and shipped a
    12-line stub, and the mission passed. Every dispatched mission prompt must
    lead with a standing quality directive so a lazy/minimal brief cannot
    downgrade the deliverable to a stub."""
    prompt = _build_mission_prompt(
        utterance="bau mir eine schöne Landingpage",
        action="eine Landingpage baut",
    )
    low = prompt.lower()
    assert "production-quality" in low or "complete" in low
    assert "skeleton" in low or "stub" in low or "placeholder" in low
    assert "failure" in low, "a stub must be named as a failure"
    # The actual task still survives.
    assert "Landingpage" in prompt


def test_quality_directive_present_on_forcespawn_path() -> None:
    """Force-spawn (action='') passes the raw utterance — it too must carry the
    quality directive, not just the enriched-action path."""
    prompt = _build_mission_prompt(
        utterance="lies die Datei x und fasse sie zusammen", action=""
    )
    low = prompt.lower()
    assert "skeleton" in low or "stub" in low or "placeholder" in low
    assert "lies die Datei x und fasse sie zusammen" in prompt


@pytest.mark.asyncio
async def test_execute_dispatches_enriched_prompt_not_raw_fragment() -> None:
    """End-to-end: execute() must dispatch the mission with the enriched
    prompt (carrying the interpreted filename), not the raw VAD fragment."""
    mgr = _RecordingManager()
    tool = SpawnWorkerTool(bus=EventBus(), manager=mgr)

    await tool.execute(
        {
            "utterance": "die Detailwürfelspiele.html",
            "action": "eine HTML-Seite namens Würfelspiel.html baut",
            "target": "Desktop-App Outputs",
        },
        _ctx(),
    )
    await asyncio.sleep(0.05)  # let the fire-and-forget bg task reach dispatch

    assert mgr.dispatched, "execute() must have dispatched a mission"
    prompt = mgr.dispatched[0]
    assert "Würfelspiel.html" in prompt, (
        f"dispatched prompt must carry the interpreted intent, got {prompt!r}"
    )


def test_forcespawn_strips_spawn_meta_from_worker_task() -> None:
    """Live regression (2026-06-16, "move to the USA" mission): an explicit
    voice trigger ("spawn a sub-agent which will help me find out X") routes to
    the force-spawn path (action=""), where the verbatim utterance became the
    worker's task. The worker then read "spawn a sub-agent" as ITS task — which
    it cannot do (no spawn tool, AP-5) — instead of doing the research, and the
    mission died critic_loop_exhausted. The routing wrapper must be stripped so
    the worker receives the REAL task."""
    prompt = _build_mission_prompt(
        utterance=(
            "Create and spawn a sub-agent which will help me find out what I "
            "have to be aware of when I move to the USA"
        ),
        action="",
    )
    low = prompt.lower()
    # the routing wrapper is gone — the worker is not told to spawn anything
    assert "spawn a sub-agent" not in low
    assert "create and spawn" not in low
    # the actual research task survives verbatim
    assert "move to the USA" in prompt
    # the standing quality directive is still present
    assert "production-quality" in low


def test_forcespawn_german_spawn_meta_stripped() -> None:
    """German is a first-class voice language here. An inflected "Sub-Agenten"
    must also be recognised as routing meta and stripped, leaving the real
    research task for the worker."""
    prompt = _build_mission_prompt(
        utterance=(
            "Spawne einen Sub-Agenten, der herausfindet, was ich beim "  # i18n-allow
            "USA-Umzug beachten muss"  # i18n-allow
        ),
        action="",
    )
    low = prompt.lower()
    assert "sub-agent" not in low
    assert "sub-agenten" not in low
    # the real task survives
    assert "USA-Umzug" in prompt


def test_forcespawn_topic_request_does_not_leave_an_orphaned_article() -> None:
    """Exact 2026-07-13 regression: routing removal stranded an article.

    The worker interpreted it as a missing deliverable noun and returned only a
    format question instead of researching the topic.
    """
    prompt = _build_mission_prompt(
        utterance=(
            "Kannst du bitte einen Subagent spawnen "  # i18n-allow: fixture
            "zum Thema Drogen in Schulen und was "  # i18n-allow: fixture
            "damit verbunden ist?"  # i18n-allow: fixture
        ),
        action="",
    )
    low = prompt.lower()

    assert "subagent spawnen" not in low
    assert "einen zum thema" not in low
    assert "research and analyze this topic thoroughly" in low
    assert "Drogen in Schulen" in prompt  # i18n-allow: quoted speech input
    assert "instead of asking which format" in low


def test_forcespawn_pure_meta_phrase_falls_back_to_raw() -> None:
    """If stripping leaves nothing real (the utterance was ONLY the routing
    wrapper), the worker must never get an empty task — fall back to the raw
    utterance so it still has something, and keep the quality directive."""
    prompt = _build_mission_prompt(utterance="spawn a sub-agent", action="")
    body = prompt.split("\n\n", 1)[-1].strip()
    assert body  # never empty
    assert "production-quality" in prompt.lower()


def test_forcespawn_genuine_deliverable_survives() -> None:
    """A genuine do-task wrapped in a spawn trigger must keep its deliverable:
    only the routing wrapper is stripped, not the real "writes a file" task."""
    prompt = _build_mission_prompt(
        utterance="spawn a sub-agent that writes a file poem.txt with a poem",
        action="",
    )
    low = prompt.lower()
    assert "spawn a sub-agent" not in low
    assert "poem.txt" in prompt
    assert "writes a file" in low


def test_forcespawn_no_meta_utterance_unchanged() -> None:
    """A force-spawn utterance WITHOUT any routing meta must be carried verbatim
    (the strip is a no-op) — existing force-spawn behaviour is preserved."""
    prompt = _build_mission_prompt(
        utterance="lies die Datei x und fasse sie zusammen", action=""
    )
    assert "lies die Datei x und fasse sie zusammen" in prompt
    assert "Aufgabe:" not in prompt


def test_quality_directive_has_honest_impossibility_escape() -> None:
    """Latency (2026-06-14): the standing quality directive must let the worker
    exit fast on a task it genuinely cannot do, instead of spiralling under the
    'never downgrade / build the finished artefact' floor (live mission 019ec708:
    'book a trip' ran 535s producing nothing). The floor governs the QUALITY of a
    doable task, never a mandate to fake an undoable one."""
    prompt = _build_mission_prompt(
        utterance="book me a trip from London to Taiwan",
        action="",
    )
    low = prompt.lower()
    assert "cannot be completed with the tools available" in low, prompt
    assert "do not simulate" in low or "do not simulate," in low or "not simulate" in low
    # The anti-stub floor must still be present (regression guard).
    assert "production-quality" in low


def test_quality_directive_respects_explicit_form_constraint() -> None:
    """Live incident 2026-06-22 (mission 019ef052): the user asked for a SINGLE
    HTML file, the worker shipped four (index.html + app.js + styles.css +
    assets/), and the mission passed. The 'never downgrade to a minimal version /
    skeleton is a floor not a ceiling' clause read a single self-contained file
    as a forbidden minimal version. The directive must carve out an explicit
    user constraint on the SHAPE/SCOPE of the deliverable: honoring it is part of
    satisfying the request, never a downgrade."""
    prompt = _build_mission_prompt(
        utterance="mach mir bitte eine einzige, in sich geschlossene HTML-Datei",
        action="",
    )
    low = prompt.lower()
    # The new carve-out names an explicit user constraint on form/scope.
    assert "constraint" in low, "the quality floor must defer to an explicit user constraint"
    # Regression: the anti-stub quality floor and impossibility escape survive.
    assert "production-quality" in low
    assert "skeleton" in low or "stub" in low or "placeholder" in low
    assert "cannot be completed with the tools available" in low
