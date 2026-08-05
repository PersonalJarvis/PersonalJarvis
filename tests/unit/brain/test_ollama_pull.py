"""In-app Ollama model downloads: honest fit verdicts, honest progress.

The point of this module is §3's "recoverable in-app" contract: a keyless
install whose server holds no models used to dead-end at "run: ollama pull …",
a terminal instruction in an app with no terminal. These tests pin the parts
that would silently lie if they broke — a model reported ready that the server
does not list, a fit verdict invented from an unreadable memory probe, a
duplicate multi-gigabyte download.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

import jarvis.brain.ollama_pull as pull
import jarvis.core.config as cfg
from jarvis.core.config import JarvisConfig


@pytest.fixture(autouse=True)
def _clean_runs(monkeypatch):
    """No ambient config, no OLLAMA_HOST, no leftover runs between tests."""
    monkeypatch.setattr(cfg, "load_config", lambda: JarvisConfig())
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    pull._runs.clear()
    yield
    pull._runs.clear()


# ── Fit verdict ──────────────────────────────────────────────────────────
def test_fit_is_comfortable_with_headroom() -> None:
    verdict, note = pull.fit_verdict(5.2, 32.0)
    assert verdict == "comfortable"
    assert "32" in note


def test_fit_is_tight_but_never_forbidden() -> None:
    """A GPU box runs models the RAM rule calls tight — the verdict informs the
    choice, it does not forbid it."""
    verdict, note = pull.fit_verdict(18.0, 16.0)
    assert verdict == "tight"
    assert "will" in note.lower()


def test_fit_is_unknown_when_memory_cannot_be_read() -> None:
    """An unreadable host must not produce an invented number that would make a
    9 GB model look safe on a 4 GB box."""
    verdict, _note = pull.fit_verdict(9.0, None)
    assert verdict == "unknown"


def test_total_memory_survives_a_broken_probe(monkeypatch) -> None:
    import psutil

    def _boom() -> None:
        raise OSError("no /proc")

    monkeypatch.setattr(psutil, "virtual_memory", _boom)
    assert pull.total_memory_gb() is None


# ── Installed-model bookkeeping ──────────────────────────────────────────
@pytest.mark.parametrize(
    ("model", "installed", "expected"),
    [
        ("qwen3.5:4b", {"qwen3.5:4b"}, True),
        # ``ollama pull qwen3.5`` installs qwen3.5:latest — a literal compare
        # would offer a pull the user already completed.
        ("qwen3.5", {"qwen3.5:latest"}, True),
        ("qwen3.5", {"qwen3.5:4b"}, False),
        ("qwen3-vl", set(), False),
    ],
)
def test_installed_matching_understands_the_latest_tag(
    model: str, installed: set[str], expected: bool
) -> None:
    assert pull._is_installed(model, installed) is expected


class _FakeTagsClient:
    payload: dict[str, Any] = {}
    fail: bool = False

    def __init__(self, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _FakeTagsClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get(self, url: str) -> Any:
        if _FakeTagsClient.fail:
            raise httpx.ConnectError("connection refused")

        class _Resp:
            @staticmethod
            def raise_for_status() -> None:
                return None

            @staticmethod
            def json() -> dict[str, Any]:
                return _FakeTagsClient.payload

        return _Resp()


@pytest.fixture()
def fake_tags(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _FakeTagsClient)
    _FakeTagsClient.fail = False
    _FakeTagsClient.payload = {}
    return _FakeTagsClient


async def test_installed_models_excludes_cloud_references(fake_tags) -> None:
    """``:cloud`` entries are ollama.com-proxied, not local weights — the same
    rule the brain applies when it picks a model."""
    fake_tags.payload = {
        "models": [
            {"name": "qwen3.5:latest"},
            {"name": "kimi-k2.5:cloud"},
            {"name": "other", "remote": True},
        ]
    }
    installed, error = await pull.installed_models()
    assert installed == {"qwen3.5:latest"}
    assert error is None


async def test_unreachable_server_reports_a_sentence_not_an_empty_list(fake_tags) -> None:
    """An empty list would read as "you have nothing installed" — which is a
    different problem with a different fix."""
    fake_tags.fail = True
    installed, error = await pull.installed_models()
    assert installed == set()
    assert error and "ollama.com/download" in error


async def test_recommendations_mark_what_is_already_there(fake_tags) -> None:
    fake_tags.payload = {"models": [{"name": "qwen3.5:latest"}]}
    result = await pull.recommendations()
    by_id = {m["id"]: m for m in result["models"]}
    assert by_id["qwen3.5"]["installed"] is True
    assert by_id["qwen3-vl"]["installed"] is False
    # The vision entry must be findable AS the vision entry — it is the one
    # that makes Screen Context work on a local-only install.
    assert by_id["qwen3-vl"]["vision"] is True
    assert result["server_reachable"] is True


async def test_recommendations_stay_usable_when_the_server_is_down(fake_tags) -> None:
    fake_tags.fail = True
    result = await pull.recommendations()
    assert result["server_reachable"] is False
    assert result["message"]
    assert [m["id"] for m in result["models"]], "the shortlist is still worth showing"


# ── Pull lifecycle ───────────────────────────────────────────────────────
async def test_pull_of_an_installed_model_is_a_no_op(fake_tags) -> None:
    fake_tags.payload = {"models": [{"name": "qwen3.5:latest"}]}
    result = await pull.start_pull("qwen3.5")
    assert result["state"] == "done"
    assert result["already"] is True


async def test_second_pull_joins_the_running_one(fake_tags, monkeypatch) -> None:
    """A duplicate multi-gigabyte download is the one mistake this route must
    never make."""
    fake_tags.payload = {"models": []}
    started: list[str] = []

    async def _never_finishes(model: str) -> None:
        started.append(model)
        await asyncio.Event().wait()

    monkeypatch.setattr(pull, "_run_pull", _never_finishes)
    first = await pull.start_pull("qwen3-vl")
    second = await pull.start_pull("qwen3-vl")
    assert first["state"] == "running"
    assert second["state"] == "running"
    await asyncio.sleep(0)
    assert started == ["qwen3-vl"]
    run = pull._run_for("qwen3-vl")
    assert run.task is not None
    run.task.cancel()


async def test_empty_model_name_is_rejected() -> None:
    result = await pull.start_pull("   ")
    assert result["state"] == "error"


async def test_status_trusts_the_server_over_local_bookkeeping(fake_tags) -> None:
    """A model pulled from the CLI or a previous app run reads as installed,
    not as "idle"."""
    fake_tags.payload = {"models": [{"name": "qwen3-vl:latest"}]}
    status = await pull.pull_status("qwen3-vl")
    assert status["state"] == "done"
    assert status["installed"] is True
    assert status["percent"] == 100.0


async def test_status_reports_real_progress(fake_tags) -> None:
    fake_tags.payload = {"models": []}
    run = pull._run_for("qwen3-vl")
    run.state = "running"
    run.completed = 250
    run.total = 1000
    status = await pull.pull_status("qwen3-vl")
    assert status["state"] == "running"
    assert status["percent"] == 25.0


class _FakeStream:
    """Minimal ``client.stream`` context manager over canned NDJSON lines."""

    def __init__(self, lines: list[str], status_code: int = 200) -> None:
        self._lines = lines
        self.status_code = status_code

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)  # type: ignore[arg-type]

    async def aiter_lines(self):
        for line in self._lines:
            yield line


def _streaming_client(stream: _FakeStream, tags_payload: dict[str, Any]) -> type:
    class _Client(_FakeTagsClient):
        def stream(self, method: str, url: str, **kwargs: Any) -> _FakeStream:
            assert url.endswith("/api/pull")
            return stream

    _FakeTagsClient.payload = tags_payload
    return _Client


async def test_finished_pull_is_verified_against_the_inventory(monkeypatch) -> None:
    """A pull can end cleanly and still leave nothing usable; "ready" over a
    missing model is exactly the lie this check exists to prevent."""
    stream = _FakeStream(['{"status":"pulling"}', '{"status":"success"}'])
    monkeypatch.setattr(httpx, "AsyncClient", _streaming_client(stream, {"models": []}))
    await pull._run_pull("qwen3-vl")
    run = pull._run_for("qwen3-vl")
    assert run.state == "error"
    assert "not listed" in run.message


async def test_successful_pull_reports_ready(monkeypatch) -> None:
    stream = _FakeStream(
        ['{"status":"pulling","completed":50,"total":100}', '{"status":"success"}']
    )
    monkeypatch.setattr(
        httpx, "AsyncClient", _streaming_client(stream, {"models": [{"name": "qwen3-vl:latest"}]})
    )
    await pull._run_pull("qwen3-vl")
    run = pull._run_for("qwen3-vl")
    assert run.state == "done"
    assert "ready" in run.message


async def test_unknown_model_name_points_at_the_library(monkeypatch) -> None:
    stream = _FakeStream([], status_code=404)
    monkeypatch.setattr(httpx, "AsyncClient", _streaming_client(stream, {"models": []}))
    await pull._run_pull("no-such-model")
    run = pull._run_for("no-such-model")
    assert run.state == "error"
    assert "ollama.com/library" in run.message


async def test_stream_error_line_becomes_an_error_state(monkeypatch) -> None:
    stream = _FakeStream(['{"error":"file does not exist"}'])
    monkeypatch.setattr(httpx, "AsyncClient", _streaming_client(stream, {"models": []}))
    await pull._run_pull("qwen3-vl")
    run = pull._run_for("qwen3-vl")
    assert run.state == "error"
    assert "file does not exist" in run.message
