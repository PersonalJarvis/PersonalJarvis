"""The figure gate must hold: every shipped society figure honours the contract,
and the gate still catches a figure that breaks it.

A character that faces -Z walks backwards on the island; a clip with root
motion slides its feet; a mirrored node shades inside out. Nothing but the
gate reads the binary, so these tests protect the gate's teeth, not one asset
(docs/agent-society/character-pipeline.md §10).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_GATE = _REPO / "scripts" / "ci" / "check_society_figures.py"
_TOOLS = _REPO / "scripts" / "figures" / "glb_tools.py"
_FIGURES = _REPO / "jarvis" / "ui" / "web" / "frontend" / "src" / "assets" / "society" / "figures"


def _load(path: Path, name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = mod  # dataclasses need the module registered before exec
    spec.loader.exec_module(mod)
    return mod


def _shipped() -> list[Path]:
    return sorted(_FIGURES.rglob("*.glb")) if _FIGURES.exists() else []


def test_every_shipped_figure_honours_the_contract():
    gate = _load(_GATE, "check_society_figures")
    results = gate.check_dir(_FIGURES) if _FIGURES.exists() else {}
    failed = {k: v for k, v in results.items() if v}
    assert failed == {}, f"figures violating the contract: {json.dumps(failed, indent=2)}"


def test_png_codec_round_trips():
    gt = _load(_TOOLS, "glb_tools")
    rows = [
        bytes(b for x in range(16) for b in ((x * 7) & 0xFF, (y * 13) & 0xFF, 200, 255))
        for y in range(4)
    ]
    data = gt.encode_png(16, 4, rows, 4)
    w, h, channels, decoded = gt.decode_png(data)
    assert (w, h, channels) == (16, 4, 4)
    assert decoded == rows


@pytest.mark.skipif(not _shipped(), reason="no figure asset built yet")
@pytest.mark.parametrize(
    "mutate, expected",
    [
        ("turn_around", "faces the wrong way"),
        ("mirror", "negative/zero scale"),
        ("drop_walk", "clip 'walk' missing"),
        ("root_motion", "root motion"),
        ("blur", "NEAREST"),
    ],
)
def test_the_gate_catches_a_broken_figure(tmp_path: Path, mutate: str, expected: str):
    gate = _load(_GATE, "check_society_figures")
    gt = _load(_TOOLS, "glb_tools")
    source = _shipped()[0]
    glb = gt.read_glb(source)
    doc = glb.doc
    names = gt.node_index_by_name(doc)
    if mutate == "turn_around":
        doc["nodes"][names["FWD"]]["translation"] = [0.0, 0.5, -1.0]
    elif mutate == "mirror":
        doc["nodes"][names["hips"]]["scale"] = [-1.0, 1.0, 1.0]
    elif mutate == "drop_walk":
        doc["animations"] = [a for a in doc["animations"] if a.get("name") != "walk"]
    elif mutate == "root_motion":
        walk = gt.clip_by_name(doc, "walk")
        root = names["root"]
        # Point a thigh's rotation output at the root's translation: a walking
        # thigh swings, so the root now drifts in XZ (any spread > 1e-3 counts).
        thigh = names["upper_leg_l"]
        for ch in walk["channels"]:
            if ch["target"] == {"node": thigh, "path": "rotation"}:
                ch["target"] = {"node": root, "path": "translation"}
                break
    elif mutate == "blur":
        for sampler in doc["samplers"]:
            sampler["magFilter"] = 9729
    broken = tmp_path / source.name
    gt.write_glb(broken, doc, glb.blob)
    (tmp_path / "SOURCES.md").write_text(
        f"| `{source.name}` | test | CC0 | - | mutated |\n", encoding="utf-8"
    )
    problems = gate.check_file(broken, sources_md=tmp_path / "SOURCES.md")
    assert any(expected in p for p in problems), f"gate missed {mutate!r}: {problems}"


def test_main_reports_skipped_when_nothing_is_shipped(tmp_path: Path):
    gate = _load(_GATE, "check_society_figures")
    assert gate.main([str(tmp_path)]) == gate.EXIT_SKIPPED
