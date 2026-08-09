"""Unit tests for the persistent Codex app-server JSONL transport.

Every subprocess, pipe, auth result, and process-tree container is a local fake.
The suite never reads a Codex auth file, starts the CLI, or touches the network.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import jarvis.codex_app_server as transport
from jarvis.codex_app_server import (
    CodexAppServerCapability,
    CodexAppServerClient,
    CodexAppServerDisconnected,
    CodexAppServerError,
    CodexAppServerRPCError,
    CodexAppServerTimeout,
    CodexNotificationOverflow,
    CodexSubscriptionUnavailable,
)

# Captured before the autouse fixture stubs it out, so the dedicated test can
# exercise the real pre-spawn verification.
_REAL_VERIFY_SPAWN_BINARY = transport._verify_spawn_binary


class FakeProcessTree:
    supports_containment = True

    def __init__(self) -> None:
        self.assigned: list[int] = []
        self.closed = False

    def assign(self, pid: int) -> None:
        self.assigned.append(pid)

    def close(self) -> None:
        self.closed = True


class FakeProfileProcessLock:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def fileno(self) -> int:
        if self.closed:
            raise ValueError("closed")
        return 91


@pytest.fixture(autouse=True)
def isolated_profile_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        transport,
        "_acquire_subscription_process_lock",
        FakeProfileProcessLock,
    )
    monkeypatch.setattr(transport, "_subscription_login_in_flight", False)
    monkeypatch.setattr(transport, "_subscription_login_process", None)
    monkeypatch.setattr(transport, "_subscription_profile_mutating", False)
    monkeypatch.setattr(transport, "_subscription_active_transports", set())
    monkeypatch.setattr(transport, "_subscription_transport_owner", None)
    monkeypatch.setattr(transport, "_subscription_transport_process_lock", None)
    monkeypatch.setattr(transport, "_subscription_mutation_process_lock", None)
    monkeypatch.setattr(transport, "_subscription_snapshot_cache", None)
    monkeypatch.setattr(transport, "_subscription_snapshot_cache_key", "")
    monkeypatch.setattr(transport, "_subscription_snapshot_cache_at", 0.0)
    monkeypatch.setattr(transport, "_subscription_status_probe_active", False)
    monkeypatch.setattr(transport, "_sha256_cache", {})
    monkeypatch.setattr(transport, "_subscription_snapshot_cache_is_failure", False)
    monkeypatch.setattr(transport, "_subscription_transport_epoch", 0)
    monkeypatch.setattr(transport, "_subscription_activation_block", None)
    # The pre-spawn re-hash needs a real approved file; the suite's fake
    # binaries have none. The dedicated test exercises the real check.
    monkeypatch.setattr(transport, "_verify_spawn_binary", lambda _path: None)


class FakeStdin:
    def __init__(self, process: FakeProcess) -> None:
        self.process = process
        self.messages: list[dict[str, Any]] = []
        self.closed = False
        self.drain_calls = 0

    def write(self, payload: bytes) -> None:
        assert payload.endswith(b"\n")
        for line in payload.splitlines():
            message = json.loads(line.decode("utf-8"))
            self.messages.append(message)
            self.process.on_client_message(message)

    async def drain(self) -> None:
        self.drain_calls += 1
        await asyncio.sleep(0)

    def close(self) -> None:
        self.closed = True
        self.process.finish(0)


class FakeProcess:
    _next_pid = 4100

    def __init__(
        self,
        responder: Callable[[FakeProcess, dict[str, Any]], None] | None = None,
        config_result: dict[str, Any] | None = None,
    ) -> None:
        type(self)._next_pid += 1
        self.pid = type(self)._next_pid
        self.returncode: int | None = None
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdin = FakeStdin(self)
        self._waited = asyncio.Event()
        self._responder = responder or _default_responder
        self._config_result = config_result or {
            "config": {},
            "layers": [{"name": "sessionFlags", "config": {}}],
        }

    def on_client_message(self, message: dict[str, Any]) -> None:
        if message.get("method") == "config/read" and isinstance(
            message.get("id"), int
        ):
            _respond(self, message["id"], self._config_result)
            return
        if message.get("method") == "configRequirements/read" and isinstance(
            message.get("id"), int
        ):
            _respond(self, message["id"], {"requirements": None})
            return
        self._responder(self, message)

    def emit(self, message: dict[str, Any]) -> None:
        self.stdout.feed_data(json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n")

    def emit_stderr(self, text: str) -> None:
        self.stderr.feed_data(text.encode("utf-8") + b"\n")

    def finish(self, returncode: int) -> None:
        if self.returncode is not None:
            return
        self.returncode = returncode
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self._waited.set()

    def terminate(self) -> None:
        self.finish(-15)

    def kill(self) -> None:
        self.finish(-9)

    async def wait(self) -> int:
        await self._waited.wait()
        assert self.returncode is not None
        return self.returncode


def _respond(process: FakeProcess, request_id: int, result: Any) -> None:
    asyncio.get_running_loop().call_soon(process.emit, {"id": request_id, "result": result})


def _respond_handshake(process: FakeProcess, message: dict[str, Any]) -> bool:
    request_id = message.get("id")
    if not isinstance(request_id, int):
        return False
    if message.get("method") == "initialize":
        _respond(process, request_id, {"userAgent": "fake"})
        return True
    if message.get("method") == "account/read":
        _respond(
            process,
            request_id,
            {
                "account": {
                    "type": "chatgpt",
                    "email": "not-consumed@example.invalid",
                    "planType": "plus",
                },
                "requiresOpenaiAuth": True,
            },
        )
        return True
    return False


def _default_responder(process: FakeProcess, message: dict[str, Any]) -> None:
    if _respond_handshake(process, message):
        return
    request_id = message.get("id")
    if not isinstance(request_id, int):
        return
    method = message.get("method")
    if method == "thread/start":
        _respond(process, request_id, {"thread": {"id": "thread-1"}})
    else:
        _respond(process, request_id, {})


@pytest.mark.asyncio
async def test_text_transport_uses_stable_app_server_without_realtime_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = SpawnHarness(monkeypatch)
    client = CodexAppServerClient(purpose="text")

    await client.ensure_started()

    argv, _kwargs = harness.calls[0]
    argv_text = "\n".join(str(value) for value in argv)
    assert "--disable\nrealtime_conversation" in argv_text
    assert "--enable\nrealtime_conversation" not in argv_text
    assert "experimental_realtime_" not in argv_text
    assert f'model_provider="{transport._TEXT_MODEL_PROVIDER}"' in argv
    assert client._sink_server is None
    initialize = harness.processes[0].stdin.messages[0]
    assert initialize["params"]["capabilities"]["experimentalApi"] is False

    await client.close()


@pytest.mark.asyncio
async def test_text_thread_is_ephemeral_read_only_and_uses_managed_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = SpawnHarness(monkeypatch)
    client = CodexAppServerClient(purpose="text")
    monkeypatch.setattr(client, "_audit_text_thread_start_response", lambda *_a, **_k: None)

    result = await client.text_thread_start()

    assert result == {"thread": {"id": "thread-1"}}
    params = next(
        message["params"]
        for message in harness.processes[0].stdin.messages
        if message.get("method") == "thread/start"
    )
    assert params["modelProvider"] == transport._TEXT_MODEL_PROVIDER
    assert "model" not in params
    assert params["ephemeral"] is True
    assert params["sandbox"] == "read-only"
    assert params["dynamicTools"] is None
    assert params["runtimeWorkspaceRoots"] == []
    assert params["config"]["features"]["realtime_conversation"] is False

    await client.close()


class SpawnHarness:
    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        responders: list[Callable[[FakeProcess, dict[str, Any]], None] | None] | None = None,
        probe_responders: list[
            Callable[[FakeProcess, dict[str, Any]], None] | None
        ]
        | None = None,
        *,
        audit_effective_config: bool = False,
        mcp_ids: tuple[str, ...] = (),
    ) -> None:
        self.all_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.all_processes: list[FakeProcess] = []
        self.processes: list[FakeProcess] = []
        self.probe_processes: list[FakeProcess] = []
        self.all_trees: list[FakeProcessTree] = []
        self.trees: list[FakeProcessTree] = []
        self.probe_trees: list[FakeProcessTree] = []
        self._responders = list(responders or [])
        self._probe_responders = list(probe_responders or [])
        self._pending_tree: FakeProcessTree | None = None
        self._spawn_count = 0
        self.codex_home = Path("C:/isolated-jarvis-codex-voice-test")

        capability = CodexAppServerCapability(
            available=True,
            chatgpt_authenticated=True,
            binary_path="codex-test",
            version="codex-test 1.0",
            reason="ready",
        )
        monkeypatch.setattr(transport, "_read_codex_capability", lambda _binary: capability)

        def make_tree(_name: str) -> FakeProcessTree:
            tree = FakeProcessTree()
            self.all_trees.append(tree)
            self._pending_tree = tree
            return tree

        async def spawn(*args: Any, **kwargs: Any) -> FakeProcess:
            self._spawn_count += 1
            self.all_calls.append((args, kwargs))
            responder = self._responders.pop(0) if self._responders else None
            session_config: dict[str, Any] = {}
            user_config = {
                "mcp_servers": {
                    server_id: {"enabled": True} for server_id in mcp_ids
                }
            }
            process = FakeProcess(
                responder,
                config_result={
                    "config": {},
                    "layers": [
                        {"name": "sessionFlags", "config": session_config},
                        {"name": "user", "config": user_config},
                    ],
                },
            )
            self.all_processes.append(process)
            tree = self._pending_tree
            assert tree is not None
            self._pending_tree = None
            self.calls.append((args, kwargs))
            self.processes.append(process)
            self.trees.append(tree)
            return process

        monkeypatch.setattr(transport, "make_process_tree", make_tree)
        monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(
            transport,
            "_validated_subscription_home",
            lambda **_kwargs: self.codex_home,
        )
        if not audit_effective_config:
            monkeypatch.setattr(
                CodexAppServerClient,
                "_audit_effective_config",
                lambda *_args, **_kwargs: None,
            )
            monkeypatch.setattr(
                CodexAppServerClient,
                "_audit_thread_start_response",
                lambda *_args, **_kwargs: None,
            )


@pytest.mark.asyncio
async def test_lazy_start_handshake_scrubs_api_billing_environment_and_reaps_tree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    selected_home = tmp_path / "selected-codex-account"
    monkeypatch.setenv("CODEX_HOME", str(selected_home))
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-child")
    monkeypatch.setenv("CODEX_API_KEY", "must-not-reach-child")
    monkeypatch.setenv("CODEX_OPENAI_API_KEY", "must-not-reach-child")
    monkeypatch.setenv("JARVIS_REALTIME_OPENAI_API_KEY", "must-not-reach-child")
    monkeypatch.setenv("JARVIS_AGENT_OPENAI_API_KEY", "must-not-reach-child")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-reach-child")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-reach-child")
    monkeypatch.setenv("HTTPS_PROXY", "http://must-not-reach-child.invalid")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/test/bus")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/test")
    harness = SpawnHarness(monkeypatch)
    client = CodexAppServerClient()

    assert harness.calls == []
    assert client.running is False

    await client.ensure_started()

    assert len(harness.calls) == 1
    assert client.ready is True
    argv, kwargs = harness.calls[0]
    assert argv[:5] == (
        "codex-test",
        "app-server",
        "--strict-config",
        "--enable",
        "realtime_conversation",
    )
    argv_text = "\n".join(str(value) for value in argv)
    assert "skills.include_instructions=false" in argv_text
    assert "skills.bundled.enabled=false" in argv_text
    disabled_features = {
        argv[index + 1]
        for index, value in enumerate(argv[:-1])
        if value == "--disable"
    }
    assert "memories" in disabled_features
    assert "memories.use_memories=false" in argv_text
    assert 'cli_auth_credentials_store="file"' in argv
    assert 'experimental_realtime_ws_base_url="https://api.openai.com/v1"' in argv
    assert (
        'experimental_realtime_webrtc_call_base_url='
        '"https://chatgpt.com/backend-api/codex"'
    ) in argv
    child_env = kwargs["env"]
    for name in transport._OPENAI_BILLING_ENV_NAMES:
        assert name not in child_env
    for name in ("ANTHROPIC_API_KEY", "AWS_SECRET_ACCESS_KEY", "HTTPS_PROXY"):
        assert name not in child_env
    assert child_env["CODEX_HOME"] == str(harness.codex_home)
    assert child_env["CODEX_HOME"] != str(selected_home)
    assert "DBUS_SESSION_BUS_ADDRESS" not in child_env
    assert "XDG_RUNTIME_DIR" not in child_env
    assert child_env["RUST_LOG"] == "warn"
    assert child_env["RUST_BACKTRACE"] == "0"
    assert child_env["CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED"] == "1"
    # Windows-only by construction: NO_WINDOW_CREATIONFLAGS is 0 elsewhere, so
    # asserting the bit off Windows only ever asserted 0 and failed there.
    if sys.platform == "win32":
        assert kwargs["creationflags"] & transport.NO_WINDOW_CREATIONFLAGS
    else:
        assert kwargs["creationflags"] == 0

    process = harness.processes[0]
    assert process.stdin.messages[0]["method"] == "initialize"
    assert process.stdin.messages[0]["params"]["capabilities"] == {
        "experimentalApi": True,
        "mcpServerOpenaiFormElicitation": False,
        "requestAttestation": False,
    }
    assert process.stdin.messages[1] == {"method": "initialized"}
    assert process.stdin.messages[2]["method"] == "account/read"
    assert process.stdin.messages[2]["params"] == {"refreshToken": False}
    assert process.stdin.messages[3]["method"] == "configRequirements/read"
    assert process.stdin.messages[4]["method"] == "config/read"
    assert harness.probe_processes == []
    assert harness.trees[0].assigned == [process.pid]

    await client.close()

    assert harness.trees[0].closed is True
    assert process.returncode is not None


@pytest.mark.asyncio
async def test_posix_child_runs_behind_parent_lifeline_and_inherits_profile_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(transport.sys, "platform", "linux")
    harness = SpawnHarness(monkeypatch)
    client = CodexAppServerClient()

    await client.ensure_started()

    argv, kwargs = harness.calls[0]
    assert argv[0:2] == (sys.executable, "-I")
    assert Path(argv[2]).name == "child_lifeline.py"
    separator = argv.index("--")
    assert argv[separator + 1 : separator + 6] == (
        "codex-test",
        "app-server",
        "--strict-config",
        "--enable",
        "realtime_conversation",
    )
    assert kwargs["start_new_session"] is True
    assert kwargs["pass_fds"][0] == 91
    assert len(kwargs["pass_fds"]) == 2
    # The supervisor spawns the real child with close_fds, so the profile lock
    # only reaches app-server when it is re-declared in the lifeline argv.
    assert argv[4:6] == ("--keep-fd", "91")
    assert argv[separator - 1] == "91"
    assert client._lifeline_write_fd is not None
    await client.close()
    assert client._lifeline_write_fd is None


@pytest.mark.asyncio
async def test_posix_lifeline_setup_failure_closes_both_pipe_descriptors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(transport.sys, "platform", "linux")
    harness = SpawnHarness(monkeypatch)
    closed: list[int] = []
    monkeypatch.setattr(transport.os, "pipe", lambda: (80, 81))
    monkeypatch.setattr(transport.os, "close", closed.append)

    def refuse_inheritable(*_args: object) -> None:
        raise OSError("inheritable flag refused")

    monkeypatch.setattr(transport.os, "set_inheritable", refuse_inheritable)
    client = CodexAppServerClient()

    with pytest.raises(OSError, match="inheritable flag refused"):
        await client.ensure_started()

    assert closed == [80, 81]
    assert harness.calls == []
    assert harness.all_trees[0].closed is True


@pytest.mark.asyncio
async def test_posix_profile_lock_failure_creates_no_pipe_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lock is resolved BEFORE the pipe, because its descriptors have to
    reach the lifeline argv — so a lock failure has nothing to leak."""
    monkeypatch.setattr(transport.sys, "platform", "linux")
    harness = SpawnHarness(monkeypatch)
    pipes = 0

    def count_pipe() -> tuple[int, int]:
        nonlocal pipes
        pipes += 1
        return (80, 81)

    monkeypatch.setattr(transport.os, "pipe", count_pipe)

    def unavailable_lock(_client: CodexAppServerClient) -> tuple[int, ...]:
        raise CodexSubscriptionUnavailable("profile lock unavailable")

    monkeypatch.setattr(transport, "_subscription_transport_pass_fds", unavailable_lock)
    client = CodexAppServerClient()

    with pytest.raises(CodexSubscriptionUnavailable, match="profile lock unavailable"):
        await client.ensure_started()

    assert pipes == 0
    assert harness.calls == []
    assert harness.all_trees[0].closed is True


@pytest.mark.asyncio
async def test_start_refuses_missing_process_containment_before_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = SpawnHarness(monkeypatch)
    tree = FakeProcessTree()
    tree.supports_containment = False
    monkeypatch.setattr(transport, "make_process_tree", lambda _name: tree)

    async def unexpected_spawn(*_args: Any, **_kwargs: Any) -> FakeProcess:
        pytest.fail("an uncontained Codex process must never be spawned")

    monkeypatch.setattr(
        transport.asyncio,
        "create_subprocess_exec",
        unexpected_spawn,
    )
    client = CodexAppServerClient()

    with pytest.raises(CodexSubscriptionUnavailable, match="containment is unavailable"):
        await client.ensure_started()

    assert harness.all_calls == []
    assert tree.closed is True
    assert client.running is False


@pytest.mark.asyncio
async def test_containment_assignment_failure_kills_child_and_closes_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = SpawnHarness(monkeypatch)

    class FailingAssignmentTree(FakeProcessTree):
        def assign(self, pid: int) -> None:
            super().assign(pid)
            raise OSError("job assignment failed")

    tree = FailingAssignmentTree()

    def make_tree(_name: str) -> FakeProcessTree:
        harness.all_trees.append(tree)
        harness._pending_tree = tree
        return tree

    monkeypatch.setattr(transport, "make_process_tree", make_tree)
    client = CodexAppServerClient()

    with pytest.raises(
        CodexSubscriptionUnavailable,
        match="containment could not be established",
    ):
        await client.ensure_started()

    assert len(harness.all_processes) == 1
    assert tree.assigned == [harness.all_processes[0].pid]
    assert tree.closed is True
    assert harness.all_processes[0].returncode == -9
    assert client.running is False


@pytest.mark.asyncio
async def test_server_reported_api_key_account_fails_before_any_work_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def responder(process: FakeProcess, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        if not isinstance(request_id, int):
            return
        if message.get("method") == "initialize":
            _respond(process, request_id, {})
        elif message.get("method") == "account/read":
            _respond(
                process,
                request_id,
                {
                    "account": {"type": "apiKey"},
                    "requiresOpenaiAuth": True,
                },
            )

    harness = SpawnHarness(monkeypatch, [responder])
    client = CodexAppServerClient()

    with pytest.raises(CodexSubscriptionUnavailable):
        await client.request("thread/start", {})

    methods = [item.get("method") for item in harness.processes[0].stdin.messages]
    assert methods == ["initialize", "initialized", "account/read"]
    assert harness.trees[0].closed is True
    assert client.running is False


@pytest.mark.asyncio
async def test_simultaneous_first_requests_wait_for_one_complete_handshake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_seen = asyncio.Event()
    initialize_request: list[tuple[FakeProcess, int]] = []

    def responder(process: FakeProcess, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        if not isinstance(request_id, int):
            return
        method = message.get("method")
        if method == "initialize":
            initialize_request.append((process, request_id))
            initialize_seen.set()
        elif method == "account/read":
            _respond(
                process,
                request_id,
                {
                    "account": {"type": "chatgpt", "email": None, "planType": "plus"},
                    "requiresOpenaiAuth": True,
                },
            )
        elif method in {"test/first", "test/second"}:
            _respond(process, request_id, {"method": method})

    harness = SpawnHarness(monkeypatch, [responder])
    client = CodexAppServerClient()
    first = asyncio.create_task(client.request("test/first"))
    second = asyncio.create_task(client.request("test/second"))

    await asyncio.wait_for(initialize_seen.wait(), timeout=1.0)
    await asyncio.sleep(0)
    process = harness.processes[0]
    assert client.running is True
    assert client.ready is False
    assert [item["method"] for item in process.stdin.messages] == ["initialize"]

    init_process, init_id = initialize_request[0]
    init_process.emit({"id": init_id, "result": {}})
    first_result, second_result = await asyncio.gather(first, second)

    assert first_result == {"method": "test/first"}
    assert second_result == {"method": "test/second"}
    methods = [item["method"] for item in process.stdin.messages]
    assert methods.count("initialize") == 1
    assert methods[:3] == ["initialize", "initialized", "account/read"]
    assert methods[3:5] == ["configRequirements/read", "config/read"]
    assert set(methods[5:]) == {"test/first", "test/second"}
    assert len(harness.processes) == 1
    assert client.ready is True
    await client.close()


@pytest.mark.asyncio
async def test_concurrent_requests_route_out_of_order_responses_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    held_requests: list[tuple[FakeProcess, dict[str, Any]]] = []
    requests_ready = asyncio.Event()

    def responder(process: FakeProcess, message: dict[str, Any]) -> None:
        if _respond_handshake(process, message):
            return
        if "id" in message:
            held_requests.append((process, message))
            if len(held_requests) >= 2:
                requests_ready.set()

    SpawnHarness(monkeypatch, [responder])
    client = CodexAppServerClient()
    await client.ensure_started()

    first = asyncio.create_task(client.request("test/first"))
    second = asyncio.create_task(client.request("test/second"))
    await asyncio.wait_for(requests_ready.wait(), timeout=1.0)

    second_process, second_message = held_requests[1]
    first_process, first_message = held_requests[0]
    second_process.emit({"id": second_message["id"], "result": {"order": 2}})
    first_process.emit({"id": first_message["id"], "result": {"order": 1}})

    assert await first == {"order": 1}
    assert await second == {"order": 2}
    await client.close()


@pytest.mark.asyncio
async def test_realtime_start_subscribes_before_request_and_returns_answer_sdp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def responder(process: FakeProcess, message: dict[str, Any]) -> None:
        if _respond_handshake(process, message):
            return
        request_id = message.get("id")
        if not isinstance(request_id, int):
            return
        method = message.get("method")
        if method == "thread/start":
            _respond(process, request_id, {"thread": {"id": "voice-thread"}})
        elif method == "thread/realtime/start":
            # Notifications deliberately arrive before the request response.
            process.emit(
                {
                    "method": "thread/realtime/started",
                    "params": {
                        "threadId": "voice-thread",
                        "realtimeSessionId": "rt-1",
                    },
                }
            )
            process.emit(
                {
                    "method": "thread/realtime/sdp",
                    "params": {"threadId": "voice-thread", "sdp": "answer-sdp"},
                }
            )
            _respond(process, request_id, {})

    harness = SpawnHarness(monkeypatch, [responder])
    client = CodexAppServerClient()
    thread = await client.thread_start(base_instructions="Answer directly.")
    assert thread == {"thread": {"id": "voice-thread"}}

    full_stream = client.subscribe("voice-thread")
    result = await client.realtime_start(
        "voice-thread",
        offer_sdp="offer-sdp",
        model="explicit-realtime-model",
        include_startup_context=False,
        client_managed_handoffs=True,
    )

    assert result.response == {}
    assert result.answer_sdp == "answer-sdp"
    assert (await full_stream.get(0.1)).method == "thread/realtime/started"
    assert (await full_stream.get(0.1)).method == "thread/realtime/sdp"

    messages = harness.processes[0].stdin.messages
    thread_params = next(
        item["params"] for item in messages if item.get("method") == "thread/start"
    )
    assert thread_params["approvalPolicy"] == "never"
    assert thread_params["sandbox"] == "read-only"
    assert thread_params["environments"] == []
    assert thread_params["dynamicTools"] is None
    assert thread_params["ephemeral"] is True
    assert Path(thread_params["cwd"]).name.startswith("jarvis-codex-voice-")
    start_params = next(
        item["params"]
        for item in messages
        if item.get("method") == "thread/realtime/start"
    )
    # The upstream v3 SDP parser rejects an offer whose last line has no
    # terminator ("unmarshal SDP: EOF") — and Jarvis's ingress validation
    # strips offers, so the transport boundary must restore the newline.
    assert start_params["transport"]["sdp"] == "offer-sdp\r\n"

    realtime_params = next(
        item["params"] for item in messages if item.get("method") == "thread/realtime/start"
    )
    assert realtime_params["transport"] == {"type": "webrtc", "sdp": "offer-sdp\r\n"}
    assert realtime_params["model"] == "explicit-realtime-model"
    assert realtime_params["includeStartupContext"] is False
    assert realtime_params["clientManagedHandoffs"] is True

    full_stream.close()
    await client.close()


def _voice_thread_responder(process: FakeProcess, message: dict[str, Any]) -> None:
    if _respond_handshake(process, message):
        return
    request_id = message.get("id")
    if not isinstance(request_id, int):
        return
    if message.get("method") == "thread/start":
        _respond(process, request_id, {"thread": {"id": "voice-thread"}})


@pytest.mark.asyncio
async def test_thread_start_sends_caller_instructions_on_the_wire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The provider's voice persona must reach the JSON-RPC frame verbatim.

    An earlier revision deleted both caller instruction arguments and always
    sent the hardcoded "dumb pipe" transport text, so the live model never
    learned its persona, the one-speaker rule, or that handoffs exist — while
    the adapter-level test kept passing against a fake client that never sees
    the wire.
    """
    harness = SpawnHarness(monkeypatch, [_voice_thread_responder])
    client = CodexAppServerClient()
    await client.thread_start(
        base_instructions="You are the live voice. Request a handoff for actions.",
        developer_instructions="Execution boundary: actions go to the client.",
    )
    params = next(
        item["params"]
        for item in harness.processes[0].stdin.messages
        if item.get("method") == "thread/start"
    )
    assert (
        params["baseInstructions"]
        == "You are the live voice. Request a handoff for actions."
    )
    assert (
        params["developerInstructions"]
        == "Execution boundary: actions go to the client."
    )
    await client.close()


@pytest.mark.asyncio
async def test_thread_start_without_instructions_keeps_the_transport_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = SpawnHarness(monkeypatch, [_voice_thread_responder])
    client = CodexAppServerClient()
    await client.thread_start(base_instructions="   ", developer_instructions=None)
    params = next(
        item["params"]
        for item in harness.processes[0].stdin.messages
        if item.get("method") == "thread/start"
    )
    assert params["baseInstructions"] == transport._TRANSPORT_BASE_INSTRUCTIONS
    assert (
        params["developerInstructions"] == transport._TRANSPORT_DEVELOPER_INSTRUCTIONS
    )
    await client.close()


@pytest.mark.asyncio
async def test_audio_append_uses_protocol_chunk_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = SpawnHarness(monkeypatch)
    client = CodexAppServerClient()

    await client.realtime_append_audio(
        "thread-a",
        data="AAEC",
        sample_rate=24_000,
        num_channels=1,
        samples_per_channel=2,
        item_id="audio-1",
    )

    message = next(
        item
        for item in harness.processes[0].stdin.messages
        if item.get("method") == "thread/realtime/appendAudio"
    )
    assert message["params"] == {
        "threadId": "thread-a",
        "audio": {
            "data": "AAEC",
            "sampleRate": 24_000,
            "numChannels": 1,
            "samplesPerChannel": 2,
            "itemId": "audio-1",
        },
    }
    await client.close()


@pytest.mark.asyncio
async def test_notification_queue_overflow_fails_fast_without_silent_audio_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = SpawnHarness(monkeypatch)
    client = CodexAppServerClient()
    await client.ensure_started()
    subscription = client.subscribe("audio-thread", max_queue_size=1)
    process = harness.processes[0]

    process.emit(
        {
            "method": "thread/realtime/outputAudio/delta",
            "params": {"threadId": "audio-thread", "delta": "first"},
        }
    )
    process.emit(
        {
            "method": "thread/realtime/outputAudio/delta",
            "params": {"threadId": "audio-thread", "delta": "second"},
        }
    )

    with pytest.raises(CodexNotificationOverflow):
        await subscription.get(timeout_s=0.2)
    assert "audio-thread" not in client._subscriptions
    await client.close()


@pytest.mark.asyncio
async def test_server_initiated_actions_are_denied_without_logging_params(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    harness = SpawnHarness(monkeypatch)
    client = CodexAppServerClient()
    await client.ensure_started()
    process = harness.processes[0]
    caplog.set_level("DEBUG", logger=transport.__name__)

    process.emit(
        {
            "id": "approval-1",
            "method": "item/commandExecution/requestApproval",
            "params": {"command": "contains-private-prompt"},
        }
    )
    process.emit(
        {
            "id": "tool-1",
            "method": "item/tool/call",
            "params": {"arguments": {"secret": "contains-private-prompt"}},
        }
    )
    process.emit(
        {
            "id": "unknown-1",
            "method": "unknown/request",
            "params": {"private": "contains-private-prompt"},
        }
    )
    process.emit_stderr("account@example.com bearer-secret contains-private-prompt")
    await asyncio.sleep(0.02)

    responses = {item.get("id"): item for item in process.stdin.messages}
    assert responses["approval-1"]["result"] == {"decision": "decline"}
    assert responses["tool-1"]["result"] == {"contentItems": [], "success": False}
    assert responses["unknown-1"]["error"]["code"] == -32000
    assert "contains-private-prompt" not in caplog.text
    assert "account@example.com" not in caplog.text
    assert "content redacted" in caplog.text
    await client.close()


@pytest.mark.asyncio
async def test_timeout_is_bounded_and_rpc_error_message_is_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    held: list[tuple[FakeProcess, dict[str, Any]]] = []
    second_request_ready = asyncio.Event()

    def responder(process: FakeProcess, message: dict[str, Any]) -> None:
        if _respond_handshake(process, message):
            return
        if "id" in message:
            held.append((process, message))
            if len(held) >= 2:
                second_request_ready.set()

    SpawnHarness(monkeypatch, [responder])
    client = CodexAppServerClient(request_timeout_s=0.01)
    await client.ensure_started()

    with pytest.raises(CodexAppServerTimeout):
        await client.request("test/timeout")
    assert client._pending == {}

    error_task = asyncio.create_task(client.request("test/error", timeout_s=1.0))
    await asyncio.wait_for(second_request_ready.wait(), timeout=1.0)
    process, message = held[-1]
    process.emit(
        {
            "id": message["id"],
            "error": {"code": 418, "message": "account@example.com bearer-secret"},
        }
    )
    with pytest.raises(CodexAppServerRPCError) as exc_info:
        await error_task
    assert exc_info.value.code == 418
    assert "account@example.com" not in str(exc_info.value)
    assert "bearer-secret" not in str(exc_info.value)
    await client.close()


@pytest.mark.asyncio
async def test_process_death_fails_pending_and_subscribers_then_next_request_restarts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    held: list[tuple[FakeProcess, dict[str, Any]]] = []
    request_ready = asyncio.Event()

    def first_responder(process: FakeProcess, message: dict[str, Any]) -> None:
        if _respond_handshake(process, message):
            return
        if "id" in message:
            held.append((process, message))
            request_ready.set()

    harness = SpawnHarness(monkeypatch, [first_responder, None])
    client = CodexAppServerClient()
    await client.ensure_started()
    subscription = client.subscribe("thread-death")
    pending = asyncio.create_task(client.request("test/pending", timeout_s=1.0))
    await asyncio.wait_for(request_ready.wait(), timeout=1.0)

    harness.processes[0].finish(9)
    with pytest.raises(CodexAppServerDisconnected):
        await pending
    with pytest.raises(CodexAppServerDisconnected):
        await subscription.get(0.1)

    assert await client.request("test/after-restart") == {}
    assert len(harness.processes) == 2
    assert harness.trees[0].closed is True
    await client.close()


def test_capability_reads_only_the_dedicated_file_backed_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import jarvis.codex_auth as auth_module

    class FakeAuthService:
        constructor: tuple[object, dict[str, object]] | None = None
        login_result = (False, "unknown")

        def __init__(self, binary: str | None, **kwargs: object) -> None:
            type(self).constructor = (binary, kwargs)

        def _resolve_binary(self) -> str:
            return "codex-test"

        def _probe_version(self, _binary: str) -> str:
            return "codex-test 1.0"

        def login_status(self) -> tuple[bool, str]:
            return type(self).login_result

    home = tmp_path / "dedicated-profile"
    home.mkdir()
    monkeypatch.setattr(auth_module, "CodexAuthService", FakeAuthService)
    monkeypatch.setattr(transport, "codex_subscription_home", lambda: home)
    monkeypatch.setattr(
        transport,
        "_trusted_native_codex_binary",
        lambda binary, _version: binary,
    )
    monkeypatch.setattr(
        transport,
        "_validated_subscription_home",
        lambda **_kwargs: home,
    )
    capability = transport._read_codex_capability(None)
    assert capability.available is True
    assert capability.chatgpt_authenticated is False
    assert "subscription-voice login" in capability.reason
    assert FakeAuthService.constructor is not None
    _binary, kwargs = FakeAuthService.constructor
    assert kwargs["codex_home"] == home
    assert kwargs["force_file_auth_store"] is True
    assert kwargs["isolate_openai_environment"] is True

    # Opaque invalid bytes deliberately prove the snapshot checks only safe
    # CLI status and never parses or decodes token contents itself.
    (home / "auth.json").write_bytes(b"not-json-and-never-read")
    FakeAuthService.login_result = (True, "chatgpt")
    capability = transport._read_codex_capability(None)
    assert capability.available is True
    assert capability.chatgpt_authenticated is True


def test_capability_revalidates_profile_after_login_status_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import jarvis.codex_auth as auth_module

    class FakeAuthService:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def _resolve_binary(self) -> str:
            return "codex-test"

        def _probe_version(self, _binary: str) -> str:
            return "codex-cli 0.146.0"

        def login_status(self) -> tuple[bool, str]:
            return True, "chatgpt"

    validations = 0

    def validate_home(**_kwargs: object) -> Path:
        nonlocal validations
        validations += 1
        if validations == 2:
            raise CodexSubscriptionUnavailable("profile changed during status")
        return tmp_path

    monkeypatch.setattr(auth_module, "CodexAuthService", FakeAuthService)
    monkeypatch.setattr(
        transport,
        "_trusted_native_codex_binary",
        lambda binary, _version: binary,
    )
    monkeypatch.setattr(transport, "_validated_subscription_home", validate_home)

    capability = transport._read_codex_capability(None)

    assert validations == 2
    assert capability.available is False
    assert capability.chatgpt_authenticated is False
    assert capability.reason == "profile changed during status"


def test_missing_dedicated_profile_is_a_login_required_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jarvis.codex_auth as auth_module

    class FakeAuthService:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def _resolve_binary(self) -> str:
            return "codex-test"

        def _probe_version(self, _binary: str) -> str:
            return "codex-cli 0.146.0"

    monkeypatch.setattr(auth_module, "CodexAuthService", FakeAuthService)
    monkeypatch.setattr(
        transport,
        "_trusted_native_codex_binary",
        lambda binary, _version: binary,
    )

    def missing_profile(**_kwargs: object) -> Path:
        raise transport.CodexSubscriptionProfileMissing("profile missing")

    monkeypatch.setattr(
        transport,
        "_validated_subscription_home",
        missing_profile,
    )

    capability = transport._read_codex_capability(None)

    assert capability.available is False
    assert capability.version == "codex-cli 0.146.0"
    assert capability.reason_code == "login_required"


def test_public_auth_snapshot_uses_live_state_without_cli_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = CodexAppServerClient("codex-test")
    client._process = SimpleNamespace(returncode=None)  # type: ignore[assignment]
    client._ready = True
    client._trusted_binary_path = "trusted-codex-test"
    owner_loop = asyncio.new_event_loop()
    client._owner_loop = owner_loop
    monkeypatch.setattr(transport, "_shared_clients", {"codex-test": client})
    monkeypatch.setattr(transport, "_subscription_active_transports", {id(client)})
    monkeypatch.setattr(transport, "_subscription_transport_owner", client)
    monkeypatch.setattr(
        transport,
        "_read_codex_capability",
        lambda _binary: pytest.fail("live status must not spawn a Codex CLI probe"),
    )

    capability = transport.codex_subscription_auth_snapshot(None)
    owner_loop.close()

    assert capability.available is True
    assert capability.chatgpt_authenticated is True
    assert capability.binary_path == "trusted-codex-test"
    assert capability.reason_code == "ready"


def test_public_auth_snapshot_holds_profile_owner_for_entire_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent status call waits for the running probe and shares its result.

    The old contract returned ``setup_invalid`` to every losing caller, so a
    burst of UI refreshes rendered "profile needs attention" on a perfectly
    connected install (the reproduced desktop bug).
    """
    started = threading.Event()
    release = threading.Event()
    process_lock = FakeProfileProcessLock()
    reads: list[str | None] = []
    results: list[CodexAppServerCapability] = []
    concurrent_results: list[CodexAppServerCapability] = []

    monkeypatch.setattr(
        transport,
        "_acquire_subscription_process_lock",
        lambda: process_lock,
    )

    def slow_probe(binary: str | None) -> CodexAppServerCapability:
        reads.append(binary)
        assert transport._subscription_profile_mutating is True
        assert transport._subscription_mutation_process_lock is process_lock
        assert process_lock.closed is False
        started.set()
        assert release.wait(timeout=10.0)
        return CodexAppServerCapability(
            available=True,
            chatgpt_authenticated=True,
            binary_path="codex-test",
            version="codex-cli 0.146.0",
            reason="ready",
            reason_code="ready",
        )

    monkeypatch.setattr(transport, "_read_codex_capability", slow_probe)
    worker = threading.Thread(
        target=lambda: results.append(
            transport.codex_subscription_auth_snapshot("codex-test")
        )
    )
    worker.start()
    assert started.wait(timeout=1.0)

    concurrent_worker = threading.Thread(
        target=lambda: concurrent_results.append(
            transport.codex_subscription_auth_snapshot("codex-test")
        )
    )
    concurrent_worker.start()
    # The concurrent caller must block on the running probe, not read the
    # profile itself and not answer with a fake broken-setup status.
    concurrent_worker.join(timeout=0.3)
    assert concurrent_worker.is_alive() is True
    assert reads == ["codex-test"]
    assert process_lock.closed is False

    release.set()
    worker.join(timeout=2.0)
    concurrent_worker.join(timeout=2.0)

    assert worker.is_alive() is False
    assert concurrent_worker.is_alive() is False
    assert results[0].reason_code == "ready"
    # Exactly one CLI probe served both callers.
    assert reads == ["codex-test"]
    assert concurrent_results[0].reason_code == "ready"
    assert concurrent_results[0].chatgpt_authenticated is True
    assert process_lock.closed is True
    assert transport._subscription_profile_mutating is False
    assert transport._subscription_mutation_process_lock is None


def test_auth_snapshot_burst_coalesces_to_single_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Six parallel status calls (the UI mount burst) cost one CLI probe."""
    reads: list[str | None] = []
    ready = CodexAppServerCapability(
        available=True,
        chatgpt_authenticated=True,
        binary_path="codex-test",
        version="codex-cli 0.146.0",
        reason="ready",
        reason_code="ready",
    )

    def probe(binary: str | None) -> CodexAppServerCapability:
        reads.append(binary)
        time.sleep(0.05)
        return ready

    monkeypatch.setattr(transport, "_read_codex_capability", probe)
    results: list[CodexAppServerCapability] = []
    results_lock = threading.Lock()

    def call() -> None:
        snapshot = transport.codex_subscription_auth_snapshot("codex-test")
        with results_lock:
            results.append(snapshot)

    workers = [threading.Thread(target=call) for _ in range(6)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=3.0)

    assert all(not worker.is_alive() for worker in workers)
    assert len(reads) == 1
    assert len(results) == 6
    assert all(snapshot.reason_code == "ready" for snapshot in results)


def test_auth_snapshot_cache_expires_and_reprobes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads: list[str | None] = []
    ready = CodexAppServerCapability(
        available=True,
        chatgpt_authenticated=True,
        binary_path="codex-test",
        version="codex-cli 0.146.0",
        reason="ready",
        reason_code="ready",
    )

    def probe(binary: str | None) -> CodexAppServerCapability:
        reads.append(binary)
        return ready

    monkeypatch.setattr(transport, "_read_codex_capability", probe)

    first = transport.codex_subscription_auth_snapshot("codex-test")
    second = transport.codex_subscription_auth_snapshot("codex-test")
    assert first.reason_code == "ready"
    assert second.reason_code == "ready"
    # Within the TTL the second call is served from the cache.
    assert len(reads) == 1

    with transport._subscription_login_lock:
        transport._subscription_snapshot_cache_at = (
            time.monotonic() - transport._SUBSCRIPTION_SNAPSHOT_CACHE_TTL_S - 1.0
        )
    third = transport.codex_subscription_auth_snapshot("codex-test")
    assert third.reason_code == "ready"
    assert len(reads) == 2


def test_logout_invalidates_auth_snapshot_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads: list[str | None] = []
    ready = CodexAppServerCapability(
        available=True,
        chatgpt_authenticated=True,
        binary_path="codex-test",
        version="codex-cli 0.146.0",
        reason="ready",
        reason_code="ready",
    )

    def probe(binary: str | None) -> CodexAppServerCapability:
        reads.append(binary)
        return ready

    monkeypatch.setattr(transport, "_read_codex_capability", probe)
    monkeypatch.setattr(
        transport,
        "_delete_codex_subscription_auth_locked",
        lambda: (True, None),
    )

    assert transport.codex_subscription_auth_snapshot("codex-test").reason_code == "ready"
    assert len(reads) == 1

    assert transport.logout_codex_subscription() == (True, None)

    # Logout changed the profile, so the cached "ready" must not survive it.
    assert transport.codex_subscription_auth_snapshot("codex-test").reason_code == "ready"
    assert len(reads) == 2


def test_auth_snapshot_contended_process_lock_serves_last_known_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A foreign process briefly owning the profile is not a broken setup."""
    ready = CodexAppServerCapability(
        available=True,
        chatgpt_authenticated=True,
        binary_path="codex-test",
        version="codex-cli 0.146.0",
        reason="ready",
        reason_code="ready",
    )
    monkeypatch.setattr(transport, "_read_codex_capability", lambda _binary: ready)
    assert transport.codex_subscription_auth_snapshot("codex-test").reason_code == "ready"

    def contended() -> FakeProfileProcessLock:
        raise CodexSubscriptionUnavailable("owned elsewhere")

    monkeypatch.setattr(transport, "_acquire_subscription_process_lock", contended)
    with transport._subscription_login_lock:
        transport._subscription_snapshot_cache_at = (
            time.monotonic() - transport._SUBSCRIPTION_SNAPSHOT_CACHE_TTL_S - 1.0
        )

    stale = transport.codex_subscription_auth_snapshot("codex-test")
    assert stale.reason_code == "ready"

    with transport._subscription_login_lock:
        transport._invalidate_subscription_snapshot_locked()
    cold = transport.codex_subscription_auth_snapshot("codex-test")
    assert cold.reason_code == "busy"
    assert cold.available is False


def _ready_capability(binary_path: str = "codex-test") -> CodexAppServerCapability:
    return CodexAppServerCapability(
        available=True,
        chatgpt_authenticated=True,
        binary_path=binary_path,
        version="codex-cli 0.147.0",
        reason="ready",
        reason_code="ready",
    )


def test_auth_snapshot_stale_cache_has_an_age_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A foreign lock serves the last known status only up to a bounded age."""
    monkeypatch.setattr(
        transport, "_read_codex_capability", lambda _binary: _ready_capability()
    )
    assert transport.codex_subscription_auth_snapshot("codex-test").reason_code == "ready"

    def contended() -> FakeProfileProcessLock:
        raise CodexSubscriptionUnavailable("owned elsewhere")

    monkeypatch.setattr(transport, "_acquire_subscription_process_lock", contended)
    with transport._subscription_login_lock:
        transport._subscription_snapshot_cache_at = (
            time.monotonic() - transport._SUBSCRIPTION_SNAPSHOT_STALE_MAX_S - 1.0
        )

    beyond = transport.codex_subscription_auth_snapshot("codex-test")
    assert beyond.reason_code == "busy"


def test_auth_snapshot_cache_is_keyed_by_binary_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Correcting a configured binary path must not serve the old binary's result."""
    reads: list[str | None] = []

    def probe(binary: str | None) -> CodexAppServerCapability:
        reads.append(binary)
        return _ready_capability(binary_path=str(binary))

    monkeypatch.setattr(transport, "_read_codex_capability", probe)

    transport.codex_subscription_auth_snapshot("codex-a")
    transport.codex_subscription_auth_snapshot("codex-a")
    assert reads == ["codex-a"]

    transport.codex_subscription_auth_snapshot("codex-b")
    assert reads == ["codex-a", "codex-b"]


def test_auth_snapshot_probe_failure_is_cached_for_waiters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raising probe must not make coalesced waiters re-run the broken CLI."""
    reads: list[str | None] = []

    def probe(binary: str | None) -> CodexAppServerCapability:
        reads.append(binary)
        raise OSError("cli exploded")

    monkeypatch.setattr(transport, "_read_codex_capability", probe)

    with pytest.raises(OSError):
        transport.codex_subscription_auth_snapshot("codex-test")

    second = transport.codex_subscription_auth_snapshot("codex-test")
    # A RAISED probe is transiently unknown — never a proven-broken setup.
    assert second.reason_code == "busy"
    assert "OSError" in second.reason
    assert reads == ["codex-test"]


def test_logout_waits_for_a_running_status_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A background status refresh must delay logout, not fail it."""
    started = threading.Event()
    release = threading.Event()

    def slow_probe(_binary: str | None) -> CodexAppServerCapability:
        started.set()
        assert release.wait(timeout=10.0)
        return _ready_capability()

    monkeypatch.setattr(transport, "_read_codex_capability", slow_probe)
    monkeypatch.setattr(
        transport,
        "_delete_codex_subscription_auth_locked",
        lambda: (True, None),
    )

    prober = threading.Thread(
        target=lambda: transport.codex_subscription_auth_snapshot("codex-test")
    )
    prober.start()
    assert started.wait(timeout=1.0)

    results: list[tuple[bool, str | None]] = []
    logout_worker = threading.Thread(
        target=lambda: results.append(transport.logout_codex_subscription())
    )
    logout_worker.start()
    logout_worker.join(timeout=0.3)
    # Still waiting on the probe — the old behavior raised immediately with
    # "login is in progress", blaming a login that did not exist.
    assert logout_worker.is_alive() is True

    release.set()
    prober.join(timeout=2.0)
    logout_worker.join(timeout=2.0)
    assert logout_worker.is_alive() is False
    assert results == [(True, None)]


def test_user_actions_report_a_probe_timeout_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(transport, "_SUBSCRIPTION_PROBE_WAIT_S", 0.05)
    started = threading.Event()
    release = threading.Event()

    def slow_probe(_binary: str | None) -> CodexAppServerCapability:
        started.set()
        assert release.wait(timeout=10.0)
        return _ready_capability()

    monkeypatch.setattr(transport, "_read_codex_capability", slow_probe)
    prober = threading.Thread(
        target=lambda: transport.codex_subscription_auth_snapshot("codex-test")
    )
    prober.start()
    assert started.wait(timeout=1.0)

    with pytest.raises(
        CodexSubscriptionUnavailable, match="status check is still running"
    ):
        transport.logout_codex_subscription()

    release.set()
    prober.join(timeout=2.0)


@pytest.mark.asyncio
async def test_disconnect_invalidates_snapshot_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads: list[str | None] = []

    def probe(binary: str | None) -> CodexAppServerCapability:
        reads.append(binary)
        return _ready_capability()

    async def no_servers() -> None:
        return None

    monkeypatch.setattr(transport, "_read_codex_capability", probe)
    monkeypatch.setattr(transport, "close_shared_codex_app_servers", no_servers)
    monkeypatch.setattr(
        transport,
        "_delete_codex_subscription_auth_locked",
        lambda: (True, None),
    )

    assert transport.codex_subscription_auth_snapshot("codex-test").reason_code == "ready"

    ok, error = await transport.disconnect_and_logout_codex_subscription()
    assert (ok, error) == (True, None)

    # Disconnect changed the profile; the cached "ready" must not survive it.
    assert transport.codex_subscription_auth_snapshot("codex-test").reason_code == "ready"
    assert reads == ["codex-test", "codex-test"]


@pytest.mark.asyncio
async def test_capability_status_feeds_the_snapshot_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The transport-start probe publishes its result for concurrent callers."""
    reads: list[str | None] = []

    def probe(binary: str | None) -> CodexAppServerCapability:
        reads.append(binary)
        return _ready_capability(binary_path="trusted-codex-test")

    monkeypatch.setattr(transport, "_read_codex_capability", probe)
    client = CodexAppServerClient("codex-test")

    capability = await client.capability_status()
    assert capability.reason_code == "ready"

    snapshot = transport.codex_subscription_auth_snapshot("codex-test")
    assert snapshot.reason_code == "ready"
    assert reads == ["codex-test"]


def test_capability_maps_probe_failed_login_status_to_busy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A CLI spawn failure or timeout is unknown state, never 'not logged in'."""
    import jarvis.codex_auth as auth_module

    class FakeAuthService:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def _resolve_binary(self) -> str:
            return "codex-test"

        def _probe_version(self, _binary: str) -> str:
            return "codex-cli 0.146.0"

        def login_status(self) -> tuple[bool, str]:
            return False, "probe_failed"

    monkeypatch.setattr(auth_module, "CodexAuthService", FakeAuthService)
    monkeypatch.setattr(
        transport,
        "_trusted_native_codex_binary",
        lambda binary, _version: binary,
    )
    monkeypatch.setattr(
        transport,
        "_validated_subscription_home",
        lambda **_kwargs: tmp_path,
    )

    capability = transport._read_codex_capability(None)

    assert capability.reason_code == "busy"
    assert capability.available is False
    assert capability.chatgpt_authenticated is False
    assert "probe failed" in capability.reason


@pytest.mark.asyncio
async def test_disconnect_leaves_a_foreign_mutation_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disconnect refused because ANOTHER owner mutates must not clear it."""

    async def no_servers() -> None:
        return None

    monkeypatch.setattr(transport, "close_shared_codex_app_servers", no_servers)
    monkeypatch.setattr(transport, "_subscription_profile_mutating", True)

    with pytest.raises(CodexSubscriptionUnavailable):
        await transport.disconnect_and_logout_codex_subscription()

    # The foreign owner's claim survives the refused disconnect.
    assert transport._subscription_profile_mutating is True


def test_reaper_launch_failure_releases_guard_and_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import jarvis.codex_auth as auth_module
    import jarvis.core.private_directory as private_directory

    released: list[str] = []

    class FakeLoginProcess:
        pid = 4711

        def release_profile_lock(self) -> None:
            released.append("released")

    class FakeAuthService:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def start_login(self) -> FakeLoginProcess:
            return FakeLoginProcess()

    monkeypatch.setattr(auth_module, "CodexAuthService", FakeAuthService)
    monkeypatch.setattr(
        transport, "_prepare_subscription_login_home", lambda: tmp_path
    )
    monkeypatch.setattr(
        transport,
        "_read_codex_capability",
        lambda _binary: _ready_capability(binary_path="codex-test"),
    )
    monkeypatch.setattr(
        transport,
        "_trusted_codex_targets_for_platform",
        lambda: (
            (
                "codex-cli 0.147.0",
                ("test", "test", "codex-test", "trusted-test-digest"),
            ),
        ),
    )
    monkeypatch.setattr(
        transport,
        "_subscription_process_lock_path",
        lambda: tmp_path / "lock" / "owner.lock",
    )
    monkeypatch.setattr(
        private_directory,
        "ensure_owner_only_directory",
        lambda path, create=False: path,
    )

    def broken_reaper(_target: object) -> None:
        raise RuntimeError("no threads left")

    monkeypatch.setattr(
        transport, "_launch_subscription_login_reaper", broken_reaper
    )

    with pytest.raises(RuntimeError, match="no threads left"):
        transport.start_codex_subscription_login("codex-test")

    # The guardian released the OS profile lock and the in-process state is
    # clean again — no permanent "login in progress" wedge.
    assert released == ["released"]
    assert transport._subscription_login_in_flight is False
    assert transport._subscription_profile_mutating is False
    assert transport._subscription_login_process is None
    assert (
        transport.codex_subscription_auth_snapshot("codex-test").reason_code
        == "ready"
    )


@pytest.mark.asyncio
async def test_capability_worker_keeps_profile_reserved_until_it_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()

    def slow_probe(_binary: str | None) -> CodexAppServerCapability:
        started.set()
        assert release.wait(timeout=10.0)
        return CodexAppServerCapability(
            available=False,
            chatgpt_authenticated=False,
            binary_path=None,
            version=None,
            reason="not connected",
        )

    monkeypatch.setattr(transport, "_read_codex_capability", slow_probe)
    monkeypatch.setattr(transport, "_subscription_profile_mutating", False)
    monkeypatch.setattr(transport, "_subscription_active_transports", set())
    client = CodexAppServerClient()
    start_task = asyncio.create_task(client.ensure_started())

    assert await asyncio.to_thread(started.wait, 1.0)
    await asyncio.sleep(0.05)
    assert start_task.done() is False
    assert id(client) in transport._subscription_active_transports

    release.set()
    with pytest.raises(CodexSubscriptionUnavailable, match="not connected"):
        await asyncio.wait_for(start_task, timeout=1.0)
    assert id(client) not in transport._subscription_active_transports


@pytest.mark.asyncio
async def test_advisory_file_status_still_reaches_live_account_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = SpawnHarness(monkeypatch)
    monkeypatch.setattr(
        transport,
        "_read_codex_capability",
        lambda _binary: CodexAppServerCapability(
            available=True,
            chatgpt_authenticated=False,
            binary_path="codex-test",
            version="codex-test 1.0",
            reason="auth file absent",
        ),
    )
    client = CodexAppServerClient()

    await client.ensure_started()

    assert client.ready is True
    assert any(
        message.get("method") == "account/read"
        for process in harness.all_processes
        for message in process.stdin.messages
    )
    await client.close()


@pytest.mark.asyncio
async def test_shared_client_ignores_active_codex_account_profiles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import jarvis.agent_accounts as accounts

    monkeypatch.setattr(
        accounts,
        "active_account",
        lambda _platform: pytest.fail("active Codex accounts must not be read"),
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ambient-profile"))
    await transport.close_shared_codex_app_servers()

    client = transport.get_shared_codex_app_server("codex-test")

    assert client._binary_path == "codex-test"
    assert client._child_environment == {}
    await transport.close_shared_codex_app_servers()


@pytest.mark.asyncio
async def test_shared_client_is_process_wide_and_refuses_a_second_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One client per binary, process-wide.

    A per-loop client could only ever be one that can never reserve the
    single-owner profile, so a second loop is refused at ACQUISITION with a
    message naming what to do — not handed a client that fails deep inside a
    call with "already owned by another client".
    """
    SpawnHarness(monkeypatch)
    await transport.close_shared_codex_app_servers()
    current = transport.get_shared_codex_app_server("codex-test")
    assert transport.get_shared_codex_app_server("codex-test") is current

    def acquire_on_foreign_loop() -> BaseException | None:
        async def acquire() -> BaseException | None:
            try:
                transport.get_shared_codex_app_server("codex-test")
            except BaseException as exc:  # noqa: BLE001 - returned for assertion
                return exc
            return None

        return asyncio.run(acquire())

    error = await asyncio.to_thread(acquire_on_foreign_loop)

    assert isinstance(error, CodexSubscriptionUnavailable)
    assert "already running elsewhere" in str(error)
    await transport.close_shared_codex_app_servers()


@pytest.mark.asyncio
async def test_client_rejects_use_from_a_foreign_event_loop() -> None:
    client = CodexAppServerClient()
    client._owner_loop = asyncio.get_running_loop()

    async def close_on_foreign_loop() -> BaseException | None:
        try:
            await client.close()
        except BaseException as exc:  # noqa: BLE001 - return for parent assertion
            return exc
        return None

    error = await asyncio.to_thread(lambda: asyncio.run(close_on_foreign_loop()))

    assert isinstance(error, CodexSubscriptionUnavailable)
    assert "cannot cross event loops" in str(error)
    await client.close()


@pytest.mark.asyncio
async def test_second_client_cannot_share_the_subscription_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = SpawnHarness(monkeypatch)
    first = CodexAppServerClient()
    second = CodexAppServerClient()

    await first.ensure_started()
    with pytest.raises(CodexSubscriptionUnavailable, match="already running elsewhere"):
        await second.ensure_started()

    assert len(harness.processes) == 1
    await first.close()
    await second.ensure_started()
    assert len(harness.processes) == 2
    await second.close()


@pytest.mark.asyncio
async def test_transport_holds_process_lock_until_close_and_blocks_logout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    SpawnHarness(monkeypatch)
    profile_lock = FakeProfileProcessLock()
    monkeypatch.setattr(
        transport,
        "_acquire_subscription_process_lock",
        lambda: profile_lock,
    )
    client = CodexAppServerClient()

    await client.ensure_started()
    assert profile_lock.closed is False
    with pytest.raises(CodexSubscriptionUnavailable, match="still active"):
        transport.logout_codex_subscription()

    await client.close()
    assert profile_lock.closed is True


def test_dedicated_profile_rejects_agents_and_other_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import jarvis.core.paths as paths

    monkeypatch.setattr(paths, "user_data_dir", lambda: tmp_path)
    home = transport._prepare_subscription_login_home()
    (home / "AGENTS.md").write_text("Hostile instructions", encoding="utf-8")

    with pytest.raises(CodexSubscriptionUnavailable, match="configuration"):
        transport._validated_subscription_home(create=False, require_marker=True)


@pytest.mark.asyncio
async def test_startup_refuses_nonempty_user_mcp_layer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = SpawnHarness(
        monkeypatch,
        mcp_ids=("hostile.server", "second"),
        audit_effective_config=True,
    )
    client = CodexAppServerClient()

    with pytest.raises(CodexSubscriptionUnavailable, match="non-empty"):
        await client.ensure_started()

    assert len(harness.all_calls) == 1
    assert harness.processes[0].returncode is not None
    await client.close()


def _safe_audit_result(
    client: CodexAppServerClient,
) -> dict[str, Any]:
    provider_id = client._transport_provider_id
    version = "sha256:test-session-flags"
    session = {
        "analytics": {"enabled": False},
        "approval_policy": "never",
        "cli_auth_credentials_store": "file",
        "chatgpt_base_url": transport._OFFICIAL_CHATGPT_BASE,
        "experimental_compact_prompt_file": client._compact_prompt_file(),
        "experimental_realtime_start_instructions": "",
        "experimental_realtime_webrtc_call_base_url": (
            transport._OFFICIAL_CHATGPT_CODEX_BASE
        ),
        "experimental_realtime_ws_backend_prompt": "",
        "experimental_realtime_ws_base_url": (
            transport._OFFICIAL_OPENAI_REALTIME_BASE
        ),
        "features": {
            **{
                feature: False
                for feature in transport._DISABLED_APP_SERVER_FEATURES
            },
            "realtime_conversation": True,
        },
        "history": {"persistence": "none"},
        "include_apps_instructions": False,
        "include_collaboration_mode_instructions": False,
        "include_environment_context": False,
        "include_permissions_instructions": False,
        "log_dir": client._log_dir(),
        "memories": {
            "dedicated_tools": False,
            "generate_memories": False,
            "use_memories": False,
        },
        "model_catalog_json": client._model_catalog_file(),
        "model_instructions_file": client._safe_instructions_file(),
        "model_provider": provider_id,
        "model_providers": {
            provider_id: {
                "base_url": client._provider_sink_base_url(),
                "name": "OpenAI ChatGPT subscription voice",
                "request_max_retries": 0,
                "requires_openai_auth": True,
                "stream_max_retries": 0,
                "supports_standalone_web_search": False,
                "supports_websockets": False,
                "wire_api": "responses",
            }
        },
        "notify": [],
        "openai_base_url": transport._OFFICIAL_OPENAI_API_BASE,
        "orchestrator": {
            "mcp": {"enabled": False},
            "skills": {"enabled": False},
        },
        "otel": {
            "exporter": "none",
            "log_user_prompt": False,
            "metrics_exporter": "none",
            "trace_exporter": "none",
        },
        "project_doc_max_bytes": 0,
        "sandbox_mode": "read-only",
        "sqlite_home": client._sqlite_home(),
        "skills": {
            "bundled": {"enabled": False},
            "include_instructions": False,
        },
        "tools": {"web_search": False},
    }
    if sys.platform == "win32":
        session["windows"] = {"sandbox": "unelevated"}

    def leaf_paths(value: Any, prefix: tuple[str, ...] = ()) -> list[str]:
        if isinstance(value, dict):
            paths: list[str] = []
            for key, child in value.items():
                paths.extend(leaf_paths(child, (*prefix, str(key))))
            return paths
        if value == []:
            return []
        return [".".join(prefix)]

    origin = {
        "name": {"type": "sessionFlags"},
        "version": version,
    }
    return {
        "config": {
            "analytics": {"enabled": False},
            "approval_policy": "never",
            "model_provider": provider_id,
            "sandbox_mode": "read-only",
        },
        "layers": [
            {
                "name": {"type": "sessionFlags"},
                "version": version,
                "config": session,
            },
            {
                "name": {"type": "user", "file": "C:/isolated/config.toml"},
                "version": "sha256:empty",
                "config": {},
            },
        ],
        "origins": {path: dict(origin) for path in leaf_paths(session)},
    }


def test_effective_config_audit_requires_empty_host_layers_and_session_origins() -> None:
    client = CodexAppServerClient()
    client._workspace = transport._create_safe_transport_workspace()
    client._sink_base_url = "http://127.0.0.1:43123/v1"
    try:
        result = _safe_audit_result(client)
        client._audit_effective_config(result)

        result["layers"][1]["config"] = {"mcp_servers": {"hostile": {}}}
        with pytest.raises(CodexSubscriptionUnavailable, match="non-empty"):
            client._audit_effective_config(result)

        result["layers"][1]["config"] = {}
        result["origins"]["approval_policy"] = {
            "name": {"type": "user", "file": "C:/hostile/config.toml"},
            "version": "sha256:hostile",
        }
        with pytest.raises(CodexSubscriptionUnavailable, match="unapproved"):
            client._audit_effective_config(result)
    finally:
        shutil.rmtree(client._workspace.root, ignore_errors=True)


def test_effective_config_audit_accepts_0147_structured_feature_origin() -> None:
    client = CodexAppServerClient()
    client._trusted_binary_version = transport._SUPPORTED_CODEX_VERSION
    client._workspace = transport._create_safe_transport_workspace()
    client._sink_base_url = "http://127.0.0.1:43123/v1"
    try:
        result = _safe_audit_result(client)
        origin = result["origins"].pop("features.multi_agent_v2")
        result["origins"]["features.multi_agent_v2.enabled"] = origin

        client._audit_effective_config(result)

        client._trusted_binary_version = transport._LEGACY_CODEX_VERSION
        with pytest.raises(CodexSubscriptionUnavailable, match="origins failed"):
            client._audit_effective_config(result)
    finally:
        shutil.rmtree(client._workspace.root, ignore_errors=True)


def test_effective_config_audit_rejects_duplicate_structured_feature_origin() -> None:
    client = CodexAppServerClient()
    client._trusted_binary_version = transport._SUPPORTED_CODEX_VERSION
    client._workspace = transport._create_safe_transport_workspace()
    client._sink_base_url = "http://127.0.0.1:43123/v1"
    try:
        result = _safe_audit_result(client)
        result["origins"]["features.multi_agent_v2.enabled"] = dict(
            result["origins"]["features.multi_agent_v2"]
        )

        with pytest.raises(CodexSubscriptionUnavailable, match="origins failed"):
            client._audit_effective_config(result)
    finally:
        shutil.rmtree(client._workspace.root, ignore_errors=True)


@pytest.mark.asyncio
async def test_dedicated_home_is_revalidated_before_a_warm_thread_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first thread start rides the startup audit; every later one
    revalidates the profile before it may send a thread/start frame."""
    harness = SpawnHarness(monkeypatch)
    validations = 0

    def validate_home(**_kwargs: object) -> Path:
        nonlocal validations
        validations += 1
        if validations >= 3:
            raise CodexSubscriptionUnavailable(
                "The dedicated Codex voice profile contains configuration."
            )
        return harness.codex_home

    monkeypatch.setattr(transport, "_validated_subscription_home", validate_home)
    client = CodexAppServerClient()

    # Startup validates the profile twice (before spawn and after the audit);
    # the first thread start rides that, the warm one hits the poisoned third.
    await client.thread_start()
    with pytest.raises(CodexSubscriptionUnavailable, match="configuration"):
        await client.thread_start()

    assert [
        message.get("method")
        for message in harness.processes[0].stdin.messages
    ].count("thread/start") == 1
    await client.close()


@pytest.mark.asyncio
async def test_account_is_rechecked_before_every_warm_thread_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live account switch between calls still stops the next thread.

    The cold start's own account/read covers the thread start that triggered
    it; every WARM thread start re-reads the live account, because
    ``codex login --with-api-key`` can switch the shared process while it
    stays alive.
    """
    account_reads = 0

    def responder(process: FakeProcess, message: dict[str, Any]) -> None:
        nonlocal account_reads
        request_id = message.get("id")
        if not isinstance(request_id, int):
            return
        method = message.get("method")
        if method == "initialize":
            _respond(process, request_id, {})
        elif method == "account/read":
            account_reads += 1
            account_type = "chatgpt" if account_reads <= 1 else "apiKey"
            _respond(
                process,
                request_id,
                {
                    "account": {
                        "type": account_type,
                        "planType": "plus" if account_type == "chatgpt" else None,
                    },
                    "requiresOpenaiAuth": True,
                },
            )
        elif method == "thread/start":
            _respond(process, request_id, {"thread": {"id": "thread-1"}})

    harness = SpawnHarness(monkeypatch, [responder])
    client = CodexAppServerClient()

    await client.thread_start()
    with pytest.raises(CodexSubscriptionUnavailable):
        await client.thread_start()

    methods = [item.get("method") for item in harness.processes[0].stdin.messages]
    # One at startup (which the first thread start rides) plus one for the
    # warm second start — not one per start on top of the startup read.
    assert methods.count("account/read") == 2
    assert methods.count("thread/start") == 1


def test_config_requirements_must_be_absent() -> None:
    CodexAppServerClient._audit_config_requirements({"requirements": None})

    with pytest.raises(CodexSubscriptionUnavailable, match="managed"):
        CodexAppServerClient._audit_config_requirements(
            {"requirements": {"network": {"enabled": True}}}
        )


def test_thread_start_response_is_audited_as_read_only_and_ephemeral() -> None:
    client = CodexAppServerClient()
    client._workspace = transport._create_safe_transport_workspace()
    try:
        cwd = client._safe_thread_cwd()
        result = {
            "approvalPolicy": "never",
            "cwd": cwd,
            "instructionSources": [],
            "model": "gpt-5.1-codex",
            "modelProvider": client._transport_provider_id,
            "sandbox": {"type": "readOnly", "networkAccess": False},
            "thread": {
                "cwd": cwd,
                "ephemeral": True,
                "id": "thread-safe",
                "modelProvider": client._transport_provider_id,
            },
        }
        client._audit_thread_start_response(result)

        result["instructionSources"] = ["C:/hostile/AGENTS.md"]
        with pytest.raises(CodexSubscriptionUnavailable, match="unsafe"):
            client._audit_thread_start_response(result)

        result["instructionSources"] = []
        result["sandbox"] = {"type": "readOnly", "networkAccess": True}
        with pytest.raises(CodexSubscriptionUnavailable, match="unsafe"):
            client._audit_thread_start_response(result)
    finally:
        shutil.rmtree(client._workspace.root, ignore_errors=True)


@pytest.mark.parametrize("plan", ["free", "go", "plus", "pro", "prolite"])
@pytest.mark.asyncio
async def test_personal_chatgpt_plan_is_accepted(plan: str) -> None:
    client = CodexAppServerClient()

    async def account_read(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {
            "account": {"type": "chatgpt", "planType": plan},
            "requiresOpenaiAuth": True,
        }

    client._request_live = account_read  # type: ignore[method-assign]
    await client._verify_live_chatgpt_account()


@pytest.mark.parametrize("plan", [None, "team", "business", "enterprise", "edu"])
@pytest.mark.asyncio
async def test_managed_or_unknown_chatgpt_plan_is_refused(plan: str | None) -> None:
    client = CodexAppServerClient()

    async def account_read(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {
            "account": {"type": "chatgpt", "planType": plan},
            "requiresOpenaiAuth": True,
        }

    client._request_live = account_read  # type: ignore[method-assign]
    with pytest.raises(CodexSubscriptionUnavailable, match="personal"):
        await client._verify_live_chatgpt_account()


@pytest.mark.asyncio
async def test_account_requires_the_openai_authenticated_provider() -> None:
    client = CodexAppServerClient()

    async def account_read(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {
            "account": {"type": "chatgpt", "planType": "plus"},
            "requiresOpenaiAuth": False,
        }

    client._request_live = account_read  # type: ignore[method-assign]
    with pytest.raises(CodexSubscriptionUnavailable, match="authentication"):
        await client._verify_live_chatgpt_account()


@pytest.mark.asyncio
async def test_realtime_start_fails_fast_on_a_realtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An upstream refusal (the 403 that ended v1, an invalid offer) must
    surface as its honest text, never hide behind the blind SDP timeout."""

    def responder(process: FakeProcess, message: dict[str, Any]) -> None:
        if _respond_handshake(process, message):
            return
        request_id = message.get("id")
        if not isinstance(request_id, int):
            return
        method = message.get("method")
        if method == "thread/start":
            _respond(process, request_id, {"thread": {"id": "voice-thread"}})
        elif method == "thread/realtime/start":
            process.emit(
                {
                    "method": "thread/realtime/error",
                    "params": {
                        "threadId": "voice-thread",
                        "message": "unexpected status 403 Forbidden: Voice session access denied.",
                    },
                }
            )
            _respond(process, request_id, {})

    SpawnHarness(monkeypatch, [responder])
    client = CodexAppServerClient()
    await client.thread_start(base_instructions="Answer directly.")

    with pytest.raises(CodexAppServerError, match="Voice session access denied"):
        await client.realtime_start(
            "voice-thread",
            offer_sdp="offer-sdp",
            include_startup_context=False,
            client_managed_handoffs=True,
        )
    await client.close()


@pytest.mark.asyncio
async def test_realtime_start_forces_handoff_and_startup_context_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = SpawnHarness(monkeypatch)
    client = CodexAppServerClient()
    await client.ensure_started()
    client._trusted_binary_version = transport._SUPPORTED_CODEX_VERSION

    await client.realtime_start(
        "thread-1",
        prompt="hostile prompt",
        trusted_prompt="  trusted voice contract  ",
        initial_items=(
            {"role": "user", "text": "  Earlier question  "},
            {"role": "assistant", "text": "Earlier answer"},
        ),
        version="v3",
        include_startup_context=True,
        client_managed_handoffs=False,
    )
    params = next(
        frame["params"]
        for frame in harness.processes[0].stdin.messages
        if frame.get("method") == "thread/realtime/start"
    )
    assert params["prompt"] == "trusted voice contract"
    assert params["initialItems"] == [
        {"role": "user", "text": "Earlier question"},
        {"role": "assistant", "text": "Earlier answer"},
    ]
    assert params["delegationAckFiller"] is False
    assert params["includeStartupContext"] is False
    assert params["clientManagedHandoffs"] is True

    with pytest.raises(CodexSubscriptionUnavailable, match="Custom"):
        await client.realtime_start("thread-1", extra={"unsafe": True})
    await client.close()


@pytest.mark.asyncio
async def test_realtime_start_omits_new_ack_field_for_legacy_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = SpawnHarness(monkeypatch)
    client = CodexAppServerClient()
    await client.ensure_started()
    client._trusted_binary_version = transport._LEGACY_CODEX_VERSION

    await client.realtime_start(
        "thread-1", trusted_prompt="Voice contract", version="v3"
    )

    params = next(
        frame["params"]
        for frame in harness.processes[0].stdin.messages
        if frame.get("method") == "thread/realtime/start"
    )
    assert params["prompt"] == "Voice contract"
    assert "delegationAckFiller" not in params
    await client.close()


@pytest.mark.asyncio
async def test_realtime_start_rejects_unbounded_or_custom_startup_context() -> None:
    client = CodexAppServerClient()
    invalid_requests = (
        {"trusted_prompt": "x" * (transport._MAX_REALTIME_START_PROMPT_BYTES + 1)},
        {"initial_items": ({"role": "system", "text": "unsafe"},)},
        {"initial_items": ({"role": "user", "text": "ok", "extra": True},)},
        {
            "initial_items": ({"role": "user", "text": "valid item"},),
            "version": "v2",
        },
        {
            "initial_items": (
                {
                    "role": "user",
                    "text": "x" * (transport._MAX_REALTIME_INITIAL_TEXT_BYTES + 1),
                },
            )
        },
    )

    for kwargs in invalid_requests:
        with pytest.raises(CodexSubscriptionUnavailable, match="startup"):
            await client.realtime_start("thread-1", **kwargs)


def test_native_codex_accepts_only_audited_current_or_legacy_hash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    binary = tmp_path / "codex.exe"
    binary.write_bytes(b"official-test-binary")
    monkeypatch.setattr(transport.sys, "platform", "win32")
    monkeypatch.setattr(transport, "_normalized_machine", lambda: "x86_64")
    monkeypatch.setattr(
        transport,
        "_TRUSTED_CODEX_TARGETS",
        {
            ("win32", "x86_64"): (
                "win32-x64",
                "x86_64-pc-windows-msvc",
                "codex.exe",
                "approved-hash",
            )
        },
    )
    monkeypatch.setattr(
        transport,
        "_LEGACY_TRUSTED_CODEX_TARGETS",
        {
            ("win32", "x86_64"): (
                "win32-x64",
                "x86_64-pc-windows-msvc",
                "codex.exe",
                "legacy-approved-hash",
            )
        },
    )
    monkeypatch.setattr(
        transport, "_sha256_file_cached", lambda _path: "approved-hash"
    )

    assert (
        transport._trusted_native_codex_binary(
            str(binary), transport._SUPPORTED_CODEX_VERSION
        )
        == str(binary.resolve())
    )
    # The hash IS the pinned build: a launcher that cannot print its version
    # (npm wrapper without node on PATH) must not brick a verified binary.
    assert (
        transport._trusted_native_codex_binary(str(binary), None)
        == str(binary.resolve())
    )
    monkeypatch.setattr(
        transport, "_sha256_file_cached", lambda _path: "legacy-approved-hash"
    )
    assert (
        transport._trusted_native_codex_binary(
            str(binary), transport._LEGACY_CODEX_VERSION
        )
        == str(binary.resolve())
    )

    monkeypatch.setattr(
        transport, "_sha256_file_cached", lambda _path: "wrong-hash"
    )
    # A real-but-different release names the required version...
    with pytest.raises(CodexSubscriptionUnavailable, match="supports Codex"):
        transport._trusted_native_codex_binary(str(binary), "codex-cli 0.148.0")
    # ...while an unknown build with the right version fails on the hash.
    with pytest.raises(CodexSubscriptionUnavailable, match="approved build"):
        transport._trusted_native_codex_binary(
            str(binary), transport._SUPPORTED_CODEX_VERSION
        )


def test_wrong_release_maps_to_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real-but-different codex release is 'install the pinned one', not a
    profile defect — the card then shows the pinned npm command."""
    import jarvis.codex_auth as auth_module

    class FakeAuthService:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def _resolve_binary(self) -> str:
            return "codex-test"

        def _probe_version(self, _binary: str) -> str:
            return "codex-cli 0.150.0"

    def refuse(_binary: str, _version: str | None) -> str:
        raise transport.CodexSubscriptionBinaryUnsupported(
            "Subscription voice supports Codex CLI 0.147.0 or 0.146.0."
        )

    monkeypatch.setattr(auth_module, "CodexAuthService", FakeAuthService)
    monkeypatch.setattr(transport, "_trusted_native_codex_binary", refuse)

    capability = transport._read_codex_capability(None)

    assert capability.reason_code == "not_installed"
    assert capability.available is False
    assert "supports Codex CLI 0.147.0 or 0.146.0" in capability.reason
    assert capability.version == "codex-cli 0.150.0"


@pytest.mark.asyncio
async def test_cancelled_reserve_is_released_by_the_abandon_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancel landing on the reserve await must not park the profile on
    'voice is starting' forever (the permanent-wedge class)."""
    acquire_started = threading.Event()
    acquire_release = threading.Event()

    def blocking_acquire() -> FakeProfileProcessLock:
        acquire_started.set()
        assert acquire_release.wait(timeout=10.0)
        return FakeProfileProcessLock()

    monkeypatch.setattr(
        transport, "_acquire_subscription_process_lock", blocking_acquire
    )

    client = CodexAppServerClient("codex-test")
    task = asyncio.create_task(client.ensure_started())
    await asyncio.to_thread(acquire_started.wait, 5.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    acquire_release.set()
    # The reserve worker completes AFTER the cancel; the abandon worker must
    # then release it. Poll briefly instead of assuming scheduling order.
    for _ in range(100):
        with transport._subscription_login_lock:
            if not transport._subscription_active_transports:
                break
        await asyncio.sleep(0.02)
    with transport._subscription_login_lock:
        assert transport._subscription_active_transports == set()
        assert transport._subscription_transport_process_lock is None
    assert client._profile_transport_epoch is None


@pytest.mark.asyncio
async def test_cancelled_disconnect_still_releases_the_mutation_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling disconnect mid-flight (even twice) must not leave the
    profile permanently 'being checked or changed'."""
    close_entered = asyncio.Event()
    close_release = asyncio.Event()

    async def blocking_close() -> None:
        close_entered.set()
        await close_release.wait()

    monkeypatch.setattr(transport, "close_shared_codex_app_servers", blocking_close)
    monkeypatch.setattr(
        transport,
        "_delete_codex_subscription_auth_locked",
        lambda: (True, None),
    )
    # Gate the release so it provably happens AFTER both cancels — otherwise a
    # fast runner could finish cleanup before the second cancel and the test
    # would silently degrade to a single-cancel check.
    finish_gate = threading.Event()
    real_finish = transport._finish_subscription_disconnect_mutation

    def gated_finish() -> None:
        assert finish_gate.wait(timeout=10.0)
        real_finish()

    monkeypatch.setattr(
        transport, "_finish_subscription_disconnect_mutation", gated_finish
    )

    task = asyncio.create_task(
        transport.disconnect_and_logout_codex_subscription()
    )
    await asyncio.wait_for(close_entered.wait(), timeout=5.0)
    with transport._subscription_login_lock:
        assert transport._subscription_profile_mutating is True

    task.cancel()
    await asyncio.sleep(0.02)
    task.cancel()  # A second cancel interrupts even shielded awaits.
    with pytest.raises(asyncio.CancelledError):
        await task
    close_release.set()
    finish_gate.set()

    for _ in range(100):
        with transport._subscription_login_lock:
            if not transport._subscription_profile_mutating:
                break
        await asyncio.sleep(0.02)
    with transport._subscription_login_lock:
        assert transport._subscription_profile_mutating is False
        assert transport._subscription_mutation_process_lock is None


def test_spawn_binary_is_rehashed_without_the_memo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The copy that gets EXECUTED is verified fresh — a poisoned hash memo
    must never launder an in-place swap into execution."""
    binary = tmp_path / "codex.exe"
    binary.write_bytes(b"approved-content")
    approved = transport._sha256_file(binary)
    monkeypatch.setattr(transport.sys, "platform", "win32")
    monkeypatch.setattr(transport, "_normalized_machine", lambda: "x86_64")
    monkeypatch.setattr(
        transport,
        "_TRUSTED_CODEX_TARGETS",
        {
            ("win32", "x86_64"): (
                "win32-x64",
                "x86_64-pc-windows-msvc",
                "codex.exe",
                approved,
            )
        },
    )
    # A poisoned memo claiming the file is approved must be ignored here.
    monkeypatch.setattr(
        transport, "_sha256_file_cached", lambda _path: approved
    )

    _REAL_VERIFY_SPAWN_BINARY(str(binary))

    binary.write_bytes(b"swapped-content")
    with pytest.raises(CodexSubscriptionUnavailable, match="changed since"):
        _REAL_VERIFY_SPAWN_BINARY(str(binary))


def test_stale_epoch_release_is_ignored() -> None:
    """A superseded holder's release must never tear down a newer reservation."""
    client = CodexAppServerClient("codex-test")
    first = transport._reserve_subscription_transport(client)
    second = transport._reserve_subscription_transport(client)  # adoption bump

    transport._release_subscription_transport(client, first)
    with transport._subscription_login_lock:
        # The stale release was a no-op: the newer reservation survives.
        assert transport._subscription_active_transports == {id(client)}
        assert transport._subscription_transport_process_lock is not None

    transport._release_subscription_transport(client, second)
    with transport._subscription_login_lock:
        assert transport._subscription_active_transports == set()
        assert transport._subscription_transport_process_lock is None


def test_failure_memo_is_not_served_on_the_stale_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One transient probe failure must not paint the card for the whole
    next busy window via the stale-cache path."""

    def probe(_binary: str | None) -> CodexAppServerCapability:
        raise OSError("cli exploded")

    monkeypatch.setattr(transport, "_read_codex_capability", probe)
    with pytest.raises(OSError):
        transport.codex_subscription_auth_snapshot("codex-test")

    def contended() -> FakeProfileProcessLock:
        raise CodexSubscriptionUnavailable("owned elsewhere")

    monkeypatch.setattr(transport, "_acquire_subscription_process_lock", contended)
    with transport._subscription_login_lock:
        # Age the memo past the fresh TTL: within it, serving the memo IS the
        # coalescing contract; the stale path is what must exclude it.
        transport._subscription_snapshot_cache_at = (
            time.monotonic() - transport._SUBSCRIPTION_SNAPSHOT_CACHE_TTL_S - 1.0
        )

    snapshot = transport.codex_subscription_auth_snapshot("codex-test")
    assert snapshot.reason_code == "busy"
    # The stale path answered with the live contention, not the failure memo.
    assert "owned elsewhere" in snapshot.reason


@pytest.mark.asyncio
async def test_live_account_gate_records_the_plan_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sticky diagnosis is written where the truth is discovered — the
    live account gate — so a plan that turns unsupported AFTER activation
    still flips every status surface, not just the activation route."""
    client = CodexAppServerClient("codex-test")

    async def no_start() -> None:
        return None

    async def refuse() -> None:
        raise transport.CodexSubscriptionPlanUnsupported(
            "Subscription voice permits only personal ChatGPT accounts."
        )

    async def no_close(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(client, "ensure_started", no_start)
    monkeypatch.setattr(client, "_verify_live_chatgpt_account", refuse)
    monkeypatch.setattr(client, "_close_process", no_close)

    with pytest.raises(transport.CodexSubscriptionPlanUnsupported):
        await client.require_chatgpt_login()

    assert transport.codex_subscription_activation_block() == (
        "Subscription voice permits only personal ChatGPT accounts."
    )


def test_unreadable_profile_reports_busy_not_setup_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An antivirus-locked or slow-disk profile read is transiently unknown —
    never 'create a fresh voice-only login'."""
    import jarvis.codex_auth as auth_module

    class FakeAuthService:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def _resolve_binary(self) -> str:
            return "codex-test"

        def _probe_version(self, _binary: str) -> str:
            return "codex-cli 0.146.0"

    def unreadable(**_kwargs: object) -> Path:
        raise transport.CodexSubscriptionInspectionFailed(
            "The dedicated Codex voice profile could not be inspected."
        )

    monkeypatch.setattr(auth_module, "CodexAuthService", FakeAuthService)
    monkeypatch.setattr(
        transport,
        "_trusted_native_codex_binary",
        lambda binary, _version: binary,
    )
    monkeypatch.setattr(transport, "_validated_subscription_home", unreadable)

    capability = transport._read_codex_capability(None)

    assert capability.reason_code == "busy"
    assert "could not be inspected" in capability.reason


@pytest.mark.asyncio
async def test_warm_thread_start_plan_refusal_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A plan that turns unsupported BETWEEN calls is discovered by the warm
    per-thread account re-check — it must record the sticky diagnosis too."""
    client = CodexAppServerClient("codex-test")

    async def already_ready() -> None:
        return None

    async def refuse() -> None:
        raise transport.CodexSubscriptionPlanUnsupported(
            "Subscription voice permits only personal ChatGPT accounts."
        )

    async def no_close(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(client, "ensure_started", already_ready)
    monkeypatch.setattr(client, "_verify_live_chatgpt_account", refuse)
    monkeypatch.setattr(client, "_close_process", no_close)
    monkeypatch.setattr(
        transport, "_validated_subscription_home", lambda **_kwargs: tmp_path
    )
    client._workspace = SimpleNamespace(
        root=tmp_path,
        instructions=tmp_path / "instructions.md",
        compact_prompt=tmp_path / "compact.md",
        model_catalog=tmp_path / "models.json",
        sqlite_home=tmp_path,
        log_dir=tmp_path,
        child_home=tmp_path,
        child_appdata=tmp_path,
        child_local_appdata=tmp_path,
        child_tmp=tmp_path,
    )
    client._sink_base_url = "http://127.0.0.1:1/"

    with pytest.raises(transport.CodexSubscriptionPlanUnsupported):
        await client.thread_start(
            base_instructions="transport only",
            developer_instructions="no tools",
            ephemeral=True,
        )

    assert "personal ChatGPT accounts" in (
        transport.codex_subscription_activation_block() or ""
    )


def test_connect_rebuilds_an_invalid_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """setup_invalid must not be an in-app dead end: the explicit login action
    rebuilds the Jarvis-owned profile instead of failing on the same check."""
    calls: list[str] = []
    attempts = {"n": 0}

    def flaky_home(**kwargs: object) -> Path:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise CodexSubscriptionUnavailable(
                "The dedicated Codex voice profile contains configuration or "
                "runtime state. Create a fresh voice-only login."
            )
        return tmp_path

    monkeypatch.setattr(transport, "_validated_subscription_home", flaky_home)
    monkeypatch.setattr(
        transport,
        "_rebuild_invalid_subscription_home",
        lambda: calls.append("rebuilt"),
    )

    home = transport._prepare_subscription_login_home()

    assert calls == ["rebuilt"]
    assert home == tmp_path


def test_logout_clears_an_invalid_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disconnect must work in-app even when the profile fails validation."""
    calls: list[str] = []

    def invalid_home(**_kwargs: object) -> Path:
        raise CodexSubscriptionUnavailable(
            "The dedicated Codex voice profile contains an unknown runtime directory."
        )

    monkeypatch.setattr(transport, "_validated_subscription_home", invalid_home)
    monkeypatch.setattr(
        transport,
        "_rebuild_invalid_subscription_home",
        lambda: calls.append("removed"),
    )

    assert transport._delete_codex_subscription_auth_locked() == (True, None)
    assert calls == ["removed"]


def test_activation_block_survives_cache_invalidation_until_logout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cache invalidation happens on every mutation attempt — including an
    ABORTED re-login — which is no evidence the refused plan changed. Only
    explicit new evidence (here: logout) clears the verdict."""
    transport.set_codex_subscription_activation_block(
        "Subscription voice permits only personal ChatGPT accounts."
    )
    with transport._subscription_login_lock:
        transport._invalidate_subscription_snapshot_locked()
    assert (
        transport.codex_subscription_activation_block()
        == "Subscription voice permits only personal ChatGPT accounts."
    )

    monkeypatch.setattr(
        transport,
        "_delete_codex_subscription_auth_locked",
        lambda: (True, None),
    )
    assert transport.logout_codex_subscription() == (True, None)
    assert transport.codex_subscription_activation_block() is None


@pytest.mark.asyncio
async def test_cold_start_plan_refusal_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The FIRST activation (app-server not yet running) discovers the plan
    refusal inside ensure_started — the sticky diagnosis must be recorded on
    that cold path too, not only on the warm re-verification."""
    client = CodexAppServerClient("codex-test")
    monkeypatch.setattr(
        transport,
        "_read_codex_capability",
        lambda _binary: _ready_capability(binary_path="codex-test"),
    )

    def refuse(**_kwargs: object) -> Path:
        raise transport.CodexSubscriptionPlanUnsupported(
            "Subscription voice permits only personal ChatGPT accounts."
        )

    monkeypatch.setattr(transport, "_validated_subscription_home", refuse)

    with pytest.raises(transport.CodexSubscriptionPlanUnsupported):
        await client.ensure_started()

    assert "personal ChatGPT accounts" in (
        transport.codex_subscription_activation_block() or ""
    )


@pytest.mark.asyncio
async def test_login_ready_respects_the_activation_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Readiness surfaces must not advertise a provider whose account the
    live gate refused permanently."""
    monkeypatch.setattr(
        transport,
        "_read_codex_capability",
        lambda _binary: _ready_capability(),
    )
    assert await transport.codex_subscription_login_ready("codex-test") is True

    transport.set_codex_subscription_activation_block(
        "Subscription voice permits only personal ChatGPT accounts."
    )
    assert await transport.codex_subscription_login_ready("codex-test") is False


def test_headless_linux_reports_lifecycle_with_a_coherent_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reason TEXT degrades with the code: every surface renders one of
    the two, and they must tell the same story."""
    import jarvis.codex_auth as auth_module

    class FakeAuthService:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def _resolve_binary(self) -> str:
            return "codex-test"

        def _probe_version(self, _binary: str) -> str:
            return "codex-cli 0.146.0"

    monkeypatch.setattr(auth_module, "CodexAuthService", FakeAuthService)
    monkeypatch.setattr(
        transport,
        "_trusted_native_codex_binary",
        lambda binary, _version: binary,
    )

    def missing_profile(**_kwargs: object) -> Path:
        raise transport.CodexSubscriptionProfileMissing("profile missing")

    monkeypatch.setattr(transport, "_validated_subscription_home", missing_profile)
    monkeypatch.setattr(transport.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    capability = transport._read_codex_capability(None)

    assert capability.reason_code == "lifecycle_unavailable"
    assert "headless Linux" in capability.reason
    assert "desktop" in capability.reason


def test_profile_allowlist_accepts_codex_own_runtime_markers() -> None:
    """Codex 0.146 writes these into CODEX_HOME on a normal run; rejecting
    them bricked the profile right after the CLI's first use."""
    assert ".sandbox_migration" in transport._ALLOWED_SUBSCRIPTION_HOME_ENTRIES
    assert "installation_id" in transport._ALLOWED_SUBSCRIPTION_HOME_ENTRIES


def test_capability_survives_a_version_probe_that_prints_a_shell_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """npm's codex launcher without node prints a localized error, not a version."""
    import jarvis.codex_auth as auth_module

    class FakeAuthService:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def _resolve_binary(self) -> str:
            return "codex-test"

        def _probe_version(self, _binary: str) -> str:
            return (
                'Der Befehl ""node"" ist entweder falsch '  # i18n-allow
                "geschrieben oder\n"  # i18n-allow
            )

        def login_status(self) -> tuple[bool, str]:
            return True, "chatgpt"

    monkeypatch.setattr(auth_module, "CodexAuthService", FakeAuthService)
    monkeypatch.setattr(
        transport,
        "_trusted_native_codex_binary",
        lambda binary, _version: binary,
    )
    monkeypatch.setattr(
        transport,
        "_validated_subscription_home",
        lambda **_kwargs: tmp_path,
    )

    capability = transport._read_codex_capability(None)

    assert capability.available is True
    assert capability.chatgpt_authenticated is True
    assert capability.reason_code == "ready"
    # The shell error never becomes the version chip; the hash-proven pinned
    # release is reported instead.
    assert capability.version == transport._SUPPORTED_CODEX_VERSION


def test_sha256_memo_rehashes_when_the_file_identity_changes(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "codex.exe"
    binary.write_bytes(b"first-content")

    first = transport._sha256_file_cached(binary)
    assert transport._sha256_file_cached(binary) == first

    binary.write_bytes(b"second-content-of-a-different-size")
    second = transport._sha256_file_cached(binary)
    assert second != first
    assert second == transport._sha256_file(binary)


def test_sha256_file_ignores_windows_fstat_ctime_semantics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    binary = tmp_path / "codex.exe"
    binary.write_bytes(b"official-test-binary")
    original_stat = Path.stat

    def stat_with_creation_time(path: Path, *args: object, **kwargs: object) -> object:
        result = original_stat(path, *args, **kwargs)
        return SimpleNamespace(
            st_dev=result.st_dev,
            st_ino=result.st_ino,
            st_size=result.st_size,
            st_mtime_ns=result.st_mtime_ns,
            st_ctime_ns=result.st_ctime_ns + 1,
        )

    monkeypatch.setattr(transport.sys, "platform", "win32")
    monkeypatch.setattr(Path, "stat", stat_with_creation_time)

    assert transport._sha256_file(binary) == (
        "fad79da58e11e95a1ca016dff1191db5df37bd2ee8e15e7e66856f7a2c99450f"
    )


def test_trusted_binary_discovery_recovers_from_a_windows_store_launcher(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "WindowsApps" / "codex.exe"
    launcher.parent.mkdir()
    launcher.write_bytes(b"store-alias")
    npm_dir = tmp_path / "npm"
    native = (
        npm_dir
        / "node_modules"
        / "@openai"
        / "codex"
        / "node_modules"
        / "@openai"
        / "codex-win32-x64"
        / "vendor"
        / "x86_64-pc-windows-msvc"
        / "bin"
        / "codex.exe"
    )
    native.parent.mkdir(parents=True)
    native.write_bytes(b"official-npm-binary")
    monkeypatch.setattr(transport.sys, "platform", "win32")
    monkeypatch.setattr(transport, "_normalized_machine", lambda: "x86_64")
    monkeypatch.setenv("PATH", str(npm_dir))
    monkeypatch.setattr("jarvis.core.path_augment.candidate_dirs", lambda: [])
    monkeypatch.setattr(
        transport,
        "_sha256_file",
        lambda path: "approved-hash" if path == native.resolve() else "wrong-hash",
    )
    monkeypatch.setattr(
        transport,
        "_TRUSTED_CODEX_TARGETS",
        {
            ("win32", "x86_64"): (
                "win32-x64",
                "x86_64-pc-windows-msvc",
                "codex.exe",
                "approved-hash",
            )
        },
    )

    assert transport._trusted_native_codex_binary(
        str(launcher), transport._SUPPORTED_CODEX_VERSION
    ) == str(native.resolve())


@pytest.mark.parametrize(
    ("runtime_platform", "machine", "variant", "triple"),
    [
        ("darwin", "arm64", "darwin-arm64", "aarch64-apple-darwin"),
        ("linux", "x86_64", "linux-x64", "x86_64-unknown-linux-musl"),
    ],
)
def test_subscription_transport_accepts_approved_posix_binary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runtime_platform: str,
    machine: str,
    variant: str,
    triple: str,
) -> None:
    binary = tmp_path / "codex"
    binary.write_bytes(b"official-posix-binary")
    monkeypatch.setattr(transport.sys, "platform", runtime_platform)
    monkeypatch.setattr(transport, "_normalized_machine", lambda: machine)
    monkeypatch.setattr(transport, "_sha256_file", lambda _path: "approved-hash")
    monkeypatch.setattr(
        transport,
        "_TRUSTED_CODEX_TARGETS",
        {
            (runtime_platform, machine): (
                variant,
                triple,
                "codex",
                "approved-hash",
            )
        },
    )

    assert transport._trusted_native_codex_binary(
        str(binary), transport._SUPPORTED_CODEX_VERSION
    ) == str(binary.resolve())


def test_dedicated_profile_rejects_hardlinked_auth_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import jarvis.core.paths as paths

    monkeypatch.setattr(paths, "user_data_dir", lambda: tmp_path / "data")
    home = transport._prepare_subscription_login_home()
    outside = tmp_path / "outside-auth.json"
    outside.write_text("{}", encoding="utf-8")
    os.link(outside, home / "auth.json")

    with pytest.raises(CodexSubscriptionUnavailable, match="linked credential"):
        transport._validated_subscription_home(create=False, require_marker=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows credential DACL contract")
def test_dedicated_profile_rejects_permissive_windows_auth_acl(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import ntsecuritycon
    import win32api
    import win32con
    import win32security

    import jarvis.core.paths as paths

    monkeypatch.setattr(paths, "user_data_dir", lambda: tmp_path / "data")
    home = transport._prepare_subscription_login_home()
    auth = home / "auth.json"
    auth.write_text("{}", encoding="utf-8")

    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(),
        win32con.TOKEN_QUERY,
    )
    current_user = win32security.GetTokenInformation(
        token,
        win32security.TokenUser,
    )[0]
    everyone = win32security.CreateWellKnownSid(win32security.WinWorldSid, None)
    dacl = win32security.ACL()
    dacl.AddAccessAllowedAce(
        win32security.ACL_REVISION,
        ntsecuritycon.FILE_ALL_ACCESS,
        current_user,
    )
    dacl.AddAccessAllowedAce(
        win32security.ACL_REVISION,
        ntsecuritycon.FILE_GENERIC_READ,
        everyone,
    )
    win32security.SetNamedSecurityInfo(
        str(auth),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        dacl,
        None,
    )

    with pytest.raises(CodexSubscriptionUnavailable, match="not owner-only"):
        transport._validated_subscription_home(create=False, require_marker=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows workspace DACL contract")
def test_transport_workspace_has_exact_owner_only_windows_dacl() -> None:
    import ntsecuritycon
    import win32api
    import win32con
    import win32security

    workspace = transport._create_safe_transport_workspace()
    try:
        descriptor = win32security.GetNamedSecurityInfo(
            str(workspace.root),
            win32security.SE_FILE_OBJECT,
            win32security.OWNER_SECURITY_INFORMATION
            | win32security.DACL_SECURITY_INFORMATION,
        )
        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(),
            win32con.TOKEN_QUERY,
        )
        current_user = win32security.GetTokenInformation(
            token,
            win32security.TokenUser,
        )[0]
        owner = descriptor.GetSecurityDescriptorOwner()
        dacl = descriptor.GetSecurityDescriptorDacl()
        control, _revision = descriptor.GetSecurityDescriptorControl()

        assert win32security.ConvertSidToStringSid(owner) == (
            win32security.ConvertSidToStringSid(current_user)
        )
        assert control & win32security.SE_DACL_PROTECTED
        assert dacl is not None and dacl.GetAceCount() == 1
        ace_type, ace_flags = dacl.GetAce(0)[0]
        access_mask = dacl.GetAce(0)[1]
        trustee = dacl.GetAce(0)[2]
        assert (ace_type, ace_flags) == (
            win32security.ACCESS_ALLOWED_ACE_TYPE,
            win32security.OBJECT_INHERIT_ACE
            | win32security.CONTAINER_INHERIT_ACE,
        )
        assert access_mask == ntsecuritycon.FILE_ALL_ACCESS
        assert win32security.ConvertSidToStringSid(trustee) == (
            win32security.ConvertSidToStringSid(current_user)
        )
    finally:
        shutil.rmtree(workspace.root, ignore_errors=True)


@pytest.mark.skipif(os.name != "posix", reason="POSIX workspace mode contract")
def test_transport_workspace_has_owner_only_posix_mode() -> None:
    workspace = transport._create_safe_transport_workspace()
    try:
        metadata = workspace.root.stat()
        assert metadata.st_uid == os.geteuid()
        assert metadata.st_mode & 0o777 == 0o700
    finally:
        shutil.rmtree(workspace.root, ignore_errors=True)


def test_headless_linux_login_degrades_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(transport.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    with pytest.raises(CodexSubscriptionUnavailable, match="headless Linux"):
        transport.start_codex_subscription_login()


def test_exact_windows_codex_runtime_state_is_allowed_and_tampering_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import jarvis.core.paths as paths

    monkeypatch.setattr(paths, "user_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(transport.sys, "platform", "win32")
    monkeypatch.setattr(transport, "_normalized_machine", lambda: "x86_64")
    monkeypatch.setattr(
        transport,
        "_TRUSTED_CODEX_TARGETS",
        {
            ("win32", "x86_64"): (
                "win32-x64",
                "x86_64-pc-windows-msvc",
                "codex.exe",
                "approved",
            )
        },
    )
    binary = tmp_path / "codex.exe"
    binary.write_bytes(b"official binary")
    hash_calls: list[Path] = []

    def hash_binary(path: Path) -> str:
        canonical = Path(path).resolve()
        hash_calls.append(canonical)
        return "approved" if canonical == binary.resolve() else "wrong"

    monkeypatch.setattr(transport, "_sha256_file", hash_binary)
    home = transport._prepare_subscription_login_home()
    (home / "installation_id").write_text(
        "8fd4ce9f-0c3d-4502-8ff7-c22a79a11833",
        encoding="utf-8",
    )
    runtime = home / "tmp" / "arg0" / "codex-arg0Ab12Cd"
    runtime.mkdir(parents=True)
    if os.name == "posix":
        os.chmod(runtime.parent, 0o700)
    (runtime / ".lock").write_bytes(b"")
    wrapper = f'@echo off\n"{binary}" --codex-run-as-apply-patch %*\n'
    (runtime / "apply_patch.bat").write_text(wrapper, encoding="utf-8")
    (runtime / "applypatch.bat").write_text(wrapper, encoding="utf-8")

    assert (
        transport._validated_subscription_home(
            create=False,
            require_marker=True,
            trusted_binary_path=str(binary.resolve()),
        )
        == home
    )
    assert hash_calls == [binary.resolve()]

    (runtime / "apply_patch.bat").write_text(
        '@echo off\n"C:\\untrusted.exe" --codex-run-as-apply-patch %*\n',
        encoding="utf-8",
    )
    with pytest.raises(CodexSubscriptionUnavailable, match="untrusted runtime"):
        transport._validated_subscription_home(
            create=False,
            require_marker=True,
            trusted_binary_path=str(binary.resolve()),
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX umask semantics")
def test_installation_id_created_under_umask_077_is_accepted(tmp_path: Path) -> None:
    home = tmp_path / "profile"
    home.mkdir(mode=0o700)
    installation_id = home / "installation_id"
    installation_id.write_text(
        "8fd4ce9f-0c3d-4502-8ff7-c22a79a11833",
        encoding="utf-8",
    )
    installation_id.chmod(0o600)

    transport._validate_codex_runtime_state(home)


def test_exact_unix_codex_runtime_aliases_must_target_trusted_binary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import jarvis.core.paths as paths

    monkeypatch.setattr(paths, "user_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(transport.sys, "platform", "linux")
    monkeypatch.setattr(transport, "_normalized_machine", lambda: "x86_64")
    monkeypatch.setattr(
        transport,
        "_TRUSTED_CODEX_TARGETS",
        {
            ("linux", "x86_64"): (
                "linux-x64",
                "x86_64-unknown-linux-musl",
                "codex",
                "approved",
            )
        },
    )
    binary = tmp_path / "codex"
    binary.write_bytes(b"official binary")
    monkeypatch.setattr(
        transport,
        "_sha256_file",
        lambda path: "approved" if Path(path).resolve() == binary.resolve() else "wrong",
    )
    home = transport._prepare_subscription_login_home()
    (home / "installation_id").write_text(
        "8fd4ce9f-0c3d-4502-8ff7-c22a79a11833",
        encoding="utf-8",
    )
    if os.name == "posix":
        os.chmod(home / "installation_id", 0o644)
    runtime = home / "tmp" / "arg0" / "codex-arg0Ab12Cd"
    runtime.mkdir(parents=True)
    if os.name == "posix":
        os.chmod(runtime.parent, 0o700)
    (runtime / ".lock").write_bytes(b"")
    aliases = {
        "apply_patch",
        "applypatch",
        "codex-execve-wrapper",
        "codex-linux-sandbox",
    }
    try:
        for alias in aliases:
            os.symlink(binary, runtime / alias)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {type(exc).__name__}")

    assert (
        transport._validated_subscription_home(
            create=False,
            require_marker=True,
            trusted_binary_path=str(binary.resolve()),
        )
        == home
    )
    (runtime / "unexpected").write_text("unsafe", encoding="utf-8")
    with pytest.raises(CodexSubscriptionUnavailable, match="unexpected runtime"):
        transport._validated_subscription_home(
            create=False,
            require_marker=True,
            trusted_binary_path=str(binary.resolve()),
        )


def test_subscription_login_guard_covers_process_and_reaper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import jarvis.codex_auth as auth_module

    cleanup_targets: list[Callable[[], None]] = []
    processes: list[object] = []

    class FakeProcess:
        pid = 731

        def wait(self) -> int:
            return 0

    class FakeAuthService:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def start_login(self) -> FakeProcess:
            process = FakeProcess()
            processes.append(process)
            return process

    home = tmp_path / "profile"
    home.mkdir()
    profile_lock = FakeProfileProcessLock()
    monkeypatch.setattr(
        transport,
        "_acquire_subscription_process_lock",
        lambda: profile_lock,
    )
    monkeypatch.setattr(auth_module, "CodexAuthService", FakeAuthService)
    monkeypatch.setattr(
        transport,
        "_launch_subscription_login_reaper",
        cleanup_targets.append,
    )
    monkeypatch.setattr(transport, "_prepare_subscription_login_home", lambda: home)
    capability_reads: list[str | None] = []

    def read_capability(binary: str | None) -> CodexAppServerCapability:
        capability_reads.append(binary)
        authenticated = len(capability_reads) > 1
        return CodexAppServerCapability(
            available=True,
            chatgpt_authenticated=authenticated,
            binary_path="codex-test",
            version="codex-cli 0.147.0",
            reason="ready",
            reason_code="ready" if authenticated else "login_required",
        )

    monkeypatch.setattr(
        transport,
        "_read_codex_capability",
        read_capability,
    )
    monkeypatch.setattr(
        transport,
        "_trusted_codex_targets_for_platform",
        lambda: (
            (
                "codex-cli 0.147.0",
                ("test", "test", "codex-test", "trusted-test-digest"),
            ),
        ),
    )
    monkeypatch.setattr(
        transport,
        "_validated_subscription_home",
        lambda **_kwargs: home,
    )
    monkeypatch.setattr(transport, "_subscription_login_in_flight", False)
    monkeypatch.setattr(transport, "_subscription_login_process", None)
    monkeypatch.setattr(transport, "_subscription_profile_mutating", False)
    monkeypatch.setattr(transport, "_subscription_active_transports", set())

    first = transport.start_codex_subscription_login()
    assert first is processes[0]
    assert capability_reads == [None]
    assert profile_lock.closed is False
    with pytest.raises(CodexSubscriptionUnavailable, match="already in progress"):
        transport.start_codex_subscription_login()
    with pytest.raises(CodexSubscriptionUnavailable, match="login is in progress"):
        transport.logout_codex_subscription()

    cleanup_targets[0]()
    assert capability_reads == [None, "codex-test"]
    assert profile_lock.closed is True
    assert transport._subscription_login_in_flight is False
    assert transport._subscription_login_process is None


def test_subscription_login_spawn_failure_releases_guard(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import jarvis.codex_auth as auth_module

    class FakeAuthService:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def start_login(self) -> None:
            raise RuntimeError("terminal unavailable")

    monkeypatch.setattr(auth_module, "CodexAuthService", FakeAuthService)
    monkeypatch.setattr(
        transport,
        "_prepare_subscription_login_home",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        transport,
        "_read_codex_capability",
        lambda _binary: CodexAppServerCapability(
            True,
            False,
            "codex-test",
            "codex-cli 0.147.0",
            "ready",
        ),
    )
    monkeypatch.setattr(
        transport,
        "_trusted_codex_targets_for_platform",
        lambda: (
            (
                "codex-cli 0.147.0",
                ("test", "test", "codex-test", "trusted-test-digest"),
            ),
        ),
    )
    monkeypatch.setattr(transport, "_subscription_login_in_flight", False)
    monkeypatch.setattr(transport, "_subscription_login_process", None)
    monkeypatch.setattr(transport, "_subscription_profile_mutating", False)
    monkeypatch.setattr(transport, "_subscription_active_transports", set())

    with pytest.raises(CodexSubscriptionUnavailable, match="terminal unavailable"):
        transport.start_codex_subscription_login()
    assert transport._subscription_login_in_flight is False


def test_subscription_logout_is_idempotent_and_deletes_only_dedicated_auth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import jarvis.core.paths as paths

    monkeypatch.setattr(paths, "user_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(transport, "_subscription_login_in_flight", False)
    monkeypatch.setattr(transport, "_subscription_profile_mutating", False)
    monkeypatch.setattr(transport, "_subscription_active_transports", set())
    profile_locks: list[FakeProfileProcessLock] = []

    def acquire_lock() -> FakeProfileProcessLock:
        lock = FakeProfileProcessLock()
        profile_locks.append(lock)
        return lock

    monkeypatch.setattr(
        transport,
        "_acquire_subscription_process_lock",
        acquire_lock,
    )

    assert transport.logout_codex_subscription() == (True, None)
    assert profile_locks[-1].closed is True

    home = transport._prepare_subscription_login_home()
    auth = home / "auth.json"
    auth.write_text("opaque credentials", encoding="utf-8")
    assert transport.logout_codex_subscription("missing-codex") == (True, None)
    assert profile_locks[-1].closed is True
    assert not auth.exists()
    assert (home / transport._SUBSCRIPTION_PROFILE_MARKER).exists()


@pytest.mark.asyncio
async def test_atomic_logout_transfers_live_transport_lock_without_a_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    SpawnHarness(monkeypatch)
    await transport.close_shared_codex_app_servers()
    profile_lock = FakeProfileProcessLock()
    monkeypatch.setattr(
        transport,
        "_acquire_subscription_process_lock",
        lambda: profile_lock,
    )
    client = transport.get_shared_codex_app_server("codex-test")
    await client.ensure_started()
    observations: list[str] = []

    def delete_auth() -> tuple[bool, None]:
        assert transport._subscription_profile_mutating is True
        assert transport._subscription_active_transports == set()
        assert transport._subscription_mutation_process_lock is profile_lock
        assert profile_lock.closed is False
        observations.append("deleted")
        return True, None

    monkeypatch.setattr(
        transport,
        "_delete_codex_subscription_auth_locked",
        delete_auth,
    )

    result = await transport.disconnect_and_logout_codex_subscription()

    assert result == (True, None)
    assert observations == ["deleted"]
    assert profile_lock.closed is True
    assert transport._subscription_profile_mutating is False
