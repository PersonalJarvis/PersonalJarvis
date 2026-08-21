"""The WebGL-context gate must hold: no 3D surface keeps a context forever.

A browser gives a page ~16 live WebGL contexts and takes the OLDEST away to
make room for a seventeenth. Since `WebGLRenderer.dispose()` does not return
one, a scene that mounts without releasing spends a slot per mount — and the
scene that dies is not the greedy one, it is the longest-running one on the
page. That is how the deck's memory map turned into the browser's own
broken-canvas placeholder on 2026-08-21 (AP-32).

These tests protect the mechanism, not one component: the gate has to stay
green on the shipped tree AND still catch a fresh offender.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_GATE = _REPO / "scripts" / "ci" / "check_webgl_contexts_released.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("check_webgl_contexts_released", _GATE)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_every_shipped_3d_surface_releases_its_context():
    gate = _load_gate()
    bad = gate.offenders()
    assert bad == [], (
        "these modules mount a WebGL renderer and never hand the context back: "
        f"{bad}. Use useWebglSurface(hostRef) and key the renderer on its generation."
    )


def test_the_gate_catches_a_scene_that_never_releases(tmp_path: Path):
    """A new 3D view added without the hook must fail, or the gate is decoration."""
    gate = _load_gate()
    offender = tmp_path / "NewSpaceView.tsx"
    offender.write_text(
        'import ForceGraph3D from "react-force-graph-3d";\n'
        "export const NewSpaceView = () => <ForceGraph3D graphData={{nodes: [], links: []}} />;\n",
        encoding="utf-8",
    )

    found = gate.offenders(tmp_path)

    assert [name for name, _ in found], "the gate did not notice an unreleased context"
    assert found[0][1] == "react-force-graph-3d"


def test_a_type_only_import_is_not_a_mounted_scene(tmp_path: Path):
    """The deck card names the library's types without ever owning a context."""
    gate = _load_gate()
    (tmp_path / "TypesOnly.tsx").write_text(
        'import type { NodeObject } from "react-force-graph-3d";\n'
        "export type N = NodeObject;\n",
        encoding="utf-8",
    )

    assert gate.offenders(tmp_path) == []


def test_releasing_by_hand_also_passes(tmp_path: Path):
    """The capability probe returns its context directly; that counts."""
    gate = _load_gate()
    (tmp_path / "probe.ts").write_text(
        'const gl = canvas.getContext("webgl2");\n'
        'gl?.getExtension("WEBGL_lose_context")?.loseContext();\n',
        encoding="utf-8",
    )

    assert gate.offenders(tmp_path) == []
