"""Native images omitted by the CLI stream still reach the chat and archive."""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agent_chat import generated_images as images
from jarvis.agent_chat import runner_cli as rc
from jarvis.missions.standalone_run import read_marker
from jarvis.ui.web.outputs_routes import router
from tests.unit.agent_chat.test_ide_cli_seats import _turn

THREAD = "a2d21c42-3c21-4bde-b602-a8a3dc99440a"
OTHER = "a2d21c42-3c21-4bde-b602-a8a3dc99440b"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aRZkAAAAASUVORK5CYII="
)


def saved(home: Path, name: str = "image.png", *, thread: str = THREAD, at: float = 200) -> Path:
    file = home / "generated_images" / thread / name
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(PNG)
    os.utime(file, (at, at))
    return file


def collect(home: Path, root: Path, since: float = 100) -> list[images.GeneratedImage]:
    return images.collect_generated_images(
        codex_home=home, thread_id=THREAD, since=since, outputs_root=root
    )


def test_imports_only_this_turn_and_thread(tmp_path: Path) -> None:
    home, root = tmp_path / "seat", tmp_path / "outputs"
    current = saved(home)
    saved(home, "old.png", at=50)
    saved(home, thread=OTHER)
    result = collect(home, root)
    assert len(result) == 1
    assert result[0].path.read_bytes() == current.read_bytes() == PNG
    assert str(home) not in result[0].markdown
    assert "base64" not in result[0].markdown
    assert read_marker(result[0].path.parents[4])["kind"] == "chat_image"


def test_recovery_is_idempotent_and_keeps_the_original(tmp_path: Path) -> None:
    home, root = tmp_path / "seat", tmp_path / "outputs"
    original = saved(home)
    first = collect(home, root)
    assert collect(home, root) == first
    assert len(list(root.iterdir())) == 1
    assert original.read_bytes() == PNG


def test_missing_image_capability_is_an_empty_result(tmp_path: Path) -> None:
    assert collect(tmp_path / "seat", tmp_path / "outputs") == []
    assert not (tmp_path / "outputs").exists()


def test_refuses_path_traversal_thread(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        images.collect_generated_images(
            codex_home=tmp_path, thread_id="../../other", since=0, outputs_root=tmp_path / "out"
        )


@pytest.mark.parametrize("data", [b"<html>not an image</html>", b"not png"])
def test_refuses_non_image_payload(tmp_path: Path, data: bytes) -> None:
    file = saved(tmp_path / "seat")
    file.write_bytes(data)
    with pytest.raises(ValueError, match="supported PNG"):
        collect(tmp_path / "seat", tmp_path / "out")


def test_bounds_image_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    saved(tmp_path / "seat")
    monkeypatch.setattr(images, "MAX_IMAGE_BYTES", 8)
    with pytest.raises(ValueError, match="exceeds"):
        collect(tmp_path / "seat", tmp_path / "out")


def test_rejects_cross_thread_file_links(tmp_path: Path) -> None:
    home = tmp_path / "seat"
    foreign = saved(home, thread=OTHER)
    ours = home / "generated_images" / THREAD
    ours.mkdir()
    try:
        (ours / "foreign.png").symlink_to(foreign)
    except OSError:
        pytest.skip("Creating symlinks is unavailable for this test account")
    with pytest.raises(ValueError, match="conversation"):
        collect(home, tmp_path / "out")


def test_existing_download_route_serves_the_png_and_rejects_escape(tmp_path: Path) -> None:
    home, root = tmp_path / "seat", tmp_path / "outputs"
    saved(home)
    result = collect(home, root)[0]
    url = result.markdown.split("](", 1)[1].split(")", 1)[0]
    app = FastAPI()
    app.state.outputs_root = root
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get(url)
        assert response.status_code == 200
        assert response.content == PNG
        assert response.headers["content-type"] == "image/png"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert client.get(url.replace("tasks/chat/artifacts/files", "other")).status_code == 404


@pytest.mark.parametrize("content", [PNG, b"invalid image", None])
def test_cli_pump_delivers_native_image_or_reports_delivery_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: bytes | None
) -> None:
    home, root = tmp_path / "seat", tmp_path / "outputs"
    monkeypatch.setenv("JARVIS_ISOLATION_ROOT", str(root))
    target = home / "generated_images" / THREAD / "native.png"
    started = {"type": "thread.started", "thread_id": THREAD}
    # An actual lightweight subprocess writes the file without an image event,
    # reproducing the native CLI's dropped extension item.
    write_image = (
        f"p=Path({str(target)!r}); p.parent.mkdir(parents=True); p.write_bytes({content!r}); "
        if content is not None
        else ""
    )
    body = (
        "import json; from pathlib import Path; "
        + write_image
        + f"print(json.dumps({started!r})); "
        "print(json.dumps({'type':'turn.completed','usage':{}}))"
    )
    plan = rc.CliPlan(
        [sys.executable, "-c", body], {**os.environ, "CODEX_HOME": str(home)}, None, "codex", None
    )
    vendor, events = _turn(monkeypatch, tmp_path, "codex-cli", plan, "openai-codex")
    assert vendor == THREAD
    if content is None:
        assert [event["kind"] for event in events] == ["turn_finished"]
        assert events[-1]["payload"]["status"] == "done"
        return
    if content != PNG:
        assert [event["kind"] for event in events] == ["error", "turn_finished"]
        assert events[-1]["payload"]["status"] == "error"
        return
    assert [event["kind"] for event in events] == ["assistant_text", "turn_finished"]
    assert "![generated-image.png](/api/outputs/" in events[0]["payload"]["text"]
    assert events[-1]["payload"]["status"] == "done"
