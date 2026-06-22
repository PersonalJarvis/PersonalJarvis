"""Unit tests for the mutation safety gate (jarvis.cli_ctl.safety).

Model: reads and reversible mutations proceed without friction; only destructive
requests (DELETE + denylist, or an explicit dangerous=True) require --yes.
"""
from __future__ import annotations

import pytest
import typer

from jarvis.cli_ctl import safety


def test_is_mutating():
    assert not safety.is_mutating("GET")
    for m in ("POST", "PUT", "PATCH", "DELETE", "post"):
        assert safety.is_mutating(m)


def test_is_dangerous_heuristic():
    assert safety.is_dangerous("DELETE", "/api/tasks/1")
    assert safety.is_dangerous("POST", "/api/missions/x/dispatch")
    assert safety.is_dangerous("POST", "/api/settings/restart-app")
    assert safety.is_dangerous("POST", "/api/contacts/x/call")
    assert not safety.is_dangerous("POST", "/api/tasks")
    assert not safety.is_dangerous("GET", "/api/missions")


def test_get_never_gated():
    assert safety.gate_request("GET", "/api/missions") is True


def test_reversible_mutation_proceeds_without_yes():
    # A non-dangerous POST is agent-first: it just runs (server-audited).
    assert safety.gate_request("POST", "/api/tasks") is True


def test_dry_run_prints_and_blocks(capsys):
    assert safety.gate_request("POST", "/api/tasks", body={"a": 1}, dry_run=True) is False
    out = capsys.readouterr().out
    assert "dry_run" in out or "POST" in out


def test_dangerous_requires_yes():
    with pytest.raises(typer.Exit) as exc:
        safety.gate_request("DELETE", "/api/tasks/1")
    assert exc.value.exit_code == 1


def test_dangerous_with_yes_proceeds():
    assert safety.gate_request("DELETE", "/api/tasks/1", assume_yes=True) is True


def test_dangerous_env_yes(monkeypatch):
    monkeypatch.setenv("JARVIS_CLI_ASSUME_YES", "1")
    assert safety.gate_request("DELETE", "/api/tasks/1") is True


def test_explicit_dangerous_override_true():
    # A plain path forced dangerous (e.g. config set) needs --yes.
    with pytest.raises(typer.Exit):
        safety.gate_request("PUT", "/api/control/config", dangerous=True)
    assert safety.gate_request("PUT", "/api/control/config", dangerous=True, assume_yes=True) is True


def test_explicit_dangerous_override_false():
    # A heuristically-dangerous path forced safe (reversible) proceeds.
    assert safety.gate_request("POST", "/api/missions/x/dispatch", dangerous=False) is True
