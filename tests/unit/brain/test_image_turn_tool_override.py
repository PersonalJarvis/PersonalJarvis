"""Image-turn tool surface: historically ``{}`` (pixels answer the turn), but
a mandated WRITE tool must survive it (code-review finding 2026-08-08).

"erstell einen Ordner hier auf dem Desktop" matches the screen-intent phrase
"hier auf dem", the explicit screen-context path attaches a screenshot, and
the old hard ``{}`` stripped the very ``run_shell`` call the local-outcome
mandate requires — the honest "never ran" fallback fired instead of the
action. Mirrors ``test_smalltalk_tool_visibility.py``.
"""

from __future__ import annotations

from jarvis.brain.manager import BrainManager


def _mgr(tools: dict, *, required: str = "", is_write: bool = False) -> BrainManager:
    m = BrainManager.__new__(BrainManager)  # bypass heavy __init__
    m._tools = tools
    m._evidence_required_tool = required
    m._evidence_required_is_write = is_write
    return m


def test_image_turn_hides_all_tools_without_mandate() -> None:
    m = _mgr({"run_shell": object(), "spawn_worker": object(), "screenshot": object()})
    assert m._image_turn_tool_override() == {}


def test_image_turn_keeps_only_the_mandated_write_tool() -> None:
    shell = object()
    m = _mgr(
        {"run_shell": shell, "spawn_worker": object(), "screenshot": object()},
        required="run_shell",
        is_write=True,
    )
    assert m._image_turn_tool_override() == {"run_shell": shell}


def test_image_turn_read_mandate_keeps_the_historical_hide() -> None:
    # A READ mandate answers from the mandated tool's data path elsewhere;
    # the image-turn hide only widens for WRITE mandates.
    m = _mgr(
        {"cli_gcloud": object(), "screenshot": object()},
        required="cli_gcloud",
        is_write=False,
    )
    assert m._image_turn_tool_override() == {}


def test_image_turn_mandated_tool_missing_degrades_to_empty() -> None:
    # Mandate for a tool that is not registered (deployment without run_shell)
    # must not crash and keeps the historical hide.
    m = _mgr({"screenshot": object()}, required="run_shell", is_write=True)
    assert m._image_turn_tool_override() == {}
