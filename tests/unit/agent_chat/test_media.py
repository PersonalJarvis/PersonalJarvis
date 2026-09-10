"""Every runner shares the same media delivery and workspace boundary."""

from __future__ import annotations

import asyncio
import base64
import json

import pytest

from jarvis.agent_chat import media
from jarvis.agent_chat.runner_cli import _content_text
from jarvis.agent_chat.service import AgentChatService
from jarvis.agent_chat.store import AgentChatStore

PNG = b"\x89PNG\r\n\x1a\nexample image bytes"


def event(output, kind="tool_result", **extra):
    field = "text" if kind == "assistant_text" else "output"
    return {"kind": kind, "ts_ms": 1000, "payload": {"turn_id": "turn", field: output, **extra}}


def normalize(tmp_path, output, kind="tool_result", **extra):
    cwd = tmp_path / "work"
    cwd.mkdir(exist_ok=True)
    return media.normalize_media_event(
        event(output, kind, **extra), cwd=cwd, outputs_root=tmp_path / "archive", scope="session"
    )


def test_mixed_images_videos_and_audio_are_all_delivered(tmp_path):
    outputs = [
        {"url": "https://example.test/a.png"},
        {"url": "https://example.test/b.mp4"},
        {"url": "https://example.test/c.wav"},
    ]
    result = normalize(tmp_path, json.dumps({"results": outputs}))
    assert len(result) == 4
    assert "![image]" in result[1]["payload"]["text"]
    assert "[video]" in result[2]["payload"]["text"]
    assert "[audio]" in result[3]["payload"]["text"]
    assert not (tmp_path / "archive").exists()  # No arbitrary server-side URL fetch.


def test_remote_path_cannot_import_a_same_named_hub_file(tmp_path):
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / "screen.png").write_bytes(PNG)
    result = media.normalize_media_event(
        event("![screen](screen.png)", "assistant_text"),
        cwd=cwd,
        outputs_root=tmp_path / "archive",
        scope="session",
        allow_local_files=False,
    )
    assert not (tmp_path / "archive").exists()
    assert len(result) == 1
    assert "remote file" in result[0]["payload"]["text"]
    assert "![screen]" not in result[0]["payload"]["text"]


def test_remote_inline_image_is_still_archived(tmp_path):
    result = media.normalize_media_event(
        event(
            json.dumps(
                {"type": "image", "mime": "image/png", "data": base64.b64encode(PNG).decode()}
            )
        ),
        cwd=tmp_path,
        outputs_root=tmp_path / "archive",
        scope="session",
        allow_local_files=False,
    )
    assert len(result) == 2
    assert "/api/outputs/" in result[1]["payload"]["text"]


@pytest.mark.parametrize(
    "shape",
    [
        lambda b: {"content": [{"type": "image", "mimeType": "image/png", "data": b}]},
        lambda b: {"data": [{"b64_json": b}]},
        lambda b: {"inlineData": {"mimeType": "image/png", "data": b}},
        lambda b: {"resource": {"mimeType": "image/png", "blob": b}},
        lambda b: {"type": "image_generation_call", "result": b},
    ],
)
def test_binary_receipt_shapes_become_small_durable_links(tmp_path, shape):
    encoded = base64.b64encode(PNG).decode()
    result = normalize(tmp_path, json.dumps(shape(encoded)))
    assert len(result) == 2
    assert encoded not in json.dumps(result)
    files = list((tmp_path / "archive").rglob("*.png"))
    assert len(files) == 1 and files[0].read_bytes() == PNG
    assert "/api/outputs/" in result[1]["payload"]["text"]


@pytest.mark.parametrize(
    "shape",
    [
        {"image_url": {"url": "https://example.test/signed?id=1"}},
        {"video": {"url": "https://example.test/signed?id=1"}},
        {
            "type": "resource_link",
            "mimeType": "video/mp4",
            "uri": "https://example.test/signed?id=1",
        },
        {"image_url": "https://example.test/signed?id=1"},
    ],
)
def test_extensionless_urls_keep_their_media_hint(tmp_path, shape):
    result = normalize(tmp_path, shape)
    assert len(result) == 2
    assert "#jarvis-media=" in result[1]["payload"]["text"]
    assert "?id=1" in result[1]["payload"]["text"]


@pytest.mark.parametrize(
    "name", ["movie.mp4", "photo with spaces.webp", "sound.mp3", "diagram.svg"]
)
def test_local_cli_files_are_retained_after_the_source_is_removed(tmp_path, name):
    cwd = tmp_path / "work"
    cwd.mkdir()
    source = cwd / name
    source.write_bytes(b"tool-created media")
    result = normalize(tmp_path, {"file_path": str(source)})
    source.unlink()
    assert len(result) == 2
    assert [p.read_bytes() for p in (tmp_path / "archive").rglob("media.*")] == [
        b"tool-created media"
    ]
    assert str(cwd) not in result[1]["payload"]["text"]


def test_markdown_local_media_stays_in_place_without_a_duplicate(tmp_path):
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / "plot.png").write_bytes(PNG)
    result = normalize(tmp_path, "Before.\n\n![Plot](plot.png)\n\nAfter.", "assistant_text")
    assert len(result) == 1
    text = result[0]["payload"]["text"]
    assert "![Plot](</api/outputs/" in text
    assert text.startswith("Before.") and text.endswith("After.")


def test_preserves_normal_links_and_does_not_scan_code_examples(tmp_path):
    text = '[Documentation](https://example.test/docs "Title")\n```sh\nread /secret/photo.png\n```'
    result = normalize(tmp_path, text, "assistant_text")
    assert result == [event(text, "assistant_text")]


def test_quoted_cli_media_paths_with_spaces(tmp_path):
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / "final movie.mp4").write_bytes(b"video")
    result = normalize(tmp_path, 'Saved "final movie.mp4"')
    assert len(result) == 2
    assert "[video]" in result[1]["payload"]["text"]


@pytest.mark.parametrize(
    "name,reference", [("frame#1.png", "frame#1.png"), ("my photo.png", "my%20photo.png")]
)
def test_local_media_filenames_are_not_mistaken_for_url_fragments(tmp_path, name, reference):
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / name).write_bytes(PNG)
    result = normalize(tmp_path, {"path": reference})
    assert len(result) == 2
    assert "![image]" in result[1]["payload"]["text"]


def test_plain_remote_url_is_rendered_in_place_without_duplicate(tmp_path):
    result = normalize(tmp_path, "Watch https://example.test/clip.mp4", "assistant_text")
    assert len(result) == 1


def test_mixed_assistant_content_keeps_text_and_each_visual_in_order(tmp_path):
    blocks = [
        {"type": "text", "text": "First image:"},
        {"type": "image_url", "image_url": {"url": "https://example.test/first.png"}},
        {"type": "text", "text": "Then the video:"},
        {"type": "video", "url": "https://example.test/second.mp4"},
    ]
    result = normalize(tmp_path, json.dumps({"content": blocks}), "assistant_text")
    assert len(result) == 1
    text = result[0]["payload"]["text"]
    assert (
        text.index("First image:")
        < text.index("![image]")
        < text.index("Then the video:")
        < text.index("[video]")
    )
    assert "image_url" not in text


def test_uploaded_media_uses_the_existing_attachment_url_contract(tmp_path):
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / "upload.mp4").write_bytes(b"video attachment")
    original = {
        "kind": "user_message",
        "payload": {
            "text": "Watch this",
            "attachments": [{"name": "upload.mp4", "kind": "other", "reference": '"upload.mp4"'}],
        },
    }
    result = media.normalize_media_event(
        original, cwd=cwd, outputs_root=tmp_path / "archive", scope="session"
    )
    assert len(result) == 1
    attachment = result[0]["payload"]["attachments"][0]
    assert attachment["url"].startswith("/api/outputs/")
    assert "reference" not in attachment
    assert result[0]["payload"]["text"] == "Watch this"


def test_backend_and_renderer_recognize_the_same_media_extensions():
    import re
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[3]
        / "jarvis/ui/web/frontend/src/components/agentchat/ChatMarkdown.tsx"
    ).read_text(encoding="utf-8")
    block = source.split("const EXTENSIONS:", 1)[1].split("};", 1)[0]
    frontend = dict(re.findall(r'(\w+): "(image|video|audio)"', block))
    assert frontend == {ext[1:]: mime.split("/", 1)[0] for ext, mime in media.MEDIA_TYPES.items()}


def test_archived_video_supports_byte_ranges_for_seeking(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from jarvis.ui.web.outputs_routes import router

    cwd = tmp_path / "work"
    cwd.mkdir()
    data = b"video byte range fixture"
    (cwd / "clip.mp4").write_bytes(data)
    result = normalize(tmp_path, {"path": "clip.mp4"})
    url = result[1]["payload"]["text"].split("(<", 1)[1].split(">)", 1)[0]
    app = FastAPI()
    app.state.outputs_root = tmp_path / "archive"
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get(url, headers={"Range": "bytes=0-3"})
        assert response.status_code == 206
        assert response.content == data[:4]
        assert response.headers["content-type"] == "video/mp4"


def test_changing_media_is_not_published_as_a_complete_artifact(tmp_path):
    file = tmp_path / "clip.mp4"
    file.write_bytes(b"initial")
    expected = file.stat()
    file.write_bytes(b"still being written")
    archive = tmp_path / "archive"
    normalizer = media.MediaNormalizer(tmp_path, archive, "session")
    with file.open("rb") as source, pytest.raises(ValueError, match="changed"):
        normalizer._archive(source, ".mp4", expected)
    assert not list(archive.rglob(".standalone-run.json"))


def test_metadata_filenames_do_not_pick_up_unrelated_workspace_images(tmp_path):
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / "logo.png").write_bytes(PNG)
    result = normalize(
        tmp_path, {"filename": "logo.png", "iconLink": "https://example.test/icon.png"}
    )
    assert len(result) == 1
    assert not (tmp_path / "archive").exists()


@pytest.mark.parametrize("key", ["output", "outputs", "artifacts", "files", "media"])
def test_plain_output_lists_still_deliver_each_media_url(tmp_path, key):
    result = normalize(
        tmp_path, {key: ["https://example.test/a.png", "https://example.test/b.mp4"]}
    )
    assert len(result) == 3


def test_media_in_failed_tool_output_is_not_claimed_as_a_result(tmp_path):
    result = normalize(tmp_path, "https://example.test/photo.png", is_error=True)
    assert len(result) == 1


@pytest.mark.parametrize(
    "reference",
    [
        "../secret.png",
        "file:///etc/private.png",
        "https://user:password@example.test/a.png",
        "javascript:alert(1).png",
    ],
)
def test_invalid_or_outside_workspace_media_is_not_imported(tmp_path, reference):
    (tmp_path / "secret.png").write_bytes(PNG)
    result = normalize(tmp_path, {"path": reference})
    assert not list((tmp_path / "archive").glob("*"))
    assert all("![image]" not in str(item) for item in result)
    assert result[-1]["kind"] == "error"


def test_import_limit_reports_an_error_instead_of_silently_hiding_media(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "MAX_FILE_BYTES", 2)
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / "large.mp4").write_bytes(b"large video")
    result = normalize(tmp_path, {"path": "large.mp4"})
    assert result[-1]["kind"] == "error"
    assert not list((tmp_path / "archive").rglob("media.mp4"))


def test_claude_shaped_content_does_not_discard_non_text_media():
    blocks = [
        {"type": "text", "text": "Here is the video"},
        {"type": "resource_link", "mimeType": "video/mp4", "uri": "https://example.test/video.mp4"},
    ]
    assert json.loads(_content_text(blocks)) == blocks


@pytest.mark.parametrize("provider", ["openai", "google", "openai-codex", "arbitrary-cli"])
def test_service_persists_and_streams_media_for_any_provider(tmp_path, monkeypatch, provider):
    monkeypatch.setenv("JARVIS_ISOLATION_ROOT", str(tmp_path / "archive"))
    store = AgentChatStore(tmp_path / "chat.db")
    session = store.create_session(provider=provider, model="", effort="", cwd=str(tmp_path))
    service = AgentChatService(store)
    queue = service.subscribe(session.session_id)
    asyncio.run(
        service._emit(session.session_id, event('{"url":"https://example.test/result.webm"}'))
    )
    persisted = store.list_events(session.session_id)
    assert [item["kind"] for item in persisted] == ["tool_result", "assistant_text"]
    assert queue.get_nowait() == persisted[0]
    assert queue.get_nowait() == persisted[1]
    assert "[video]" in persisted[1]["payload"]["text"]
    store.close()
