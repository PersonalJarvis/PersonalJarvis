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


# ── tech proper names are not on-screen commands ──────────────────────────
#
# Live incident 2026-07-27 11:52 (voice session 57cc5f5f, flight recorder
# ActionProposed dispatch_to_harness/screenshot): a model-comparison question
# naming "Open AI Embedding 3 Large" matched the bare ``open\w*`` vehicle verb,
# the gate allowed, and computer_use screenshotted the desktop and started
# driving it until the user hit Escape. Same defect class the local-action gate
# fixed on 2026-07-10 for "OpenRouter" — it had never reached this gate.


@pytest.mark.parametrize(
    "utterance",
    [
        # The live utterance, verbatim from the flight recorder.
        "Hey, kannst du mal einen Vergleich machen von den "  # i18n-allow: live
        "beiden Modellen und zwar von den beiden Embedding "  # i18n-allow: live
        "Modellen? Einmal von dem Embedding Modell ähm aus "  # i18n-allow: live
        "Olama. Ich glaube, das nennt sich ähm BGE ähm "  # i18n-allow: live
        "{gedankenstrich} M3 und Gemini oder ähm Open AI "  # i18n-allow: live
        "Embedding 3 Large, was der Unterschied?",  # i18n-allow: live
        # "open" as a brand/license prefix — spaced and unspaced.
        "Was ist der Unterschied zwischen OpenAI Embedding 3 "  # i18n-allow: DE
        "Large und BGE-M3?",  # i18n-allow: DE fixture
        "Was kostet OpenRouter pro Million Token?",  # i18n-allow: DE fixture
        "Ist Open Source hier die bessere Wahl?",  # i18n-allow: DE fixture
        "Was hältst du von Open Weights Modellen?",  # i18n-allow: DE fixture
        "How does OpenCV compare to a vision model?",
        # "window" as a model term, never the desktop object.
        "Erklär mir das Context Window von Gemini 3 Pro",  # i18n-allow: DE
        "What is sliding window attention?",
        # "edge" as an engineering term, never the browser.
        "Ist das ein Edge Case oder ein echter Bug?",  # i18n-allow: DE fixture
        "Lohnt sich Edge Computing für uns?",  # i18n-allow: DE fixture
        "Is that library still cutting edge?",
    ],
)
def test_tech_proper_names_never_read_as_a_desktop_command(utterance: str) -> None:
    assert llm_computer_use_allowed(utterance) is False


@pytest.mark.parametrize(
    "utterance",
    [
        # Masking a product name must not disarm a real command in the SAME turn.
        "Mach mal ein Fenster auf und such nach Open AI Preisen.",  # i18n-allow: DE trigger
        "Open Chrome and check the OpenRouter status page.",
        # The vehicle nouns themselves stay vehicles.
        "Mach den Edge zu.",  # i18n-allow: DE trigger
        "Schließ das Fenster.",  # i18n-allow: DE trigger
        # Real English open-verb conjugations still count.
        "Opening Notepad, please.",
        "He opened the wrong tab, fix it.",
    ],
)
def test_product_name_masking_leaves_real_commands_intact(utterance: str) -> None:
    assert llm_computer_use_allowed(utterance) is True


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
        "Refresh this page.",
        "Reload the current tab.",
        "Aktualisiere diese Seite.",  # i18n-allow: DE trigger
        "Abre el navegador.",
        "Actualiza esta pÃ¡gina.",
        "Haz clic en el botón azul.",
        # Naming the vehicle makes even a web lookup a desktop task.
        "Google das mal im Browser.",  # i18n-allow: DE trigger
    ],
)
def test_explicit_desktop_ask_allows_computer_use(utterance: str) -> None:
    assert llm_computer_use_allowed(utterance) is True


# ── looking is not operating (maintainer mandate 2026-08-02, BUG-124) ─────
#
# The user asked "what is on my screen?" and Computer-Use started every time:
# the bare surface noun ("Bildschirm") was enough to pass the gate, so a
# question that Screen Context answers with ONE screenshot instead moved the
# mouse. These pin the split — surface nouns no longer authorize a mission on
# their own when the turn reads as a look request.


@pytest.mark.parametrize(
    "utterance",
    [
        # The reported utterance, and its nearest phrasings.
        "Hey, was ist da auf meinem Bildschirm?",  # i18n-allow: live utterance
        "Was siehst du auf meinem Bildschirm?",  # i18n-allow: DE turn fixture
        "Kannst du mal auf meinen Bildschirm schauen?",  # i18n-allow: DE fixture
        "Schau dir das mal an.",  # i18n-allow: DE turn fixture
        "Was steht da im Terminal?",  # i18n-allow: DE turn fixture
        "Lies mir das Fenster vor.",  # i18n-allow: DE turn fixture
        "What is on my screen right now?",
        "Can you see this error on my screen?",
        "Read this window to me.",
        "¿Qué ves en mi pantalla?",
        # A screenshot request names the feature outright — it is the least
        # ambiguous look request there is and must never reach the harness.
        "Mach mal einen Screenshot.",  # i18n-allow: DE turn fixture
        "Mach mir mal eben einen Screenshot davon.",  # i18n-allow: DE fixture
        "Take a quick screenshot for me.",
        "Hazme una captura de pantalla, por favor.",
    ],
)
def test_look_request_never_drives_the_desktop(utterance: str) -> None:
    assert llm_computer_use_allowed(utterance) is False


def test_look_request_stays_blocked_inside_a_live_desktop_episode() -> None:
    """A question mid-episode is still a question, not a corrective follow-up.

    The recent-run window exists so "try again" keeps working after a mission.
    It must not re-open the desktop for "what is on my screen?", or every
    conversation that follows a Computer-Use run inherits the original defect.
    """
    look = "Was ist da auf meinem Bildschirm?"  # i18n-allow: DE turn fixture
    cu_run_registry.register_run("m3", "open the browser", token=None)
    assert llm_computer_use_allowed(look) is False
    # The vehicle-free corrective follow-up is unaffected by the new rule.
    assert llm_computer_use_allowed("Versuch es nochmal.") is True  # i18n-allow


@pytest.mark.parametrize(
    "utterance",
    [
        # An action verb wins over the look vocabulary in the same sentence:
        # the user wants something DONE to what they are pointing at.
        "Klick auf den Button auf meinem Bildschirm.",  # i18n-allow: DE trigger
        "Schau in Chrome nach und klick auf Anmelden.",  # i18n-allow: DE trigger
        "Look at my screen and close that window.",
        # Naming the harness IS the explicit ask the mandate asks for.
        "Mach das per Computer-Use",
        "Use computer use to open Spotify",
    ],
)
def test_explicit_action_outranks_the_look_vocabulary(utterance: str) -> None:
    assert llm_computer_use_allowed(utterance) is True


def test_visual_intent_probe_failure_keeps_desktop_automation_working() -> None:
    """A classifier defect must degrade to the old behaviour, never brick CU."""
    import jarvis.screen_context.intent as intent_module

    def _boom(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("classifier exploded")

    original = intent_module.requests_screen_operation
    intent_module.requests_screen_operation = _boom  # type: ignore[assignment]
    try:
        # The probe is what is under test; the turn is only its input.
        look = "Was ist auf meinem Bildschirm?"  # i18n-allow: DE turn fixture
        assert llm_computer_use_allowed(look) is True
    finally:
        intent_module.requests_screen_operation = original  # type: ignore[assignment]


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
