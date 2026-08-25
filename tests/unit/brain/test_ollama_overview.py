"""The overview: one payload, disk snapshot first, one background refresh.

What would lie if it broke: an open that waits on the server although a
snapshot exists, a snapshot from another server painted as this one's, a
day-old snapshot painted although the server answers, two refreshes racing
for one open, a half-written snapshot file after a crash.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest

from jarvis.brain import ollama_inventory, ollama_overview, ollama_pull, ollama_runtime
from jarvis.core.config import BrainProviderConfig, JarvisConfig
from tests.fakes.fake_ollama_server import FakeOllamaServer

ROOT = "http://localhost:11434"


def _names(directory: Path) -> list[str]:
    return [p.name for p in directory.iterdir()]


def _cfg() -> JarvisConfig:
    cfg = JarvisConfig()
    cfg.brain.providers["ollama"] = BrainProviderConfig(model="qwen3.5:4b")
    return cfg


def _status(running: bool) -> dict:
    return {
        "installed": True,
        "binary": "/usr/local/bin/ollama",
        "running": running,
        "version": "0.32.15" if running else "",
        "detail": "Ollama is running." if running else "Ollama is installed but not running.",
        "base_url": ROOT,
        "host_kind": "local",
        "models_dir": "/home/me/.ollama/models",
    }


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeOllamaServer:
    ollama_inventory._reset_for_tests()
    ollama_overview._reset_for_tests()
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    server = FakeOllamaServer()
    server.add("qwen3.5:4b", size=3_400_000_000, capabilities=("completion", "tools", "vision"))
    server.add("qwen3-embedding:4b", size=2_500_000_000, capabilities=("embedding",))
    server.load("qwen3.5:4b", size_vram=3_000_000_000)

    def _client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=server.transport())

    monkeypatch.setattr(ollama_inventory, "_make_client", _client)
    monkeypatch.setattr(ollama_runtime, "runtime_status", lambda: _status(not server.offline))

    async def _recommendations() -> dict:
        return {"models": [], "curated_reviewed_on": "2026-08-24", "server_reachable": True}

    monkeypatch.setattr(ollama_pull, "recommendations", _recommendations)
    yield server
    ollama_inventory._reset_for_tests()
    ollama_overview._reset_for_tests()


async def test_first_open_builds_live_and_saves_the_snapshot(fake, tmp_path: Path) -> None:
    payload, source = await ollama_overview.get_overview(ROOT, _cfg())
    assert source == "live"
    assert payload["server"]["running"] is True
    assert payload["inventory"]["disk_bytes"] == 3_400_000_000 + 2_500_000_000
    assert [r["name"] for r in payload["server"]["running_models"]] == ["qwen3.5:4b"]
    roles = {r["id"]: r for r in payload["roles"]["roles"]}
    assert roles["chat"]["current"] == "qwen3.5:4b" and roles["chat"]["installed"] is True
    assert payload["recommended"]["curated_reviewed_on"] == "2026-08-24"
    assert payload["fetched_at"] == pytest.approx(time.time(), abs=5)
    # One sweep for the whole payload.
    paths = [c[1] for c in fake.calls]
    assert paths.count("/api/tags") == 1 and paths.count("/api/show") == 2

    saved = ollama_overview.load_snapshot()
    assert saved is not None and saved["root"] == ROOT
    assert saved["payload"]["inventory"]["disk_bytes"] == payload["inventory"]["disk_bytes"]
    assert sorted(_names(tmp_path)) == [ollama_overview.SNAPSHOT_FILE_NAME], "no .part left"


async def test_a_second_open_within_the_window_is_the_memo(fake) -> None:
    first, _ = await ollama_overview.get_overview(ROOT, _cfg())
    fake.calls.clear()
    second, source = await ollama_overview.get_overview(ROOT, _cfg())
    assert source == "live" and second is first
    assert fake.calls == []


async def test_the_disk_snapshot_paints_at_once_and_one_refresh_runs(fake, tmp_path) -> None:
    """Offline server + a snapshot from the last open: the answer is the
    snapshot, within one tick, and exactly one background refresh starts."""
    live, _ = await ollama_overview.get_overview(ROOT, _cfg())
    ollama_overview._reset_for_tests()  # a fresh process: memo empty, file present
    ollama_inventory._reset_for_tests()
    fake.offline = True
    fake.calls.clear()

    started = time.perf_counter()
    payload, source = await ollama_overview.get_overview(ROOT, _cfg())
    assert source == "cache"
    assert payload["inventory"]["disk_bytes"] == live["inventory"]["disk_bytes"]
    assert time.perf_counter() - started < 0.5
    task = ollama_overview._refresh_tasks.get(ROOT)
    assert task is not None and not task.done()
    # A second open while the refresh runs joins it — no second task.
    again, source_again = await ollama_overview.get_overview(ROOT, _cfg())
    assert source_again == "cache"
    assert ollama_overview._refresh_tasks.get(ROOT) is task

    refreshed = await task
    assert refreshed["server"]["running"] is False
    assert "did not answer" in refreshed["inventory"]["error"]
    assert ROOT not in ollama_overview._refresh_tasks
    # The refreshed payload is now the memo, and it reached the disk.
    memo, memo_source = await ollama_overview.get_overview(ROOT, _cfg())
    assert memo_source == "live" and memo is refreshed
    saved = ollama_overview.load_snapshot()
    assert saved is not None and saved["payload"]["server"]["running"] is False


async def test_a_day_old_snapshot_is_replaced_by_a_live_build(fake) -> None:
    await ollama_overview.get_overview(ROOT, _cfg())
    stale = ollama_overview.load_snapshot()
    assert stale is not None
    stale["payload"]["fetched_at"] = time.time() - ollama_overview.STALE_AFTER_S - 60
    ollama_overview.save_snapshot(ROOT, stale["payload"])
    ollama_overview._reset_for_tests()
    ollama_inventory._reset_for_tests()
    fake.calls.clear()
    _payload, source = await ollama_overview.get_overview(ROOT, _cfg())
    assert source == "live"
    assert [c[1] for c in fake.calls].count("/api/tags") == 1
    assert ollama_overview._refresh_tasks == {}


async def test_a_snapshot_of_another_server_is_not_painted(fake) -> None:
    await ollama_overview.get_overview(ROOT, _cfg())
    ollama_overview._reset_for_tests()
    ollama_inventory._reset_for_tests()
    _payload, source = await ollama_overview.get_overview("http://gpu-box:11434", _cfg())
    assert source == "live"


async def test_fresh_skips_memo_and_disk(fake) -> None:
    await ollama_overview.get_overview(ROOT, _cfg())
    fake.calls.clear()
    ollama_inventory._reset_for_tests()
    _payload, source = await ollama_overview.get_overview(ROOT, _cfg(), fresh=True)
    assert source == "live"
    assert [c[1] for c in fake.calls].count("/api/tags") == 1


def test_a_malformed_snapshot_is_ignored(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    path = ollama_overview.snapshot_path()
    assert ollama_overview.load_snapshot() is None
    path.write_text("{not json", encoding="utf-8")
    assert ollama_overview.load_snapshot() is None
    path.write_text(json.dumps({"payload": "nope", "fetched_at": 1}), encoding="utf-8")
    assert ollama_overview.load_snapshot() is None
    ollama_overview.save_snapshot(ROOT, {"fetched_at": 12.0, "x": 1})
    assert ollama_overview.load_snapshot() == {
        "root": ROOT,
        "fetched_at": 12.0,
        "payload": {"fetched_at": 12.0, "x": 1},
    }


async def test_a_failing_refresh_is_logged_not_lost(fake, monkeypatch, caplog) -> None:
    """A bug in the build must not vanish into a garbage-collected task."""
    await ollama_overview.get_overview(ROOT, _cfg())
    ollama_overview._reset_for_tests()

    async def _boom(*_a, **_kw) -> dict:
        raise RuntimeError("build exploded")

    monkeypatch.setattr(ollama_overview, "build_overview", _boom)
    _payload, source = await ollama_overview.get_overview(ROOT, _cfg())
    assert source == "cache"
    task = ollama_overview._refresh_tasks[ROOT]
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)  # let the done-callback run
    assert ROOT not in ollama_overview._refresh_tasks
    assert any("background refresh" in r.getMessage() for r in caplog.records)
