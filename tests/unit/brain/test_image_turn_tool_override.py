"""Captured-screen turn: the tool surface is narrowed, never emptied.

Historically hard ``{}`` (docs/screen-context.md §3.1a, maintainer mandate
2026-08-02 "looking is not operating"), then widened once for a mandated WRITE
tool (code-review finding 2026-08-08).

2026-08-17 review: an emptied surface left a turn that MIXES a look with an
action ("Schau mal hier. Mach das Fenster zu.") describing a picture with no way
to do the thing that was asked — ``intent.requests_screen_operation`` anchors its
action verbs at the clause start, so a separable German verb slips through and
the turn still classifies SCREEN. ``computer_use`` therefore stays in the
surface; the look/operate boundary is enforced per call by
``cu_gate.llm_computer_use_allowed``, which reads the USER's utterance and so
refuses a pure look request anyway (pinned in
``test_screen_turn_computer_use_boundary`` below).
"""  # i18n-allow: verbatim quote of the German utterance

from __future__ import annotations

from jarvis.brain.manager import BrainManager


def _mgr(tools: dict, *, required: str = "", is_write: bool = False) -> BrainManager:
    m = BrainManager.__new__(BrainManager)  # bypass heavy __init__
    m._tools = tools
    m._evidence_required_tool = required
    m._evidence_required_is_write = is_write
    return m


def _surface() -> dict:
    return {
        "screenshot": object(),       # read — must stay
        "search_web": object(),       # read — must stay
        "computer_use": object(),     # the action vehicle a screen turn may need
        "run_shell": object(),        # write mandate target (2026-08-08)
        "spawn_worker": object(),     # unattended background agent — hidden
        "multi_spawn": object(),      # ditto
        "contact-upsert": object(),   # silent record write — hidden
        "wiki-ingest": object(),      # ditto
        "google_calendar": object(),  # ditto
        "call-contact": object(),     # ditto
        "update_profile": object(),   # ditto
    }


def test_screen_turn_keeps_computer_use_and_the_read_tools() -> None:
    out = _mgr(_surface())._image_turn_tool_override()
    for kept in ("screenshot", "search_web", "computer_use", "run_shell"):
        assert kept in out, kept


def test_screen_turn_hides_the_unattended_vehicles() -> None:
    # Screen pixels are untrusted evidence (docs/screen-context.md §4.2): text
    # rendered on screen must not be able to start a background agent or write a
    # record behind the user's back.
    out = _mgr(_surface())._image_turn_tool_override()
    for hidden in (
        "spawn_worker", "multi_spawn", "contact-upsert", "wiki-ingest",
        "google_calendar", "call-contact", "update_profile",
    ):
        assert hidden not in out, hidden


def test_screen_turn_never_empties_the_surface() -> None:
    m = _mgr({"screenshot": object(), "computer_use": object()})
    assert m._image_turn_tool_override() != {}


def test_screen_turn_keeps_the_mandated_write_tool() -> None:
    # "erstell einen Ordner hier auf dem Desktop" (2026-08-08): the local-outcome
    # mandate needs run_shell, and it must survive the screen-turn hide.
    shell = object()
    m = _mgr({"run_shell": shell, "spawn_worker": object()}, required="run_shell", is_write=True)
    out = m._image_turn_tool_override()
    assert out["run_shell"] is shell
    assert "spawn_worker" not in out


def test_screen_turn_mandate_exempts_a_hidden_tool() -> None:
    # A mandated record write (say-do via resolve_save_mandate) outranks the
    # hide, exactly as it does in the signalless gate (AD-CLI8).
    ingest = object()
    m = _mgr({"wiki-ingest": ingest, "contact-upsert": object()}, required="wiki-ingest")
    out = m._image_turn_tool_override()
    assert out["wiki-ingest"] is ingest
    assert "contact-upsert" not in out


def test_screen_turn_computer_use_boundary_still_holds() -> None:
    """The 2026-08-02 mandate survives the widened surface.

    computer_use being VISIBLE is not computer_use being ALLOWED: the execution
    gate refuses a pure look request and permits the mixed look-plus-action turn
    the empty surface used to break.
    """
    from jarvis.brain.cu_gate import llm_computer_use_allowed
    from jarvis.screen_context.intent import classify
    from jarvis.screen_context.models import VisualIntent

    # A pure look request captures, and computer_use is refused for it even
    # though the tool now sits in the surface. The mandate's outcome holds.
    for look in ("Was siehst du auf meinem Bildschirm?", "Lies mir das mal vor"):
        assert not llm_computer_use_allowed(look), look

    # The turn the empty surface used to break: a look glued to an action whose
    # verb ``requests_screen_operation`` misses (German separable "mach … zu"),
    # so it still classifies SCREEN and still captures — but the action is real
    # and the vehicle must be both visible AND allowed.
    mixed = "Schau mal hier. Mach das Fenster zu."
    assert classify(mixed).intent is VisualIntent.SCREEN
    assert llm_computer_use_allowed(mixed)
    assert "computer_use" in _mgr(_surface())._image_turn_tool_override()
