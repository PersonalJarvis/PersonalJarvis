"""Acquisition publishes verified weights, never inference readiness or activation."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from filelock import FileLock

from jarvis.realtime.local_runtime import store
from jarvis.realtime.local_runtime.packages import load_manifest, verify_package
from tests.fakes.fake_local_voice_model import model_manifest


def test_download_is_verified_and_subsequent_calls_reuse_files(tmp_path: Path, monkeypatch) -> None:
    requests: list[str] = []

    def download(url, cancel):
        requests.append(url)
        yield b"mo"
        yield b"del"

    monkeypatch.setattr(store, "_download", download)
    model = model_manifest()
    progress = []
    weights = store.acquire_model(model, tmp_path, progress=progress.append)
    assert verify_package(model, weights).verified
    assert load_manifest(weights.parent / "manifest.json") == model
    assert progress[-1].completed_bytes == 5
    assert store.acquire_model(model, tmp_path) == weights
    assert len(requests) == 1
    assert not list(tmp_path.rglob(".download-*"))


def test_cancellation_keeps_verified_files_but_never_publishes_incomplete_package(
    tmp_path: Path, monkeypatch
) -> None:
    cancel = threading.Event()

    def download(url, stop):
        yield b"mo"
        stop.set()
        yield b"del"

    monkeypatch.setattr(store, "_download", download)
    with pytest.raises(store.ModelAcquisitionCancelled):
        store.acquire_model(model_manifest(), tmp_path, cancel=cancel)
    assert not list(tmp_path.rglob("manifest.json"))
    assert not list(tmp_path.rglob("model.gguf"))
    assert not list(tmp_path.rglob(".download-*"))


def test_failed_replacement_does_not_modify_previous_verified_version(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(store, "_download", lambda url, cancel: iter([b"model"]))
    original = model_manifest()
    weights = store.acquire_model(original, tmp_path)
    changed = model_manifest(
        source={"kind": "huggingface", "repository": "test/voice", "revision": "b" * 40}
    )
    monkeypatch.setattr(store, "_download", lambda url, cancel: iter([b"other"]))
    with pytest.raises(store.ModelAcquisitionError, match="checksum"):
        store.acquire_model(changed, tmp_path)
    assert verify_package(original, weights).verified
    assert load_manifest(weights.parent / "manifest.json") == original
    assert len(list(tmp_path.rglob("manifest.json"))) == 1


def test_custom_local_weights_are_copied_without_any_network(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "model.gguf").write_bytes(b"model")

    def forbidden(*args):
        raise AssertionError("Local import must not use the network")

    monkeypatch.setattr(store, "_download", forbidden)
    model = model_manifest(source={"kind": "local"})
    weights = store.acquire_model(model, tmp_path / "store", local_directory=source)
    (source / "model.gguf").write_bytes(b"other")
    assert verify_package(model, weights).verified


def test_process_lock_refuses_competing_downloads(tmp_path: Path, monkeypatch) -> None:
    model = model_manifest()
    identity = tmp_path / model.id
    identity.mkdir()
    with FileLock(identity / ".acquire.lock", timeout=0):
        with pytest.raises(store.ModelAcquisitionError, match="already"):
            store.acquire_model(model, tmp_path)


def test_disk_preflight_prevents_download_before_first_byte(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(store.shutil, "disk_usage", lambda root: SimpleNamespace(free=0))

    def forbidden(*args):
        raise AssertionError("No bytes should be downloaded")

    monkeypatch.setattr(store, "_download", forbidden)
    with pytest.raises(store.ModelAcquisitionError, match="disk"):
        store.acquire_model(model_manifest(), tmp_path)


def test_retry_resumes_at_verified_file_boundaries(tmp_path: Path, monkeypatch) -> None:
    first = model_manifest().artifacts[0].model_dump()
    model = model_manifest(
        artifacts=[
            first,
            {
                "path": "audio/projector.gguf",
                "size_bytes": 5,
                "sha256": hashlib.sha256(b"audio").hexdigest(),
            },
        ]
    )
    requests: list[str] = []

    def broken(url, cancel):
        requests.append(url)
        yield b"model" if url.endswith("model.gguf") else b"wrong"

    monkeypatch.setattr(store, "_download", broken)
    with pytest.raises(store.ModelAcquisitionError):
        store.acquire_model(model, tmp_path)

    def repaired(url, cancel):
        requests.append(url)
        yield b"audio"

    monkeypatch.setattr(store, "_download", repaired)
    weights = store.acquire_model(model, tmp_path)
    assert verify_package(model, weights).verified
    assert sum(url.endswith("model.gguf") for url in requests) == 1


def test_http_redirect_is_checked_before_following_it(tmp_path: Path, monkeypatch) -> None:
    requests: list[str] = []

    def reply(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        monkeypatch.setattr(store, "_HTTP", SimpleNamespace(client=lambda: client))
        with pytest.raises(store.ModelAcquisitionError, match="redirected"):
            store.acquire_model(model_manifest(), tmp_path)
    assert len(requests) == 1


def test_untrusted_http_body_is_not_in_error_message(tmp_path: Path, monkeypatch) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(403, text="private-response"))
    ) as client:
        monkeypatch.setattr(store, "_HTTP", SimpleNamespace(client=lambda: client))
        with pytest.raises(store.ModelAcquisitionError, match="HTTP 403") as error:
            store.acquire_model(model_manifest(), tmp_path)
    assert "private-response" not in str(error.value)
