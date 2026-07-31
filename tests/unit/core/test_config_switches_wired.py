"""The dead-config-switch gate must hold: no NEW config field that nothing reads.

A switch the user can set but that no code reads is a lie told by the settings
screen and by jarvis.toml. The known backlog is frozen in a baseline file; these
tests protect the mechanism that stops it from growing.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_GATE = _REPO / "scripts" / "ci" / "check_config_switches_wired.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("check_config_switches_wired", _GATE)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_no_new_dead_switch_beyond_the_recorded_backlog():
    gate = _load_gate()
    fresh = sorted(set(gate.unread_fields()) - gate.load_baseline())
    assert fresh == [], (
        "config fields nothing reads, and not in the recorded backlog: "
        f"{fresh}. Wire them up or remove them."
    )


def test_gate_sees_the_real_config_surface():
    """Guard against the gate silently scanning nothing."""
    gate = _load_gate()
    fields = gate.config_fields()
    assert len(fields) > 300, f"expected the full config surface, found {len(fields)}"
    # Spot-check a field that is unambiguously read by shipped code, so a
    # regression in usage detection shows up here rather than as 400 findings.
    assert "provider" in fields


def test_bom_files_are_readable_by_the_gate():
    """The largest modules start with a BOM; plain utf-8 makes ast.parse fail.

    This is the bug that made an earlier version of this gate report 24 live
    switches as dead: it skipped exactly manager.py, desktop_app.py and
    server.py, which is where most config reads live.
    """
    gate = _load_gate()
    bom_files = [
        path
        for path in (_REPO / "jarvis").rglob("*.py")
        if "__pycache__" not in path.parts
        and path.read_bytes()[:3] == b"\xef\xbb\xbf"
    ]
    assert bom_files, "expected at least one BOM file to guard against"
    for path in bom_files:
        # Must not raise: the gate reads utf-8-sig, so the BOM is stripped.
        ast.parse(gate._read(path))


def test_baseline_has_no_stale_entries():
    """Every baselined field still exists; otherwise the backlog is fiction."""
    gate = _load_gate()
    fields = set(gate.config_fields())
    stale = sorted(gate.load_baseline() - fields)
    assert stale == [], (
        f"baseline lists fields that no longer exist: {stale}. "
        "Run: python scripts/ci/check_config_switches_wired.py --update"
    )
