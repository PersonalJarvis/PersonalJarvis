"""Screen-narration guard (live failure 2026-06-14).

A small-talk / knowledge conversation (Marie Curie, Nobel Prizes) was followed
by the cut-off fragment ``"Kannst du mir sagen, was genau..."``. The router
brain answered by *describing the screen* — "Auf Ihrem Bildschirm ist zu sehen,
dass die Spracherkennung ... abgebrochen ist ..." — although no screenshot was
ever attached or captured (the live log shows ``vision_none=True`` and zero
screenshot-tool executions). The narration was a pure fabrication.

Two defences, mapping to the two requirements:

1. Deterministic tool-gate (``BrainManager._gate_screen_tool``): the on-demand
   ``screenshot`` tool is only offered when the utterance actually refers to the
   screen (or an image is already attached, or it is a pointer turn). A plain
   conversational / cut-off fragment can no longer reach for — and then narrate
   — the screen. Reuses ``should_attach_screenshot`` so the marker-bearing
   screen questions of 2026-05-31 keep the tool.

2. Prompt honesty (router ``SYSTEM_PROMPT`` SCREEN-CONTEXT block): with no image
   attached the brain has NOT seen the screen and must never describe it; a
   vague / cut-off utterance gets one short clarifying question.
"""
from __future__ import annotations

from jarvis.brain.manager import BrainManager
from jarvis.brain.router import SYSTEM_PROMPT

# The exact transcript from the live failure.
_BUG_FRAGMENT = "Kannst du mir sagen, was genau..."


def _mgr(tools: dict) -> BrainManager:
    m = BrainManager.__new__(BrainManager)  # bypass heavy __init__
    m._tools = tools
    return m


def test_conversational_fragment_drops_screenshot_tool() -> None:
    m = _mgr({})
    gated = m._gate_screen_tool(
        {"screenshot": object(), "search_web": object()},
        user_text=_BUG_FRAGMENT,
        has_image=False,
        pointing_turn=False,
    )
    assert "screenshot" not in gated
    assert "search_web" in gated  # other tools are untouched


def test_screen_reference_keeps_screenshot_tool() -> None:
    m = _mgr({})
    gated = m._gate_screen_tool(
        {"screenshot": object()},
        user_text="was siehst du auf dem Bildschirm",
        has_image=False,
        pointing_turn=False,
    )
    assert "screenshot" in gated


def test_attached_image_keeps_screenshot_tool() -> None:
    m = _mgr({})
    gated = m._gate_screen_tool(
        {"screenshot": object()},
        user_text=_BUG_FRAGMENT,
        has_image=True,  # an image is already in context → screen is in scope
        pointing_turn=False,
    )
    assert "screenshot" in gated


def test_pointer_turn_keeps_screenshot_tool() -> None:
    m = _mgr({})
    # "worauf zeige ich" carries no visual marker, but a pointer turn is, by
    # definition, about the screen — the tool must stay.
    gated = m._gate_screen_tool(
        {"screenshot": object()},
        user_text="worauf zeige ich gerade",
        has_image=False,
        pointing_turn=True,
    )
    assert "screenshot" in gated


def test_marker_bearing_smalltalk_keeps_screenshot_through_both_stages() -> None:
    # Documented 2026-05-31 case: a greeting-prefixed but screen-referencing
    # utterance ("Hallo, lies mir vor was oben links steht") must still reach
    # the screenshot tool after BOTH the smalltalk override and the gate. The
    # markers "lies" / "oben links" / "steht" keep should_attach_screenshot True.
    m = _mgr({"screenshot": object(), "spawn_worker": object()})
    after_smalltalk = m._smalltalk_tool_override()  # keeps only {"screenshot"}
    assert set(after_smalltalk) == {"screenshot"}
    after_gate = m._gate_screen_tool(
        after_smalltalk,
        user_text="Hallo, lies mir vor was oben links steht",
        has_image=False,
        pointing_turn=False,
    )
    assert "screenshot" in after_gate


def test_plain_smalltalk_drops_screenshot_through_both_stages() -> None:
    # Plain chit-chat with no screen reference: the gate removes the otherwise
    # smalltalk-safe screenshot tool, so the brain cannot narrate the screen.
    m = _mgr({"screenshot": object(), "spawn_worker": object()})
    after_smalltalk = m._smalltalk_tool_override()
    after_gate = m._gate_screen_tool(
        after_smalltalk,
        user_text="danke dir",
        has_image=False,
        pointing_turn=False,
    )
    assert "screenshot" not in after_gate


def test_gate_is_noop_without_screenshot_tool() -> None:
    m = _mgr({})
    tools = {"search_web": object()}
    assert (
        m._gate_screen_tool(
            tools, user_text=_BUG_FRAGMENT, has_image=False, pointing_turn=False
        )
        == tools
    )


def test_router_prompt_forbids_screen_claims_without_image() -> None:
    # Honest no-image rule present.
    assert "hast du den Bildschirm NICHT gesehen" in SYSTEM_PROMPT
    assert "erfinden" in SYSTEM_PROMPT
    # Clarify-first for a vague / cut-off utterance.
    assert "vage, abgebrochen oder unklar" in SYSTEM_PROMPT
    # The fabrication-inviting instruction is gone.
    assert "liege kein Screenshot vor" not in SYSTEM_PROMPT
