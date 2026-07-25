"""Every mission worker honors the orchestrator's ``timeout_s`` budget.

The Kontrollierer allocates a degressive per-iteration time budget (720 s for
iteration 0, 360 s for corrections) and passes it to ``worker.spawn`` as
``timeout_s``. A worker that silently swallows the kwarg into ``**_unused``
runs on its own 1200 s module constant instead, overshooting the 1380 s task
budget — a correction iteration then burns 20 minutes before the orchestrator
can grade the diff (live case: antigravity corrections ran 1200 s instead of
360 s while ``[brain.worker]`` pointed at the Google CLI worker).

These tests pin the contract for ALL worker backends: the parameter must be
declared (never ``**_unused``-swallowed) and must reach the actual enforcement
point (``asyncio.wait_for`` deadline, PTY timeout, agent-loop deadline).
"""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any

import pytest

import jarvis.core.config as config_module
import jarvis.missions.workers.api_agent_worker as api_module
import jarvis.missions.workers.gemini_worker as gemini_module
import jarvis.missions.workers.google_cli_worker as google_module
from jarvis.google_cli.pty_runner import PtyRunResult
from jarvis.google_cli.resolver import GoogleCli
from jarvis.missions.workers.api_agent_worker import ApiAgentWorker
from jarvis.missions.workers.claude_direct_worker import ClaudeDirectWorker
from jarvis.missions.workers.codex_direct_worker import CodexDirectWorker
from jarvis.missions.workers.gemini_worker import GeminiWorker
from jarvis.missions.workers.google_cli_worker import (
    GoogleCliWorker,
    _build_agy_worker_argv,
)


class _StubBinding:
    tool_specs: tuple[dict[str, object], ...] = ()

    def close(self) -> None:
        return None

    def mcp_server_config(self) -> dict[str, dict[str, object]]:
        return {}

    def apply_environment(self, env: dict[str, str]) -> dict[str, str]:
        return dict(env)


class _RecordingInventory:
    """Records the broker TTL a worker requests for its self-issued grant."""

    def __init__(self) -> None:
        self.ttl_s: float | None = None

    def bind_broker(self, *, ttl_s: float, **_kwargs: Any) -> _StubBinding:
        self.ttl_s = ttl_s
        return _StubBinding()

    def report_for(self, backend: str, *, binding: Any = None) -> dict[str, object]:
        return {"backend": backend}


class _Job:
    def assign(self, _pid: int) -> None:
        return None


_WORKER_CLASSES = (
    ApiAgentWorker,
    ClaudeDirectWorker,
    CodexDirectWorker,
    GeminiWorker,
    GoogleCliWorker,
)


@pytest.mark.parametrize("worker_cls", _WORKER_CLASSES, ids=lambda c: c.__name__)
def test_spawn_declares_timeout_s(worker_cls: type) -> None:
    """``timeout_s`` must be a declared parameter on every backend — a
    ``**_unused`` catch-all silently discards the orchestrator's budget."""
    for method_name in ("spawn", "_spawn_bound"):
        params = inspect.signature(getattr(worker_cls, method_name)).parameters
        assert "timeout_s" in params, (
            f"{worker_cls.__name__}.{method_name} swallows timeout_s into **kwargs"
        )


@pytest.mark.asyncio
async def test_gemini_worker_enforces_timeout_s(monkeypatch, tmp_path) -> None:
    """A hanging Gemini CLI is killed after ``timeout_s``, not after the
    1200 s module constant, and the synthetic stderr keeps the load-bearing
    "timeout" substring the orchestrator classifies ``is_timeout`` from."""

    class _HangingProcess:
        pid = 4242

        def __init__(self) -> None:
            self.returncode: int | None = None

        async def communicate(self) -> tuple[bytes, bytes]:
            await asyncio.sleep(30.0)
            return b"", b""

        def kill(self) -> None:
            self.returncode = -9

        async def wait(self) -> int:
            return self.returncode if self.returncode is not None else -9

    async def _hanging(*_args: Any, **_kwargs: Any) -> _HangingProcess:
        return _HangingProcess()

    monkeypatch.setattr(gemini_module, "create_worker_subprocess", _hanging)
    inventory = _RecordingInventory()
    worker = GeminiWorker(capability_inventory=inventory)  # type: ignore[arg-type]
    t0 = time.perf_counter()
    events = [
        e
        async for e in worker.spawn(
            "build it",
            worktree=tmp_path,
            env={},
            job=_Job(),
            worker_id="w",
            log_dir=tmp_path / "logs",
            timeout_s=0.2,
        )
    ]
    elapsed = time.perf_counter() - t0
    assert elapsed < 10.0, "worker ignored timeout_s and ran on its module constant"
    result = events[-1]
    assert result.is_error is True
    assert "timeout" in result.result
    assert inventory.ttl_s == pytest.approx(0.2 + 60.0)


@pytest.mark.asyncio
async def test_gemini_quota_fallback_shares_one_deadline(monkeypatch, tmp_path) -> None:
    """The Pro→Flash quota retry runs on the REMAINING budget — one iteration
    must never burn 2 × timeout_s across the two attempts."""
    spawn_times: list[float] = []

    class _QuotaProcess:
        pid = 1

        def __init__(self) -> None:
            self.returncode: int | None = None

        async def communicate(self) -> tuple[bytes, bytes]:
            self.returncode = 1
            return b"", b"code: 429 QUOTA_EXHAUSTED"

        def kill(self) -> None:
            self.returncode = -9

        async def wait(self) -> int:
            return -9

    class _HangingProcess:
        pid = 2

        def __init__(self) -> None:
            self.returncode: int | None = None

        async def communicate(self) -> tuple[bytes, bytes]:
            await asyncio.sleep(30.0)
            return b"", b""

        def kill(self) -> None:
            self.returncode = -9

        async def wait(self) -> int:
            return -9

    procs: list[Any] = [_QuotaProcess(), _HangingProcess()]

    async def _next_proc(*_args: Any, **_kwargs: Any) -> Any:
        spawn_times.append(time.perf_counter())
        return procs.pop(0)

    monkeypatch.setattr(gemini_module, "create_worker_subprocess", _next_proc)
    worker = GeminiWorker(capability_inventory=_RecordingInventory())  # type: ignore[arg-type]
    t0 = time.perf_counter()
    events = [
        e
        async for e in worker.spawn(
            "build it",
            worktree=tmp_path,
            env={},
            job=_Job(),
            worker_id="w",
            log_dir=tmp_path / "logs",
            model="gemini-3-pro-preview",
            timeout_s=1.0,
        )
    ]
    elapsed = time.perf_counter() - t0
    assert len(spawn_times) == 2, "quota fallback was not attempted"
    assert elapsed < 10.0, "fallback attempt got a fresh budget instead of the remainder"
    assert events[-1].is_error is True


@pytest.mark.asyncio
async def test_google_cli_worker_passes_timeout_to_pty_and_agy(monkeypatch, tmp_path) -> None:
    """agy path: the PTY hard cap IS the orchestrator budget, and agy's own
    ``--print-timeout`` is derived slightly below it so agy flushes its final
    output before the external kill."""
    monkeypatch.setattr(
        google_module, "resolve_google_cli", lambda: GoogleCli(kind="agy", argv_prefix=["agy.exe"])
    )
    monkeypatch.setattr(google_module, "ensure_isolated_home", lambda **_k: str(tmp_path / "iso"))
    monkeypatch.setattr(google_module, "_oauth_login_present", lambda *_a: True)
    captured: dict[str, Any] = {}

    def _fake_run(argv, *, timeout_s, cwd=None, env=None, on_spawn=None, **_kw):
        captured["argv"] = list(argv)
        captured["timeout_s"] = timeout_s
        return PtyRunResult(text="ok", raw="raw", exit_status=0, timed_out=False, error=None)

    monkeypatch.setattr(google_module, "run_cli_over_pty", _fake_run)
    inventory = _RecordingInventory()
    worker = GoogleCliWorker(capability_inventory=inventory)  # type: ignore[arg-type]
    _ = [
        e
        async for e in worker.spawn(
            "build it",
            worktree=tmp_path,
            env={"PATH": "/p"},
            job=_Job(),
            worker_id="w",
            log_dir=tmp_path / "logs",
            timeout_s=360.0,
        )
    ]
    assert captured["timeout_s"] == 360.0
    argv = captured["argv"]
    assert argv[argv.index("--print-timeout") + 1] == "330s"
    assert inventory.ttl_s == pytest.approx(360.0 + 60.0)


def test_agy_print_timeout_never_drops_below_floor(tmp_path) -> None:
    """Tiny budgets clamp agy's self-timeout to a workable 30 s floor instead
    of a nonsensical zero/negative value; the PTY cap stays the enforcement."""
    argv = _build_agy_worker_argv("agy.exe", "p", tmp_path, timeout_s=45.0)
    assert argv[argv.index("--print-timeout") + 1] == "30s"


@pytest.mark.asyncio
async def test_api_agent_worker_enforces_timeout_s(monkeypatch, tmp_path) -> None:
    """The in-process agent loop checks the PASSED budget, not the module
    constant — an exhausted budget yields a timed-out result before the
    next brain turn starts."""

    class _NeverCalledBrain:
        def can_call_tools(self) -> bool:
            return True

        async def complete(self, _req: Any):
            raise AssertionError("brain must not run once the budget is exhausted")
            yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(api_module, "_build_brain", lambda *_a, **_k: _NeverCalledBrain())
    monkeypatch.setattr(config_module, "get_jarvis_agent_secret", lambda *_a, **_k: "key")
    inventory = _RecordingInventory()
    worker = ApiAgentWorker("openai", capability_inventory=inventory)  # type: ignore[arg-type]
    events = [
        e
        async for e in worker.spawn(
            "build it",
            worktree=tmp_path,
            env={},
            job=_Job(),
            worker_id="w",
            log_dir=tmp_path / "logs",
            timeout_s=0.0,
        )
    ]
    result = events[-1]
    assert result.timed_out is True
    assert "timeout" in result.result
    assert inventory.ttl_s == pytest.approx(60.0)
