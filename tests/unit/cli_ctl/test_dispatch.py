"""Tests for the unified `jarvis` entry-point dispatch + reserved-name parity."""
from __future__ import annotations

import jarvis.__main__ as jm
from jarvis.cli_ctl.reserved import (
    CONTROL_GLOBAL_OPTIONS,
    RESERVED_CONTROL_NAMES,
    is_control_invocation,
)


def _launcher_tokens():
    parser = jm._build_parser()
    options: set[str] = set()
    positionals: set[str] = set()
    for action in parser._actions:
        options.update(action.option_strings)
        if action.choices:
            positionals.update(str(c) for c in action.choices)
    return options, positionals


# `--json` is deliberately shared: leading, it is the control CLI's global
# option (`jarvis --json missions list`); trailing a launcher flag, it switches
# the preflight to JSON Lines (`jarvis --check --json`). Dispatch separates the
# two by looking for a reserved COMMAND past the leading options, which is what
# test_json_is_shared_and_dispatch_separates_the_two_uses pins down. Every other
# control-global option must still be exclusive to the control CLI.
_SHARED_OPTIONS = frozenset({"--json"})


def test_no_reserved_name_collides_with_launcher():
    options, positionals = _launcher_tokens()
    # A launcher subcommand (e.g. `serve`) must never be hijacked by dispatch.
    assert RESERVED_CONTROL_NAMES.isdisjoint(positionals)
    # Control-global options must not collide with launcher flags, except for
    # the one that is shared on purpose.
    assert (CONTROL_GLOBAL_OPTIONS - _SHARED_OPTIONS).isdisjoint(options)
    # Reserved names are bare words, never flag-like.
    assert all(not n.startswith("-") for n in RESERVED_CONTROL_NAMES)
    # The shared option really is on both sides — if it ever leaves the
    # launcher, the carve-out above must go with it.
    assert _SHARED_OPTIONS <= options
    assert _SHARED_OPTIONS <= CONTROL_GLOBAL_OPTIONS


def test_json_is_shared_and_dispatch_separates_the_two_uses():
    # Control CLI: a reserved command follows the global options.
    assert is_control_invocation(["--json", "missions", "list"])
    assert is_control_invocation(["--json", "--url", "http://h:1", "brain", "list"])
    # Bare `jarvis --json` is the control CLI answering with its own usage,
    # never the app launching.
    assert is_control_invocation(["--json"])
    # Launcher: `--json` decorates a launcher flag, in either order of reading.
    assert not is_control_invocation(["--check", "--json"])
    assert not is_control_invocation(["--json", "--check"])
    assert not is_control_invocation(["--json", "--doctor"])
    assert not is_control_invocation(["--json", "serve"])


def test_launcher_invocations_not_routed():
    assert not is_control_invocation([])
    assert not is_control_invocation(["serve"])
    assert not is_control_invocation(["--wizard"])
    assert not is_control_invocation(["--check"])
    assert not is_control_invocation(["--debug"])
    assert not is_control_invocation(["--worker-tool-broker-stdio"])


def test_control_invocations_routed():
    assert is_control_invocation(["missions", "list"])
    assert is_control_invocation(["brain", "switch", "openai"])
    assert is_control_invocation(["config", "get", "brain.primary"])
    assert is_control_invocation(["--json", "missions", "list"])
    assert is_control_invocation(["--url", "http://h:1", "system", "status"])


def test_main_routes_control(monkeypatch):
    captured = {}

    def fake_run_control(argv):
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(jm, "_run_control", fake_run_control)
    rc = jm.main(["missions", "list"])
    assert rc == 0
    assert captured["argv"] == ["missions", "list"]


def test_main_does_not_route_launcher(monkeypatch):
    def boom(argv):
        raise AssertionError("launcher invocation must not route to control")

    monkeypatch.setattr(jm, "_run_control", boom)
    monkeypatch.setattr(jm, "_cmd_check", lambda as_json=False: 0)
    rc = jm.main(["--check"])
    assert rc == 0


def test_main_routes_frozen_worker_broker_mode(monkeypatch):
    from jarvis.missions.workers import broker_stdio

    monkeypatch.setattr(broker_stdio, "main", lambda: 17)

    assert jm.main(["--worker-tool-broker-stdio"]) == 17
