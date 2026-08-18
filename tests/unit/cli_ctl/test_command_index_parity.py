"""The static `jarvisctl` command index equals the live Typer app.

The brain's ``cli_jarvisctl`` tool shows ``jarvis.cli_ctl.command_index`` in
its description so the model calls commands directly instead of reading
``--help`` for several rounds. Static data can drift; this test walks the real
Typer app (the ~5 s import is fine in a test, not on the brain build path) and
fails on any group or command that disagrees in either direction.
"""
from __future__ import annotations

from jarvis.cli_ctl.command_index import (
    COMMAND_INDEX,
    TOP_LEVEL_COMMANDS,
    command_names,
    render_command_index,
)


def _live_tree() -> tuple[dict[str, set[str]], set[str]]:
    from jarvis.cli_ctl.__main__ import app

    groups: dict[str, set[str]] = {}
    top: set[str] = set()

    def _name(cmd) -> str:  # noqa: ANN001
        return cmd.name or cmd.callback.__name__.replace("_", "-")

    for cmd in app.registered_commands:
        top.add(_name(cmd))
    for group in app.registered_groups:
        names: set[str] = set()
        inst = group.typer_instance
        for cmd in inst.registered_commands:
            names.add(_name(cmd))
        # One level of nesting is enough for this CLI (config language get/set).
        for sub in inst.registered_groups:
            for cmd in sub.typer_instance.registered_commands:
                names.add(f"{sub.name} {_name(cmd)}")
        groups[group.name] = names
    return groups, top


def test_every_live_group_and_command_is_in_the_index() -> None:
    live, top = _live_tree()
    assert set(live) == set(COMMAND_INDEX), (
        f"groups differ — live-only: {set(live) - set(COMMAND_INDEX)}, "
        f"index-only: {set(COMMAND_INDEX) - set(live)}"
    )
    for group, names in live.items():
        indexed = set()
        for entry in COMMAND_INDEX[group]:
            # A nested command keeps its sub-group word ("language get").
            parts = entry.split(" ")
            if len(parts) > 1 and f"{parts[0]} {parts[1]}" in names:
                indexed.add(f"{parts[0]} {parts[1]}")
            else:
                indexed.add(parts[0])
        assert indexed == names, (
            f"{group}: live-only {names - indexed}, index-only {indexed - names}"
        )
    assert top == set(TOP_LEVEL_COMMANDS)


def test_render_is_compact_and_names_the_direct_call_rule() -> None:
    text = render_command_index()
    assert "skills:" in text and "enable <name>" in text
    assert "do not spend rounds on --help" in text
    assert len(text) < 3000, len(text)
    assert command_names("skills")[:2] == ("list", "show")
