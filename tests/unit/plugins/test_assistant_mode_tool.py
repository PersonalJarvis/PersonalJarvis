"""The three mode tools the assistant can call itself.

``switch_mode`` is how "be my friend for a bit" works by voice; ``save_mode``
is the last beat of the mode-builder interview. They live in the normal tool
registry rather than as bespoke realtime declarations, so the speaking brain
and the typing brain get the same behaviour from one file — these tests lock
that they are registered, gated at the right tiers, and honest when they fail.
"""
from __future__ import annotations

from importlib.metadata import entry_points

import pytest

import jarvis.core.config as core_config
from jarvis.brain import modes, persona_loader
from jarvis.plugins.tool.assistant_mode import ListModesTool, SaveModeTool, SwitchModeTool


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Never touch the real data dir or the real jarvis.toml."""
    monkeypatch.setattr(core_config, "DATA_DIR", tmp_path)
    stored: dict[str, str] = {}
    monkeypatch.setattr(modes, "_configured_slug", lambda: stored.get("slug", modes.DEFAULT_MODE))
    monkeypatch.setattr(
        "jarvis.core.config_writer.set_active_mode",
        lambda slug, **_kw: stored.__setitem__("slug", slug),
    )
    modes.set_section_override(None)
    yield
    modes.set_section_override(None)


def test_all_three_tools_are_registered_entry_points() -> None:
    """Unregistered, they exist but the model can never reach them."""
    names = {e.name for e in entry_points(group="jarvis.tool")}
    assert {"list-modes", "switch-mode", "save-mode"} <= names


def test_risk_tiers_match_what_each_tool_actually_does() -> None:
    assert ListModesTool.risk_tier == "safe"  # a property read
    assert SwitchModeTool.risk_tier == "monitor"  # visible, reversible in one sentence
    assert SaveModeTool.risk_tier == "ask"  # writes a file every future turn reads


async def test_list_modes_reports_the_shelf_and_the_active_one() -> None:
    result = await ListModesTool().execute({}, None)
    assert result.success
    assert [m["slug"] for m in result.output["modes"]][:5] == list(modes.BUILTIN_SLUGS)
    assert result.output["active"] == modes.DEFAULT_MODE


async def test_switching_reaches_the_persona_layer() -> None:
    """The point of the whole feature: one call changes what the brain is told."""
    base = persona_loader.base_persona_prompt()
    result = await SwitchModeTool().execute({"slug": "friend"}, None)
    assert result.success
    assert "Friend" in persona_loader.load_effective_persona_prompt()[len(base) :]


async def test_an_unknown_mode_comes_back_with_the_real_list() -> None:
    """A near-miss must be recoverable in the same turn, not an apology."""
    result = await SwitchModeTool().execute({"slug": "friendly"}, None)
    assert not result.success
    assert "friend" in result.error
    assert "coach" in result.error


async def test_switching_under_a_section_override_says_so() -> None:
    """Reporting a mode the user will not actually hear is the lie to avoid."""
    modes.set_section_override(modes.MODE_CODING)
    result = await SwitchModeTool().execute({"slug": "friend"}, None)
    assert result.success
    assert "coding" in result.output
    assert modes.active_slug() == modes.MODE_CODING


async def test_save_mode_writes_and_can_activate() -> None:
    result = await SaveModeTool().execute(
        {
            "name": "Night Owl",
            "character": "Speak quietly. It is late.",
            "emoji": "🦉",
            "verbosity": "brief",
            "activate": True,
        },
        None,
    )
    assert result.success
    saved = modes.get_mode("night-owl")
    assert saved is not None and saved.verbosity == modes.VERBOSITY_BRIEF
    assert modes.active_slug() == "night-owl"


async def test_save_mode_does_not_activate_unless_asked() -> None:
    await SaveModeTool().execute({"name": "Night Owl", "character": "Quietly."}, None)
    assert modes.active_slug() == modes.DEFAULT_MODE


async def test_save_mode_needs_both_a_name_and_a_character() -> None:
    result = await SaveModeTool().execute({"name": "Half", "character": "  "}, None)
    assert not result.success


async def test_save_mode_refuses_a_path_shaped_name() -> None:
    result = await SaveModeTool().execute({"name": "../escape", "character": "x"}, None)
    assert not result.success
