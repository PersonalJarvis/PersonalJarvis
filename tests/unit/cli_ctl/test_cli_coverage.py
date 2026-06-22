"""The CLI coverage gate must hold: every REST route module is mounted, so every
WebUI action is reachable from the dynamic `jarvis api ...` layer."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_GATE = _REPO / "scripts" / "ci" / "check_cli_coverage.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("check_cli_coverage", _GATE)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_all_route_modules_are_mounted():
    gate = _load_gate()
    missing = gate.unmounted_modules()
    assert missing == [], f"unmounted route modules (dead CLI commands): {missing}"


def test_gate_discovers_route_modules():
    gate = _load_gate()
    mods = gate.route_modules()
    assert len(mods) > 15, f"expected many route modules, found {mods}"
    # frontier_routes was the route module we had to mount; it must be tracked.
    assert "frontier_routes" in mods
