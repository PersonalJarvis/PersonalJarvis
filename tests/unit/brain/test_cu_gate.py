"""Explicit-desktop gate for LLM-chosen computer_use calls (cu_gate.py).

Live incident 2026-07-21 11:36 (voice session 06a65611): a pure knowledge
question about the Gulfstream G100's runway requirement was delegated to the
router brain, which called computer_use — Safari opened on the user's screen
and googled the answer. The gate pins that a question-shaped turn without any
explicit on-screen vocabulary can never start a desktop mission, while every
explicit desktop ask (and the BUG-105 corrective follow-ups inside a recent
desktop episode) still passes.
"""
from __future__ import annotations

import pytest

from jarvis.brain.cu_gate import (
    CU_BLOCKED_MODEL_FEEDBACK,
    CU_VEHICLE_TOOL_NAMES,
    llm_computer_use_allowed,
)
from jarvis.harness import cu_run_registry


@pytest.fixture(autouse=True)
def _fresh_run_registry():
    cu_run_registry.clear_runs()
    yield
    cu_run_registry.clear_runs()


# ── live regression: knowledge questions must NEVER drive the desktop ─────


@pytest.mark.parametrize(
    "utterance",
    [
        # voice-session 2026-07-21 11:36 — googled in Safari before the gate.
        # Also pins that the German NOUN "Start- und Landebahn" (runway) never
        # counts as the action verb "start".
        "braucht die Golf braucht die Golf 100 Start- und Landebahn.",  # i18n-allow: live utterance
        "Kann eine Gulfstream 800 in St. Moritz landen?",  # i18n-allow: live utterance
        "Wie lang ist die Landebahn in St. Moritz?",  # i18n-allow: DE turn fixture
        "What runway length does a Gulfstream G100 need?",
        "What type of runway does it need?",
        "Was kostet eine Gulfstream 800?",  # i18n-allow: DE turn fixture
        # Search intent without a named vehicle belongs to search_web.
        "Such im Internet nach den aktuellsten News.",  # i18n-allow: DE turn fixture
        "Google mal, wie hoch der Bitcoin gerade steht.",  # i18n-allow: DE turn fixture
    ],
)
def test_knowledge_question_blocks_computer_use(utterance: str) -> None:
    assert llm_computer_use_allowed(utterance) is False


# ── explicit desktop asks keep passing ────────────────────────────────────


@pytest.mark.parametrize(
    "utterance",
    [
        "Öffne ein Terminal.",  # i18n-allow: DE trigger
        "Öffne Chrome und geh auf gmail.com.",  # i18n-allow: DE trigger
        "Mach Notepad auf.",  # i18n-allow: DE trigger
        "Klick den blauen Button.",  # i18n-allow: DE trigger
        "Scroll mal runter.",  # i18n-allow: DE trigger
        "Starte Spotify.",  # i18n-allow: DE trigger
        "Open the browser and search for Gulfstream G100 runway length.",
        "Click the settings icon.",
        "Type hello into the search field.",
        "Abre el navegador.",
        "Haz clic en el botón azul.",
        # Naming the vehicle makes even a web lookup a desktop task.
        "Google das mal im Browser.",  # i18n-allow: DE trigger
    ],
)
def test_explicit_desktop_ask_allows_computer_use(utterance: str) -> None:
    assert llm_computer_use_allowed(utterance) is True


# ── BUG-105 corrective follow-ups inside a desktop episode ────────────────


def test_vehicle_free_follow_up_passes_only_inside_a_recent_episode() -> None:
    follow_up = "Versuch es nochmal."  # i18n-allow: DE follow-up fixture
    assert llm_computer_use_allowed(follow_up) is False

    cu_run_registry.register_run("m1", "open the browser", token=None)
    assert llm_computer_use_allowed(follow_up) is True

    cu_run_registry.finish_run("m1", "finished", exit_code=0)
    assert llm_computer_use_allowed(follow_up) is True

    cu_run_registry.clear_runs()
    assert llm_computer_use_allowed(follow_up) is False


def test_recent_run_window_expires() -> None:
    cu_run_registry.register_run("m2", "open chrome", token=None)
    cu_run_registry.finish_run("m2", "finished", exit_code=0)
    assert cu_run_registry.has_recent_run(60.0) is True
    run = cu_run_registry._RUNS["m2"]
    run.ended_at = run.ended_at - 3600.0
    assert cu_run_registry.has_recent_run(60.0) is False


# ── plumbing contracts ────────────────────────────────────────────────────


def test_empty_turn_fails_open_for_non_conversational_routes() -> None:
    assert llm_computer_use_allowed("") is True
    assert llm_computer_use_allowed("   ") is True


def test_gate_covers_exactly_the_computer_use_tool() -> None:
    assert CU_VEHICLE_TOOL_NAMES == frozenset({"computer_use"})


def test_feedback_redirects_to_inline_answer_and_search_web() -> None:
    assert "search_web" in CU_BLOCKED_MODEL_FEEDBACK
    assert "NOT executed" in CU_BLOCKED_MODEL_FEEDBACK
