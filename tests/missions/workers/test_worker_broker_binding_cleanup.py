"""Mission worker grants are single-owner resources across every exit path."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import jarvis.claude_auth_state as claude_auth_state
import jarvis.codex_auth_state as codex_auth_state
import jarvis.codex_quota_state as codex_quota_state
import jarvis.missions.init as missions_init
import jarvis.missions.workers.claude_direct_worker as claude_module
import jarvis.missions.workers.codex_direct_worker as codex_module
import jarvis.missions.workers.google_cli_worker as google_module
from jarvis.google_cli.resolver import GoogleCli
from jarvis.missions.workers.api_agent_worker import ApiAgentWorker
from jarvis.missions.workers.claude_direct_worker import ClaudeDirectWorker
from jarvis.missions.workers.codex_direct_worker import CodexDirectWorker
from jarvis.missions.workers.gemini_worker import GeminiWorker
from jarvis.missions.workers.google_cli_worker import (
    BrokerMcpConfigurationError,
    GoogleCliWorker,
    _build_agy_worker_env,
)


class _CountingBinding:
    tool_specs: tuple[dict[str, object], ...] = ()
    tool_names: tuple[str, ...] = ("github/list_issues",)
    available = True

    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1

    def mcp_server_config(self) -> dict[str, dict[str, object]]:
        return {
            "jarvis_worker_tools": {
                "command": "python",
                "args": ["-m", "jarvis.missions.workers.broker_stdio"],
            }
        }

    def apply_environment(self, env: dict[str, str]) -> dict[str, str]:
        return dict(env)


class _CountingInventory:
    def __init__(self, binding: _CountingBinding) -> None:
        self.binding = binding
        self.bind_calls = 0

    def bind_broker(self, **_kwargs: Any) -> _CountingBinding:
        self.bind_calls += 1
        return self.binding

    def report_for(
        self, backend: str, *, binding: _CountingBinding | None = None
    ) -> dict[str, object]:
        return {
            "backend": backend,
            "broker": {"status": "available" if binding is not None else "unavailable"},
        }


class _FakeStream:
    def __init__(self, data: bytes = b"") -> None:
        self._data = data
        self._lines = data.splitlines(keepends=True)
        self._line_index = 0
        self._read = False

    async def read(self, _size: int = -1) -> bytes:
        if self._read:
            return b""
        self._read = True
        return self._data

    async def readline(self) -> bytes:
        if self._line_index >= len(self._lines):
            return b""
        line = self._lines[self._line_index]
        self._line_index += 1
        return line

    def write(self, _data: bytes) -> None:
        return None

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None


class _FakeProcess:
    def __init__(self, stdout: bytes, *, returncode: int) -> None:
        self.pid = 4242
        self.returncode = returncode
        self.stdin = _FakeStream()
        self.stdout = _FakeStream(stdout)
        self.stderr = _FakeStream()

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


class _Job:
    def assign(self, _pid: int) -> None:
        return None


WorkerFactory = Callable[[_CountingInventory], Any]


_WORKERS: tuple[tuple[str, WorkerFactory], ...] = (
    (
        "api",
        lambda inventory: ApiAgentWorker(
            "openai",
            capability_inventory=inventory,  # type: ignore[arg-type]
        ),
    ),
    (
        "claude",
        lambda inventory: ClaudeDirectWorker(
            capability_inventory=inventory  # type: ignore[arg-type]
        ),
    ),
    (
        "codex",
        lambda inventory: CodexDirectWorker(
            capability_inventory=inventory  # type: ignore[arg-type]
        ),
    ),
    (
        "gemini",
        lambda inventory: GeminiWorker(
            capability_inventory=inventory  # type: ignore[arg-type]
        ),
    ),
    (
        "google",
        lambda inventory: GoogleCliWorker(
            capability_inventory=inventory  # type: ignore[arg-type]
        ),
    ),
)


def _spawn(worker: Any, tmp_path: Path):  # noqa: ANN202
    return worker.spawn(
        "complete the mission",
        worktree=tmp_path,
        env={},
        job=object(),
        worker_id="mission::0",
        log_dir=tmp_path / "logs",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("_name,worker_factory", _WORKERS, ids=lambda value: str(value))
async def test_binding_closes_once_after_normal_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _name: str,
    worker_factory: WorkerFactory,
) -> None:
    binding = _CountingBinding()
    inventory = _CountingInventory(binding)
    worker = worker_factory(inventory)

    async def _normal(*_args: Any, **kwargs: Any):  # noqa: ANN202
        assert kwargs["broker_binding"] is binding
        yield "init"
        yield "result"

    monkeypatch.setattr(worker, "_spawn_bound", _normal)
    stream = _spawn(worker, tmp_path)
    assert [event async for event in stream] == ["init", "result"]
    await stream.aclose()

    assert inventory.bind_calls == 1
    assert binding.close_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("_name,worker_factory", _WORKERS, ids=lambda value: str(value))
async def test_binding_closes_once_after_empty_early_return(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _name: str,
    worker_factory: WorkerFactory,
) -> None:
    binding = _CountingBinding()
    worker = worker_factory(_CountingInventory(binding))

    async def _empty(*_args: Any, **_kwargs: Any):  # noqa: ANN202
        if False:
            yield None

    monkeypatch.setattr(worker, "_spawn_bound", _empty)
    assert [event async for event in _spawn(worker, tmp_path)] == []
    assert binding.close_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("_name,worker_factory", _WORKERS, ids=lambda value: str(value))
async def test_binding_closes_once_after_worker_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _name: str,
    worker_factory: WorkerFactory,
) -> None:
    binding = _CountingBinding()
    worker = worker_factory(_CountingInventory(binding))

    async def _broken(*_args: Any, **_kwargs: Any):  # noqa: ANN202
        if False:
            yield None
        raise RuntimeError("worker failed")

    monkeypatch.setattr(worker, "_spawn_bound", _broken)
    with pytest.raises(RuntimeError, match="worker failed"):
        _ = [event async for event in _spawn(worker, tmp_path)]
    assert binding.close_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("_name,worker_factory", _WORKERS, ids=lambda value: str(value))
async def test_binding_closes_once_when_consumer_closes_generator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _name: str,
    worker_factory: WorkerFactory,
) -> None:
    binding = _CountingBinding()
    worker = worker_factory(_CountingInventory(binding))

    async def _long_running(*_args: Any, **_kwargs: Any):  # noqa: ANN202
        yield "init"
        await asyncio.Event().wait()

    monkeypatch.setattr(worker, "_spawn_bound", _long_running)
    stream = _spawn(worker, tmp_path)
    assert await anext(stream) == "init"
    await stream.aclose()

    assert binding.close_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("_name,worker_factory", _WORKERS, ids=lambda value: str(value))
async def test_binding_closes_once_when_active_read_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _name: str,
    worker_factory: WorkerFactory,
) -> None:
    binding = _CountingBinding()
    worker = worker_factory(_CountingInventory(binding))
    waiting = asyncio.Event()

    async def _long_running(*_args: Any, **_kwargs: Any):  # noqa: ANN202
        yield "init"
        waiting.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(worker, "_spawn_bound", _long_running)
    stream = _spawn(worker, tmp_path)
    assert await anext(stream) == "init"
    read = asyncio.create_task(anext(stream))
    await waiting.wait()
    read.cancel()
    with pytest.raises(asyncio.CancelledError):
        await read
    await stream.aclose()

    assert binding.close_calls == 1


@pytest.mark.asyncio
async def test_google_to_gemini_fallback_reuses_outer_binding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binding = _CountingBinding()
    inventory = _CountingInventory(binding)
    monkeypatch.setattr(
        google_module,
        "resolve_google_cli",
        lambda: GoogleCli(kind="gemini", argv_prefix=["gemini"]),
    )
    seen: list[object] = []

    async def _gemini_bound(_self: Any, *_args: Any, **kwargs: Any):  # noqa: ANN202
        seen.append(kwargs["broker_binding"])
        yield "gemini-result"

    monkeypatch.setattr(GeminiWorker, "_spawn_bound", _gemini_bound)
    worker = GoogleCliWorker(capability_inventory=inventory)  # type: ignore[arg-type]

    assert [event async for event in _spawn(worker, tmp_path)] == ["gemini-result"]
    assert seen == [binding]
    assert inventory.bind_calls == 1
    assert binding.close_calls == 1


@pytest.mark.asyncio
async def test_claude_to_codex_fallback_reuses_outer_binding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binding = _CountingBinding()
    inventory = _CountingInventory(binding)
    auth_failure = (
        b'{"type":"result","subtype":"error_during_execution",'
        b'"is_error":true,"result":"Failed to authenticate. API Error: 401",'
        b'"session_id":"claude-dead"}\n'
    )

    async def _claude_process(*_args: Any, **_kwargs: Any) -> _FakeProcess:
        return _FakeProcess(auth_failure, returncode=1)

    seen: list[object] = []

    async def _codex_bound(_self: Any, *_args: Any, **kwargs: Any):  # noqa: ANN202
        seen.append(kwargs["broker_binding"])
        yield "codex-result"

    monkeypatch.setattr(claude_module, "create_worker_subprocess", _claude_process)
    monkeypatch.setattr(claude_module, "_resolve_claude_argv_prefix", lambda: ["claude"])
    monkeypatch.setattr(claude_module, "_resolve_provider_chain", lambda: ())
    monkeypatch.setattr(codex_module, "_codex_oauth_available", lambda: True)
    monkeypatch.setattr(codex_auth_state, "codex_needs_reauth", lambda: False)
    monkeypatch.setattr(codex_quota_state, "codex_in_quota_cooldown", lambda: False)
    monkeypatch.setattr(claude_auth_state, "mark_claude_auth_dead", lambda **_kwargs: None)
    monkeypatch.setattr(CodexDirectWorker, "_spawn_bound", _codex_bound)

    worker = ClaudeDirectWorker(capability_inventory=inventory)  # type: ignore[arg-type]
    events = [
        event
        async for event in worker.spawn(
            "complete the mission",
            worktree=tmp_path,
            env={"CLAUDE_CODE_OAUTH_TOKEN": "test-token"},
            job=_Job(),
            worker_id="mission::0",
            log_dir=tmp_path / "logs",
            timeout_s=5.0,
            first_output_timeout_s=5.0,
        )
    ]

    assert events[-1] == "codex-result"
    assert seen == [binding]
    assert inventory.bind_calls == 1
    assert binding.close_calls == 1


@pytest.mark.asyncio
async def test_codex_to_claude_fallback_reuses_outer_binding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binding = _CountingBinding()
    inventory = _CountingInventory(binding)
    auth_failure = b'{"type":"error","message":"Failed to refresh token. Please log in again."}\n'

    async def _codex_process(*_args: Any, **_kwargs: Any) -> _FakeProcess:
        return _FakeProcess(auth_failure, returncode=1)

    seen: list[object] = []

    async def _claude_bound(_self: Any, *_args: Any, **kwargs: Any):  # noqa: ANN202
        seen.append(kwargs["broker_binding"])
        yield "claude-result"

    monkeypatch.setattr(codex_module, "create_worker_subprocess", _codex_process)
    monkeypatch.setattr(claude_module, "_resolve_claude_binary", lambda: "claude")
    monkeypatch.setattr(missions_init, "_claude_cli_auth_viable", lambda: True)
    monkeypatch.setattr(codex_auth_state, "mark_codex_needs_reauth", lambda: None)
    monkeypatch.setattr(ClaudeDirectWorker, "_spawn_bound", _claude_bound)

    worker = CodexDirectWorker(capability_inventory=inventory)  # type: ignore[arg-type]
    events = [
        event
        async for event in worker.spawn(
            "complete the mission",
            worktree=tmp_path,
            env={},
            job=_Job(),
            worker_id="mission::0",
            log_dir=tmp_path / "logs",
            timeout_s=5.0,
            first_output_timeout_s=5.0,
        )
    ]

    assert events[-1] == "claude-result"
    assert seen == [binding]
    assert inventory.bind_calls == 1
    assert binding.close_calls == 1


def test_agy_mcp_settings_failure_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        google_module, "ensure_isolated_home", lambda **_kwargs: str(tmp_path / "missing")
    )
    with pytest.raises(BrokerMcpConfigurationError, match="could not be written"):
        _build_agy_worker_env(
            {},
            mcp_servers={"jarvis_worker_tools": {"command": "python", "args": []}},
        )


@pytest.mark.asyncio
async def test_agy_reports_broker_unavailable_when_mcp_injection_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binding = _CountingBinding()
    inventory = _CountingInventory(binding)
    monkeypatch.setattr(
        google_module,
        "resolve_google_cli",
        lambda: GoogleCli(kind="agy", argv_prefix=["agy.exe"]),
    )
    monkeypatch.setattr(google_module, "_oauth_login_present", lambda *_args: True)
    monkeypatch.setattr(
        google_module, "ensure_isolated_home", lambda **_kwargs: str(tmp_path / "missing")
    )
    monkeypatch.setattr(
        google_module,
        "run_cli_over_pty",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("agy must not start without its broker MCP configuration")
        ),
    )

    events = [
        event
        async for event in _spawn(
            GoogleCliWorker(capability_inventory=inventory),  # type: ignore[arg-type]
            tmp_path,
        )
    ]

    assert events[0].external_capabilities["broker"]["status"] == "unavailable"
    assert events[-1].is_error is True
    assert events[-1].result.startswith("GoogleCliWorker broker MCP configuration failed:")
    assert inventory.bind_calls == 1
    assert binding.close_calls == 1
