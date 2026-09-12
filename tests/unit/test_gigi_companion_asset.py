"""Check the actual review export, not an invented substitute model."""

import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "art/studies/gigi-hover-companion"


def test_review_glb_has_editable_body_and_expression_nodes():
    raw = (STUDY / "exports/gigi.glb").read_bytes()
    assert raw[:4] == b"glTF"
    size = struct.unpack_from("<I", raw, 12)[0]
    data = json.loads(raw[20 : 20 + size])
    names = {node.get("name") for node in data["nodes"]}
    assert {
        "Gigi.Root",
        "Gigi.Housing",
        "Gigi.Face",
        "Gigi.Eye.L",
        "Gigi.Eye.R",
        "Gigi.Pupil.L",
        "Gigi.Pupil.R",
        "Gigi.Mouth",
        "Gigi.Arm.L",
        "Gigi.Arm.R",
        "Gigi.RearPanel",
        "Gigi.HoverEmitter",
        "Gigi.HemLight",
    } <= names
    assert not any("emblem" in name.lower() or "logo" in name.lower() for name in names)
    assert len(raw) <= 1_000_000
    triangles = sum(
        data["accessors"][primitive["indices"]]["count"] // 3
        for mesh in data["meshes"]
        for primitive in mesh["primitives"]
    )
    assert triangles <= 40_000
    assert len(data["meshes"]) <= 40
    assert not any("uri" in buffer for buffer in data["buffers"])
    assert {clip["name"] for clip in data["animations"]} >= {"Gigi.Blink.L", "Gigi.Blink.R"}
    # Blender 5 saves compressed sources; actual readability is checked by the
    # manifest export command, not guessed from an uncompressed file signature.
    assert (STUDY / "source/gigi.blend").stat().st_size > 1024
