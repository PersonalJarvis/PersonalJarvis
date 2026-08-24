"""The capability gate that decides which registry commands may be tools.

Pinned behaviour, in the order it matters:

1. a command bound to a switched-off mode is unavailable,
2. an unreadable config leaves it available (fail open),
3. the refusal always carries somewhere else to go.
"""
from __future__ import annotations

from types import SimpleNamespace

from jarvis.commands import capabilities
from jarvis.commands.registry import get_registry


def _cfg(*, ultrawiki: bool) -> SimpleNamespace:
    return SimpleNamespace(ultrawiki=SimpleNamespace(enabled=ultrawiki))


def test_mode_off_makes_the_capability_unavailable() -> None:
    assert capabilities.is_available("ultrawiki", config=_cfg(ultrawiki=False)) is False
    assert capabilities.is_available("ultrawiki", config=_cfg(ultrawiki=True)) is True


def test_a_command_without_a_requirement_is_always_available() -> None:
    plain = SimpleNamespace(id="brain-switch")
    assert capabilities.unavailable_capability(plain, config=_cfg(ultrawiki=False)) == ""
    assert capabilities.is_available("") is True


def test_unknown_capability_names_do_not_subtract() -> None:
    """The gate only ever removes for modes it knows how to check."""
    unknown = SimpleNamespace(id="x", requires="something-nobody-registered")
    assert capabilities.unavailable_capability(unknown, config=_cfg(ultrawiki=False)) == ""


def test_unreadable_config_fails_open(monkeypatch) -> None:
    """Losing a working tool to a failed probe is worse than a refusal.

    The execute-time re-check turns a wrong "available" into a redirect; a
    wrong "unavailable" silently removes the tool for the whole session.
    """
    monkeypatch.setattr(capabilities, "_live_config", lambda: None)
    assert capabilities.is_available("ultrawiki") is True


def test_the_steer_names_tools_that_actually_work() -> None:
    """A refusal the model cannot act on is how the turn died in the first place."""
    steer = capabilities.steer_for("ultrawiki")
    assert "wiki-recall" in steer
    # The classic tools are the point: they are always loaded and answer the
    # same questions from the Obsidian vault.
    assert "wiki-page-read" in steer
    assert "wiki-list" in steer


def test_every_declared_requirement_is_resolvable() -> None:
    """No command may demand a capability the gate cannot check or explain.

    Without this, a typo in `requires=` silently becomes "always available"
    and the tool goes back to failing live.
    """
    for cmd in get_registry():
        needed = getattr(cmd, "requires", "")
        if not needed:
            continue
        assert needed in capabilities._CONFIG_FLAGS, (
            f"{cmd.id} requires {needed!r}, which the gate cannot resolve"
        )
        assert needed in capabilities._STEERS, (
            f"{cmd.id} requires {needed!r}, which has no steer for the model"
        )


def test_the_ultrawiki_commands_are_the_ones_that_declare_it() -> None:
    """Every /api/ultrawiki route in the registry is gated — none was missed."""
    gated = {c.id for c in get_registry() if getattr(c, "requires", "") == "ultrawiki"}
    ultrawiki_routes = {
        c.id for c in get_registry() if c.path.startswith("/api/ultrawiki/")
    }
    assert gated == ultrawiki_routes
    assert "ultrawiki-ask" in gated
