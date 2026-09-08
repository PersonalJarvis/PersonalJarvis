"""Behavioural tests for the future art workflow, not for a proposed visual style."""

from __future__ import annotations

import importlib.util
import json
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("art_pipeline", ROOT / "scripts/art_pipeline.py")
assert SPEC and SPEC.loader
pipeline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pipeline)


def save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def triangle() -> bytes:
    doc = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": 36}],
        "bufferViews": [{"buffer": 0, "byteLength": 36}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
    }
    text = json.dumps(doc).encode()
    text += b" " * (-len(text) % 4)
    binary = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    return (
        struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(text) + 8 + len(binary))
        + struct.pack("<II", len(text), 0x4E4F534A)
        + text
        + struct.pack("<II", len(binary), 0x004E4942)
        + binary
    )


def reference(tmp_path: Path) -> tuple[Path, dict]:
    path = pipeline.init_study("reference", tmp_path)
    data = json.loads(path.read_text())
    data["assets"] = [
        {
            "id": "sample",
            "kind": "prop",
            "source": "source/reference.blend",
            "collection": "Reference",
            "export": "exports/sample.glb",
        }
    ]
    data["runtime_evidence"] = ["evidence/runtime.png"]
    data["technical_report"] = "evidence/checks.md"
    (path.parent / "source/reference.blend").write_bytes(b"test source fixture")
    (path.parent / "exports/sample.glb").write_bytes(triangle())
    (path.parent / "evidence/runtime.png").write_bytes(b"test evidence fixture")
    (path.parent / "evidence/checks.md").write_text("Fixture review results", encoding="utf-8")
    save(path, data)
    return path, data


def test_new_study_is_pending_and_cannot_roll_out(tmp_path: Path) -> None:
    path = pipeline.init_study("world-study", tmp_path)
    assert pipeline.check(path)["ready_to_roll_out"] is False
    with pytest.raises(ValueError, match="real assets"):
        pipeline.check(path, "ready")
    with pytest.raises(FileExistsError):
        pipeline.init_study("world-study", tmp_path)


@pytest.mark.parametrize("value", ["../escape", "/absolute", "C:/absolute", "nested\\path"])
def test_paths_cannot_escape_a_study(tmp_path: Path, value: str) -> None:
    with pytest.raises(ValueError):
        pipeline.local_file(tmp_path, value)


def test_reference_check_does_not_grant_approval(tmp_path: Path) -> None:
    path, _ = reference(tmp_path)
    assert pipeline.check(path, "reference")["ready_to_roll_out"] is False
    with pytest.raises(ValueError, match="approval"):
        pipeline.check(path, "ready")


def test_approval_expires_when_reviewed_content_changes(tmp_path: Path) -> None:
    path, data = reference(tmp_path)
    data["approval"].update(status="approved", evidence_sha256=pipeline.reference_hash(path, data))
    (path.parent / "review.md").write_text(
        "Decision: approved\nTest fixture decision.", encoding="utf-8"
    )
    save(path, data)
    assert pipeline.check(path, "ready")["ready_to_roll_out"] is True
    (path.parent / "source/reference.blend").write_bytes(b"a different test source")
    with pytest.raises(ValueError, match="stale"):
        pipeline.check(path, "ready")


def test_missing_runtime_evidence_is_not_a_reference(tmp_path: Path) -> None:
    path, _ = reference(tmp_path)
    (path.parent / "evidence/runtime.png").unlink()
    with pytest.raises(ValueError, match="missing review artifact"):
        pipeline.check(path, "reference")


def test_export_destinations_are_unique(tmp_path: Path) -> None:
    path, data = reference(tmp_path)
    data["assets"].append({**data["assets"][0], "id": "another"})
    save(path, data)
    with pytest.raises(ValueError, match="own review export"):
        pipeline.read_manifest(path)


def test_truncated_export_is_rejected(tmp_path: Path) -> None:
    path, _ = reference(tmp_path)
    (path.parent / "exports/sample.glb").write_bytes(triangle()[:-4])
    with pytest.raises(ValueError, match="invalid GLB"):
        pipeline.check(path, "reference")
