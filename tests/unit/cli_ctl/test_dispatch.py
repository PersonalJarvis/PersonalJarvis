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


def test_no_reserved_name_collides_with_launcher():
    options, positionals = _launcher_tokens()
    # A launcher subcommand (e.g. `serve`) must never be hijacked by dispatch.
    assert RESERVED_CONTROL_NAMES.isdisjoint(positionals)
    # Control-global options must not collide with launcher flags.
    assert CONTROL_GLOBAL_OPTIONS.isdisjoint(options)
    # Reserved names are bare words, never flag-like.
    assert all(not n.startswith("-") for n in RESERVED_CONTROL_NAMES)


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
    monkeypatch.setattr(jm, "_cmd_check", lambda: 0)
    rc = jm.main(["--check"])
    assert rc == 0


def test_main_routes_frozen_worker_broker_mode(monkeypatch):
    from jarvis.missions.workers import broker_stdio

    monkeypatch.setattr(broker_stdio, "main", lambda: 17)

    assert jm.main(["--worker-tool-broker-stdio"]) == 17
