"""The app library stores files without changing or overstating voice readiness."""

from __future__ import annotations

import asyncio
import re
import threading
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.realtime.local_runtime import store
from jarvis.realtime.local_runtime.library import (
    LocalVoiceLibrary,
    PackageJob,
    VoiceLibrary,
    VoiceLibraryEntry,
)
from jarvis.ui.web.local_voice_routes import router
from tests.fakes.fake_local_voice_model import model_manifest


@pytest.mark.asyncio
async def test_custom_import_is_visible_after_restart_and_can_be_reverified(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "model.gguf").write_bytes(b"model")
    model = model_manifest(source={"kind": "local"})
    library = LocalVoiceLibrary(tmp_path / "store")
    job = library.start(model, source)
    await library._task
    await library.close()
    assert library._task is not None and library._task.done()
    second = LocalVoiceLibrary(tmp_path / "store")
    assert second.snapshot().entries[-1].package_present
    assert not second.snapshot().entries[-1].runtime_qualified
    third = LocalVoiceLibrary(tmp_path / "store")
    assert third.resolve(job.fingerprint) == model
    third.start(model)
    await third._task
    entry = third.snapshot().entries[-1]
    assert entry.package_present and entry.job.phase == "stored"
    assert not entry.runtime_qualified
    weights = tmp_path / "store" / model.id / model.fingerprint / "weights" / "model.gguf"
    await asyncio.to_thread(weights.unlink)
    assert not third.snapshot().entries[-1].package_present
    await second.close()
    await third.close()


@pytest.mark.asyncio
async def test_cancellation_keeps_slot_until_worker_cleanup_and_retry_is_possible(
    tmp_path: Path,
    monkeypatch,
) -> None:
    entered = threading.Event()

    def download(url, cancel):
        entered.set()
        cancel.wait(5)
        yield b"model"

    monkeypatch.setattr(store, "_download", download)
    library = LocalVoiceLibrary(tmp_path)
    model = model_manifest()
    job = library.start(model)
    assert await asyncio.to_thread(entered.wait, 2)
    assert library.cancel(job.id).phase == "cancelling"
    with pytest.raises(ValueError, match="current model"):
        library.start(model)
    await library._task
    entry = library.snapshot().entries[-1]
    assert entry.job.phase == "cancelled" and not entry.package_present
    assert not await asyncio.to_thread(lambda: list(tmp_path.rglob(".download-*")))
    monkeypatch.setattr(store, "_download", lambda *_: iter([b"model"]))
    retry = library.start(model)
    assert retry.id != job.id
    with pytest.raises(ValueError, match="no longer active"):
        library.cancel(job.id)
    await library._task
    assert library.snapshot().entries[-1].package_present
    await library.close()


@pytest.mark.asyncio
async def test_shutdown_waits_for_cancelled_download_and_never_publishes_partial_weights(
    tmp_path: Path,
    monkeypatch,
) -> None:
    entered = threading.Event()

    def download(url, cancel):
        entered.set()
        cancel.wait(5)
        yield b"model"

    monkeypatch.setattr(store, "_download", download)
    library = LocalVoiceLibrary(tmp_path)
    library.start(model_manifest())
    assert await asyncio.to_thread(entered.wait, 2)
    await library.close()
    assert library._task.done()
    assert library.snapshot().entries[-1].job.phase == "cancelled"
    assert not await asyncio.to_thread(lambda: list(tmp_path.rglob("manifest.json")))
    with pytest.raises(ValueError, match="shutting down"):
        library.start(model_manifest())


def test_mounted_routes_accept_custom_models_without_a_provider_or_gpu(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(router)
    library = LocalVoiceLibrary(tmp_path / "store")
    app.state.local_voice_library = library
    with TestClient(app) as client:
        response = client.get("/api/local-voice/models")
        assert response.status_code == 200
        assert len(response.json()["entries"]) >= 2
        assert all(not item["runtime_qualified"] for item in response.json()["entries"])
        assert client.post("/api/local-voice/models/acquire", json={}).status_code == 400
        assert (
            client.post("/api/local-voice/models/acquire", json={"model_id": "missing"}).status_code
            == 404
        )
        model = model_manifest(source={"kind": "local"})
        source = tmp_path / "source"
        source.mkdir()
        (source / "model.gguf").write_bytes(b"model")
        result = client.post(
            "/api/local-voice/models/acquire",
            json={
                "manifest": model.model_dump(mode="json"),
                "source_directory": str(source),
            },
        )
        assert result.status_code == 202
        assert result.json()["fingerprint"] == model.fingerprint
        assert client.post("/api/local-voice/jobs/wrong/cancel").status_code == 409
    assert library._closed
    schema = app.openapi()
    assert schema["paths"]["/api/local-voice/models/acquire"]["post"]["tags"] == ["local-voice"]


def test_native_voice_wire_fields_match_the_typescript_mirror() -> None:
    source = Path("jarvis/ui/web/frontend/src/hooks/useNativeVoiceModels.ts").read_text(
        encoding="utf-8"
    )
    for model, name in (
        (PackageJob, "NativeVoiceJob"),
        (VoiceLibraryEntry, "NativeVoiceEntry"),
        (VoiceLibrary, "NativeVoiceLibrary"),
    ):
        body = re.search(rf"export interface {name} \{{(.*?)\n\}}", source, re.S).group(1)
        fields = set(re.findall(r"^  (\w+):", body, re.M))
        assert fields == set(model.model_fields)
