"""Tests for deliver_to_user_folder + resolve_deliverables_dir (Fix 4,
2026-05-29): mirror a mission's archived deliverables into a user-visible
folder so a non-coder can actually find the worker's output."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.missions.kontrollierer.deliverable import (
    build_delivered_summary,
    deliver_to_user_folder,
    resolve_deliverables_dir,
)


def _make_artifact(
    mission_dir: Path, task_id: str, rel: str, content: str = "<h1>x</h1>"
) -> Path:
    files_dir = mission_dir / "tasks" / task_id / "artifacts" / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    p = files_dir / rel
    p.write_text(content, encoding="utf-8")
    return p


def test_resolve_override_is_created(tmp_path: Path) -> None:
    target = tmp_path / "my-outputs"
    out = resolve_deliverables_dir(str(target))
    assert out == target.resolve() or out == target
    assert out.is_dir()


def test_delivers_genuine_file(tmp_path: Path) -> None:
    mission = tmp_path / "mission_abc"
    _make_artifact(mission, "01__t", "proof.html")
    target = tmp_path / "delivered"

    delivered = deliver_to_user_folder(
        mission, mission_short_id="abc", override_dir=str(target)
    )

    assert len(delivered) == 1
    assert delivered[0].name == "proof.html"
    assert delivered[0].parent == target.resolve() or delivered[0].parent == target
    assert delivered[0].read_text(encoding="utf-8") == "<h1>x</h1>"


def test_no_tasks_dir_returns_empty(tmp_path: Path) -> None:
    assert deliver_to_user_folder(tmp_path / "mission_empty") == []


def test_idempotent_no_duplicate(tmp_path: Path) -> None:
    mission = tmp_path / "mission_abc"
    _make_artifact(mission, "01__t", "proof.html", "same")
    target = tmp_path / "delivered"

    first = deliver_to_user_folder(mission, override_dir=str(target))
    second = deliver_to_user_folder(mission, override_dir=str(target))

    assert len(first) == 1
    assert len(second) == 1
    # Exactly one file on disk — identical bytes are not re-copied.
    on_disk = list(target.iterdir())
    assert len(on_disk) == 1


def test_collision_different_bytes_gets_suffix(tmp_path: Path) -> None:
    target = tmp_path / "delivered"
    target.mkdir()
    # Pre-place a different-content file with the same name.
    (target / "proof.html").write_text("OLD", encoding="utf-8")

    mission = tmp_path / "mission_abc"
    _make_artifact(mission, "01__t", "proof.html", "NEW")

    delivered = deliver_to_user_folder(
        mission, mission_short_id="019e70d0-6c19", override_dir=str(target)
    )

    assert len(delivered) == 1
    # Original preserved, new file got the deterministic mission-id suffix.
    assert (target / "proof.html").read_text(encoding="utf-8") == "OLD"
    assert delivered[0].name == "proof__019e70d0-6c19.html"
    assert delivered[0].read_text(encoding="utf-8") == "NEW"


def test_build_delivered_summary_single(tmp_path: Path) -> None:
    folder = tmp_path / "Jarvis-Outputs"
    folder.mkdir()
    f = folder / "report.html"
    f.write_text("x", encoding="utf-8")
    out = build_delivered_summary([f])
    assert "report.html" in out
    assert "Jarvis-Outputs" in out
    assert out.startswith("Fertig.")


def test_build_delivered_summary_empty() -> None:
    assert build_delivered_summary([]) == ""
