"""Assistant modes: a shelf of named characters, one of them active.

Feature 2026-08-13. These lock the four contracts everything else depends on:

1. The default mode changes NOTHING — an install that never opens the modes
   screen produces the same system prompt as before the feature existed.
2. A mode is layered on the base persona, never substituted for it, so the
   honesty rules survive picking a chattier character.
3. A slug becomes a file name, and it can arrive from a tool the realtime model
   calls — so anything path-shaped is refused, not quietly repaired.
4. The screen-scoped override is in-memory only. That is the structural reason
   coding mode can no longer get permanently stuck (the pre-2026-08-13 bug:
   ``AgenticIdeView`` switched it on and nothing ever switched it off).
"""
from __future__ import annotations

import pytest

import jarvis.core.config as core_config
from jarvis.brain import modes, persona_loader


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """User modes go to a throwaway dir; the active pointer never hits jarvis.toml."""
    monkeypatch.setattr(core_config, "DATA_DIR", tmp_path)
    stored: dict[str, str] = {}
    monkeypatch.setattr(modes, "_configured_slug", lambda: stored.get("slug", modes.DEFAULT_MODE))
    monkeypatch.setattr(
        "jarvis.core.config_writer.set_active_mode",
        lambda slug, **_kw: stored.__setitem__("slug", slug),
    )
    modes.set_section_override(None)
    yield tmp_path
    modes.set_section_override(None)


# ---------------------------------------------------------------------------
# The five built-ins
# ---------------------------------------------------------------------------


def test_all_five_builtins_ship_and_are_marked_built_in() -> None:
    by_slug = {m.slug: m for m in modes.list_modes()}
    assert set(modes.BUILTIN_SLUGS) <= by_slug.keys()
    assert all(by_slug[s].built_in for s in modes.BUILTIN_SLUGS)


def test_builtins_lead_the_list_in_declared_order() -> None:
    """The switcher shows a stable shelf, not whatever the filesystem returned."""
    listed = [m.slug for m in modes.list_modes()]
    assert listed[: len(modes.BUILTIN_SLUGS)] == list(modes.BUILTIN_SLUGS)


def test_every_builtin_carries_a_name_and_a_description() -> None:
    """The switcher renders these; a blank card is indistinguishable from a bug."""
    for mode in modes.list_modes():
        assert mode.name.strip(), f"{mode.slug} has no name"
        assert mode.description.strip(), f"{mode.slug} has no description"


# ---------------------------------------------------------------------------
# Contract 1: the default mode is a no-op
# ---------------------------------------------------------------------------


def test_default_mode_contributes_nothing() -> None:
    assert modes.active_slug() == modes.MODE_ASSISTANT
    assert modes.mode_prompt_block(modes.active_mode()) == ""


def test_effective_persona_is_byte_identical_to_the_base_by_default() -> None:
    """The whole feature must be invisible until the user chooses otherwise."""
    assert persona_loader.load_effective_persona_prompt() == persona_loader.base_persona_prompt()


# ---------------------------------------------------------------------------
# Contract 2: layered, never substituted
# ---------------------------------------------------------------------------


def test_active_mode_is_appended_to_the_base_persona() -> None:
    modes.set_active(modes.MODE_FRIEND)
    base = persona_loader.base_persona_prompt()
    effective = persona_loader.load_effective_persona_prompt()

    assert effective.startswith(base), "the base persona must survive a mode switch"
    assert len(effective) > len(base)
    assert "Friend" in effective


def test_mode_block_states_that_the_base_rules_win() -> None:
    """Without this, 'be casual' reads as permission to skip the honesty rules."""
    block = modes.mode_prompt_block(modes.get_mode(modes.MODE_FRIEND))
    assert "those rules win" in block
    assert "honesty" in block


def test_custom_system_prompt_and_a_mode_compose() -> None:
    """The legacy single-slot override is a BASE; a mode still layers on top."""
    persona_loader.save_custom_prompt("You are MAX.")
    modes.set_active(modes.MODE_COACH)
    effective = persona_loader.load_effective_persona_prompt()
    assert effective.startswith("You are MAX.")
    assert "Coach" in effective


def test_verbosity_and_proactivity_are_compiled_into_the_block() -> None:
    block = modes.mode_prompt_block(modes.get_mode(modes.MODE_FOCUS))
    assert "as few words" in block  # verbosity: brief
    assert "Answer what was asked and stop" in block  # proactivity: reactive


def test_normal_knobs_add_no_text() -> None:
    """'Normal' means 'say nothing extra', not 'say the word normal'."""
    mode = modes.Mode(slug="x", name="X", emoji="", description="", character="Be nice.")
    block = modes.mode_prompt_block(mode)
    assert block.rstrip().endswith("Be nice.")


# ---------------------------------------------------------------------------
# Contract 3: a slug is a file name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    ["../escape", "/etc/passwd", "..\\windows", "a:b", "..", "", "!!!", "con", "LPT1"],
)
def test_hostile_slugs_are_refused(hostile: str) -> None:
    with pytest.raises(modes.ModeError):
        modes.normalize_slug(hostile)


@pytest.mark.parametrize(
    ("typed", "expected"),
    [("Friend Mode", "friend-mode"), ("  My  Coach ", "my-coach"), ("a_b", "a-b")],
)
def test_human_names_become_readable_slugs(typed: str, expected: str) -> None:
    assert modes.normalize_slug(typed) == expected


def test_saved_mode_stays_inside_the_modes_directory(tmp_path) -> None:
    modes.save_mode(slug="Pirate", name="Pirate", character="Speak like a pirate.")
    written = list((tmp_path / "modes").glob("*.md"))
    assert [p.name for p in written] == ["pirate.md"]


def test_a_mode_needs_a_character() -> None:
    with pytest.raises(modes.ModeError):
        modes.save_mode(slug="empty", name="Empty", character="   ")


# ---------------------------------------------------------------------------
# Round trip, activation, deletion
# ---------------------------------------------------------------------------


def test_save_then_read_back_preserves_every_field() -> None:
    modes.save_mode(
        slug="pirate",
        name="Pirate",
        character="Speak like a pirate.",
        emoji="🏴",
        description="Arr.",
        voice="alloy",
        verbosity=modes.VERBOSITY_BRIEF,
        proactivity=modes.PROACTIVITY_FORWARD,
    )
    back = modes.get_mode("pirate")
    assert back is not None
    assert (back.name, back.emoji, back.description) == ("Pirate", "🏴", "Arr.")
    assert (back.voice, back.verbosity, back.proactivity) == (
        "alloy",
        modes.VERBOSITY_BRIEF,
        modes.PROACTIVITY_FORWARD,
    )
    assert back.character == "Speak like a pirate."
    assert back.built_in is False


def test_activating_an_unknown_mode_raises() -> None:
    with pytest.raises(modes.ModeError):
        modes.set_active("no-such-mode")


def test_builtins_cannot_be_deleted() -> None:
    with pytest.raises(modes.ModeError):
        modes.delete_mode(modes.MODE_FRIEND)


def test_deleting_the_active_mode_falls_back_to_the_default() -> None:
    modes.save_mode(slug="pirate", name="Pirate", character="Arr.")
    modes.set_active("pirate")
    assert modes.active_slug() == "pirate"
    modes.delete_mode("pirate")
    assert modes.active_slug() == modes.DEFAULT_MODE


def test_a_user_copy_shadows_a_builtin_but_stays_restorable() -> None:
    modes.save_mode(slug=modes.MODE_FRIEND, name="My Friend", character="Totally different.")
    edited = modes.get_mode(modes.MODE_FRIEND)
    assert edited is not None and edited.character == "Totally different."
    assert edited.built_in is True, "still offers 'restore the original'"

    assert modes.restore_builtin(modes.MODE_FRIEND) is True
    restored = modes.get_mode(modes.MODE_FRIEND)
    assert restored is not None and "not serving a client" in restored.character


def test_a_stored_pointer_to_a_vanished_mode_degrades_to_the_default() -> None:
    """A config carried to another machine must not name a mode that is not there."""
    modes.save_mode(slug="pirate", name="Pirate", character="Arr.")
    modes.set_active("pirate")
    (core_config.DATA_DIR / "modes" / "pirate.md").unlink()
    assert modes.active_slug() == modes.DEFAULT_MODE


def test_a_corrupt_mode_file_is_skipped_not_fatal(tmp_path) -> None:
    """One bad hand-edit must not take down the mode list, and through it every turn."""
    (tmp_path / "modes").mkdir(parents=True, exist_ok=True)
    (tmp_path / "modes" / "broken.md").write_text("---\nname: [unclosed\n", encoding="utf-8")
    slugs = [m.slug for m in modes.list_modes()]
    assert set(modes.BUILTIN_SLUGS) <= set(slugs)
    assert persona_loader.load_effective_persona_prompt()


# ---------------------------------------------------------------------------
# Contract 4: the screen-scoped override never persists
# ---------------------------------------------------------------------------


def test_section_override_wins_over_the_stored_choice() -> None:
    modes.set_active(modes.MODE_FRIEND)
    modes.set_section_override(modes.MODE_CODING)
    assert modes.active_slug() == modes.MODE_CODING


def test_clearing_the_override_restores_the_users_own_choice() -> None:
    """Leaving the Agentic IDE must not leave you in a mode you never picked."""
    modes.set_active(modes.MODE_FRIEND)
    modes.set_section_override(modes.MODE_CODING)
    modes.set_section_override(None)
    assert modes.active_slug() == modes.MODE_FRIEND


def test_override_is_never_written_to_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """The structural fix: a mode a screen turned on cannot outlive the process.

    Guards the exact regression path — if someone ever "helpfully" persists the
    section override, coding mode becomes sticky again and this test fails.
    """
    written: list[str] = []
    monkeypatch.setattr(
        "jarvis.core.config_writer.set_active_mode",
        lambda slug, **_kw: written.append(slug),
    )
    modes.set_section_override(modes.MODE_CODING)
    assert modes.section_override() == modes.MODE_CODING
    assert written == [], "the section override must never reach jarvis.toml"


def test_override_naming_an_unknown_mode_raises() -> None:
    with pytest.raises(modes.ModeError):
        modes.set_section_override("no-such-mode")
