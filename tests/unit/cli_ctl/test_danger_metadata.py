"""Danger-metadata contract: server-declared x-jarvis-dangerous drives the
CLI safety gate, and the CI gate's marker list never drifts from safety.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import click
from click.testing import CliRunner

from jarvis.cli_ctl import safety
from jarvis.cli_ctl.dynamic import build_api_group

_REPO = Path(__file__).resolve().parents[3]


def _load_gate_module():
    spec = importlib.util.spec_from_file_location(
        "check_danger_metadata", _REPO / "scripts" / "ci" / "check_danger_metadata.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_gate_markers_match_safety_denylist() -> None:
    """The CI script keeps a stdlib-only copy of the marker list; this is the
    parity test that copy relies on."""
    gate = _load_gate_module()
    assert tuple(gate.DANGEROUS_MARKERS) == tuple(safety._DANGEROUS_MARKERS)


def test_gate_passes_on_current_tree() -> None:
    gate = _load_gate_module()
    assert gate.unflagged_dangerous_routes() == []


def _spec_with_flag(flagged: bool) -> dict:
    op = {
        "tags": ["demo"],
        "operationId": "wipe_everything_api_demo_wipe_post",
        "summary": "Wipe",
    }
    if flagged:
        op["x-jarvis-dangerous"] = True
    return {
        "openapi": "3.1.0",
        "info": {"version": "1"},
        "paths": {"/api/demo/wipe": {"post": op}},
    }


def _invoke(spec: dict, args: list[str]):
    calls: list[tuple] = []

    def runner(method, path, params, body, *, timeout_s=None):
        calls.append((method, path))
        return {"ok": True}

    group = build_api_group(spec, runner)
    root = click.Group("jarvis")
    root.add_command(group)
    result = CliRunner().invoke(root, args)
    return result, calls


def test_flagged_op_requires_yes_even_without_marker_match(monkeypatch) -> None:
    """/api/demo/wipe matches NO legacy marker — only the x-jarvis-dangerous
    flag makes it destructive. Non-interactive without --yes must fail closed
    and send nothing."""
    monkeypatch.delenv("JARVIS_CLI_ASSUME_YES", raising=False)
    result, calls = _invoke(_spec_with_flag(True), ["api", "demo", "wipe-everything"])
    assert result.exit_code == 1
    assert calls == []

    result, calls = _invoke(
        _spec_with_flag(True), ["api", "demo", "wipe-everything", "--yes"]
    )
    assert result.exit_code == 0
    assert calls == [("post", "/api/demo/wipe")]


def test_unflagged_op_keeps_heuristic_behavior(monkeypatch) -> None:
    """Without the flag, a marker-free POST is a plain reversible mutation:
    it proceeds without --yes (agent-first), exactly as before."""
    monkeypatch.delenv("JARVIS_CLI_ASSUME_YES", raising=False)
    result, calls = _invoke(_spec_with_flag(False), ["api", "demo", "wipe-everything"])
    assert result.exit_code == 0
    assert calls == [("post", "/api/demo/wipe")]
