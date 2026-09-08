"""Cold status queries must not initialize an inference runtime or block HTTP."""

import asyncio
import sys
import threading
from types import SimpleNamespace

import pytest

from jarvis.speech import local_models


@pytest.mark.parametrize(
    "missing", [None, "config.json", "model.bin", "tokenizer.json", "vocabulary.json"]
)
def test_local_cache_requires_complete_files(tmp_path, missing):
    for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.json"):
        if name != missing:
            (tmp_path / name).write_text("fixture", encoding="utf-8")
    assert local_models.whisper_model_cached(str(tmp_path)) is (missing is None)


def test_alias_probe_reads_installed_mapping_without_executing_it(tmp_path, monkeypatch):
    import huggingface_hub

    package = tmp_path / "faster_whisper"
    package.mkdir()
    (package / "__init__.py").write_text(
        "raise AssertionError('inference imported')", encoding="utf-8"
    )
    (package / "utils.py").write_text(
        "import torch\n_MODELS = {'turbo': 'custom/turbo'}", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "faster_whisper", raising=False)
    seen = []

    def snapshot(repo, **kwargs):
        seen.append((repo, kwargs))
        return str(tmp_path / "partial")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot)
    assert local_models.whisper_model_cached("turbo") is False
    assert seen == [("custom/turbo", {"local_files_only": True})]
    assert "faster_whisper" not in sys.modules


@pytest.mark.asyncio
async def test_slow_status_disk_check_does_not_block_the_loop(monkeypatch):
    from jarvis.ui.web import provider_routes

    entered, release = threading.Event(), threading.Event()
    loop_thread = threading.get_ident()

    def slow_check(spec):
        assert threading.get_ident() != loop_thread
        entered.set()
        assert release.wait(3)
        return {"ready": True}

    monkeypatch.setattr(provider_routes, "_local_runtime_payload", slow_check)
    spec = SimpleNamespace(auth_mode="none", id="local", label="Local")
    task = asyncio.create_task(provider_routes._tier_section_health(None, spec))
    try:
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set()
        assert not task.done()
    finally:
        release.set()
        await task
