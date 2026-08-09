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

    #: Registry manifests keyed by model id, for the real-size lookup. Empty by
    #: default, which is the offline case: the curated estimates are used.
    manifests: dict[str, dict[str, Any]] = {}

    def __init__(self, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _FakeTagsClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> Any:
        # The registry lookup is a SECOND caller of this client. It must answer
        # separately: handing it the /api/tags payload would make every model
        # report a nonsense size, and a test suite that reaches the real
        # registry would be a network dependency in a unit test.
        if "registry.ollama.ai" in url:
            return _registry_response(url)
        if _FakeTagsClient.fail:
            raise httpx.ConnectError("connection refused")

        class _Resp:
            status_code = 200

            @staticmethod
            def raise_for_status() -> None:
                return None

            @staticmethod
            def json() -> dict[str, Any]:
                return _FakeTagsClient.payload

        return _Resp()


def _registry_response(url: str) -> Any:
    """A manifest for a model the test declared, else an honest 404."""
    # ".../v2/library/<name>/manifests/<tag>"
    parts = url.split("/v2/library/", 1)[-1].split("/manifests/")
    model = parts[0] if len(parts) < 2 or parts[1] == "latest" else f"{parts[0]}:{parts[1]}"
    manifest = _FakeTagsClient.manifests.get(model)

    class _Resp:
        status_code = 200 if manifest else 404

        @staticmethod
        def json() -> dict[str, Any]:
            return manifest or {}

    return _Resp()


@pytest.fixture()
def fake_tags(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _FakeTagsClient)
    _FakeTagsClient.fail = False
    _FakeTagsClient.payload = {}
    _FakeTagsClient.manifests = {}
    pull._registry_sizes.clear()
    yield _FakeTagsClient
    pull._registry_sizes.clear()


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


# ── Hardware-aware recommendation ────────────────────────────────────────
# The shortlist used to be the same four names on every machine, so a
# workstation with a 48 GB card was told to run a 4B model and a small laptop
# was offered one it could not load. These pin the ranking that replaced it.


def test_gpu_memory_outranks_ram_in_the_verdict() -> None:
    """A 14 GB model on a box with a 24 GB card runs at full speed. Judging it
    by the RAM rule alone called that "tight", which is backwards — and called
    the same model "comfortable" on a 64 GB CPU-only server, where it crawls."""
    verdict, note = pull.fit_verdict(14.0, memory_gb=32.0, accel_gb=24.0)
    assert verdict == "comfortable"
    assert "graphics memory" in note

    verdict, note = pull.fit_verdict(14.0, memory_gb=64.0, accel_gb=8.0)
    assert verdict == "tight"
    assert "CPU" in note


def test_a_machine_with_no_readable_gpu_falls_back_to_the_ram_rule() -> None:
    """0 GB of accelerator means "none I could read", never "no memory": an AMD
    card this probe cannot see must not downgrade the machine to unusable."""
    verdict, note = pull.fit_verdict(5.0, memory_gb=32.0, accel_gb=0.0)
    assert verdict == "comfortable"
    assert "32" in note


def test_accelerator_probe_survives_a_missing_hardware_module(monkeypatch) -> None:
    import jarvis.hardware.detection as detection

    def _boom() -> tuple[float, str]:
        raise OSError("no nvidia-smi")

    monkeypatch.setattr(detection, "usable_accelerator_gb", _boom)
    assert pull.accelerator_gb() == (0.0, "none")


def _machine(monkeypatch, *, ram: float | None, accel: float) -> None:
    monkeypatch.setattr(pull, "total_memory_gb", lambda: ram)
    monkeypatch.setattr(pull, "accelerator_gb", lambda: (accel, "nvidia-smi"))


async def test_a_big_machine_is_recommended_a_big_model(fake_tags, monkeypatch) -> None:
    _machine(monkeypatch, ram=128.0, accel=80.0)
    result = await pull.recommendations()
    chat = [m for m in result["models"] if m["role"] == "chat"]
    picked = [m for m in chat if m["recommended"]]
    assert len(picked) == 1, "exactly one pick per role"
    # The largest chat model that still fits comfortably — on 80 GB that is the
    # top of the list, which is the entire point of probing the hardware.
    assert picked[0]["size_gb"] == max(m["size_gb"] for m in chat)


async def test_a_small_machine_is_recommended_a_small_model(fake_tags, monkeypatch) -> None:
    _machine(monkeypatch, ram=8.0, accel=0.0)
    result = await pull.recommendations()
    chat = [m for m in result["models"] if m["role"] == "chat"]
    picked = next(m for m in chat if m["recommended"])
    assert picked["size_gb"] <= 4.0
    assert picked["fit"] == "comfortable"


async def test_a_machine_too_small_for_anything_still_gets_a_starting_point(
    fake_tags, monkeypatch
) -> None:
    """Four entries all flagged "tight" and no pick answers "so which one?"
    with silence. The smallest is marked instead."""
    _machine(monkeypatch, ram=2.0, accel=0.0)
    result = await pull.recommendations()
    chat = [m for m in result["models"] if m["role"] == "chat"]
    picked = next(m for m in chat if m["recommended"])
    assert picked["size_gb"] == min(m["size_gb"] for m in chat)


async def test_every_role_gets_its_own_pick(fake_tags, monkeypatch) -> None:
    """A chat model does not substitute for an embedder. Each role is a separate
    decision and gets a separate answer."""
    _machine(monkeypatch, ram=64.0, accel=24.0)
    result = await pull.recommendations()
    picked_roles = {m["role"] for m in result["models"] if m["recommended"]}
    assert picked_roles == set(result["roles"])


async def test_an_installed_role_stops_being_recommended(fake_tags, monkeypatch) -> None:
    """Re-recommending a different size over a choice the user already made is
    how a panel starts nagging."""
    _machine(monkeypatch, ram=64.0, accel=24.0)
    fake_tags.payload = {"models": [{"name": "qwen3.5:latest"}]}
    result = await pull.recommendations()
    chat = [m for m in result["models"] if m["role"] == "chat"]
    assert not any(m["recommended"] for m in chat)
    # Other roles are untouched — one installed model does not silence the rest.
    assert any(m["recommended"] for m in result["models"] if m["role"] == "vision")


async def test_real_registry_sizes_replace_the_estimates(fake_tags, monkeypatch) -> None:
    """The hardcoded estimates were up to a third off, which is enough to turn
    "fits in my card" into an eviction at load time."""
    _machine(monkeypatch, ram=64.0, accel=24.0)
    fake_tags.manifests = {"qwen3.5": {"layers": [{"size": 9_000_000_000}]}}
    result = await pull.recommendations()
    by_id = {m["id"]: m for m in result["models"]}
    assert by_id["qwen3.5"]["size_gb"] == 9.0
    # A model the registry did not answer for keeps its curated estimate rather
    # than vanishing or reporting zero.
    assert by_id["qwen3-embedding:4b"]["size_gb"] > 0


async def test_an_offline_registry_leaves_a_usable_panel(fake_tags, monkeypatch) -> None:
    _machine(monkeypatch, ram=32.0, accel=0.0)
    result = await pull.recommendations()
    assert all(m["size_gb"] > 0 for m in result["models"])
    assert any(m["recommended"] for m in result["models"])


async def test_the_payload_names_the_hardware_it_judged_against(fake_tags, monkeypatch) -> None:
    """A verdict the user cannot check against their own machine is a verdict
    they have to take on faith."""
    _machine(monkeypatch, ram=32.0, accel=16.0)
    result = await pull.recommendations()
    assert result["accelerator_gb"] == 16.0
    assert result["accelerator_source"] == "nvidia-smi"
    assert result["memory_gb"] == 32.0
