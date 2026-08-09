"""Unit tests for the warm wake-ride audit token, the per-host STUN media-path
memory, and the unused 0.147 protocol levers of the Codex app-server client.

Every subprocess, pipe, auth result, and process-tree container is a local
fake, following the pattern of ``tests/unit/codex_app_server/test_transport.py``
(kept separate because the working tree is shared). The suite never reads a
Codex auth file, starts the CLI, or touches the network.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import jarvis.codex_app_server as transport
from jarvis.codex_app_server import (
    CodexAppServerCapability,
    CodexAppServerClient,
    CodexAppServerRPCError,
    CodexSubscriptionUnavailable,
)


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
    monkeypatch.setattr(transport, "_verify_spawn_binary", lambda _path: None)
    monkeypatch.setattr(transport, "_stun_media_path_memory", {})


class FakeStdin:
    def __init__(self, process: FakeProcess) -> None:
        self.process = process
        self.messages: list[dict[str, Any]] = []
        self.closed = False

    def write(self, payload: bytes) -> None:
        assert payload.endswith(b"\n")
        for line in payload.splitlines():
            message = json.loads(line.decode("utf-8"))
            self.messages.append(message)
            self.process.on_client_message(message)

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def close(self) -> None:
        self.closed = True
        self.process.finish(0)


class FakeProcess:
    _next_pid = 5200

    def __init__(
        self,
        responder: Callable[[FakeProcess, dict[str, Any]], None] | None = None,
    ) -> None:
        type(self)._next_pid += 1
        self.pid = type(self)._next_pid
        self.returncode: int | None = None
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdin = FakeStdin(self)
        self._waited = asyncio.Event()
        self._responder = responder or _default_responder

    def on_client_message(self, message: dict[str, Any]) -> None:
        self._responder(self, message)

    def emit(self, message: dict[str, Any]) -> None:
        self.stdout.feed_data(json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n")

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


def _default_responder(process: FakeProcess, message: dict[str, Any]) -> None:
    request_id = message.get("id")
    if not isinstance(request_id, int):
        return
    method = message.get("method")
    if method == "initialize":
        _respond(process, request_id, {"userAgent": "fake"})
    elif method == "account/read":
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
    elif method == "config/read":
        _respond(
            process,
            request_id,
            {"config": {}, "layers": [{"name": "sessionFlags", "config": {}}]},
        )
    elif method == "configRequirements/read":
        _respond(process, request_id, {"requirements": None})
    elif method == "thread/start":
        _respond(process, request_id, {"thread": {"id": "thread-1"}})
    elif method == "thread/realtime/listVoices":
        _respond(
            process,
            request_id,
            {
                "v1": ["cove"],
                "v2": ["cove", "maple"],
                "defaultV1": "cove",
                "defaultV2": "maple",
            },
        )
    else:
        _respond(process, request_id, {})


class SpawnHarness:
    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        responder: Callable[[FakeProcess, dict[str, Any]], None] | None = None,
    ) -> None:
        self.processes: list[FakeProcess] = []
        self.trees: list[FakeProcessTree] = []
        self._responder = responder
        self._pending_tree: FakeProcessTree | None = None
        self.codex_home = Path("C:/isolated-jarvis-codex-voice-test")
        self.home_validations = 0

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
            self._pending_tree = tree
            return tree

        async def spawn(*_args: Any, **_kwargs: Any) -> FakeProcess:
            process = FakeProcess(self._responder)
            tree = self._pending_tree
            assert tree is not None
            self._pending_tree = None
            self.processes.append(process)
            self.trees.append(tree)
            return process

        def validate_home(**_kwargs: object) -> Path:
            self.home_validations += 1
            return self.codex_home

        monkeypatch.setattr(transport, "make_process_tree", make_tree)
        monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(transport, "_validated_subscription_home", validate_home)
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

    def methods(self) -> list[str]:
        return [str(message.get("method")) for message in self.processes[0].stdin.messages]


def _age_stamp(client: CodexAppServerClient) -> None:
    """Move the shared audit stamp beyond the ride TTL without sleeping."""
    client._startup_audit_at -= transport._STARTUP_AUDIT_TTL_S + 1.0


# --- (a) warm wake-ride audit token -----------------------------------------


@pytest.mark.asyncio
async def test_warm_verify_mints_a_ride_one_wake_skips_the_reaudit_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = SpawnHarness(monkeypatch)
    client = CodexAppServerClient()

    # Cold start inside the first thread start consumes the cold one-shot.
    await client.thread_start()
    _age_stamp(client)
    client._warm_audit_process = None
    client._warm_audit_at = 0.0

    # The warm verify pass completes the FULL audit (profile walk, live
    # account, config requirements, effective config) and mints the ride.
    validations_before = harness.home_validations
    accounts_before = harness.methods().count("account/read")
    await client.require_chatgpt_login()
    assert harness.home_validations == validations_before + 1
    # One account/read for the gate itself plus one inside the full audit.
    assert harness.methods().count("account/read") == accounts_before + 2

    # A wake word within the TTL rides that just-passed audit: no profile
    # walk, no account/read, no config RPCs on top of the thread/start frame.
    validations_before = harness.home_validations
    methods_before = harness.methods()
    await client.thread_start()
    added = harness.methods()[len(methods_before) :]
    assert added == ["thread/start"]
    assert harness.home_validations == validations_before

    # The ride is one-shot: the next warm start re-audits in full.
    methods_before = harness.methods()
    await client.thread_start()
    added = harness.methods()[len(methods_before) :]
    assert added.count("account/read") == 1
    assert added.count("configRequirements/read") == 1
    assert added.count("config/read") == 1
    assert added[-1] == "thread/start"
    assert harness.home_validations == validations_before + 1
    await client.close()


@pytest.mark.asyncio
async def test_expired_warm_ride_still_reaudits_in_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = SpawnHarness(monkeypatch)
    client = CodexAppServerClient()

    await client.thread_start()
    _age_stamp(client)
    client._warm_audit_process = None
    client._warm_audit_at = 0.0
    await client.require_chatgpt_login()

    # The wake arrives AFTER the TTL: the token must not be honored.
    client._warm_audit_at -= transport._STARTUP_AUDIT_TTL_S + 1.0
    client._startup_audit_at -= transport._STARTUP_AUDIT_TTL_S + 1.0
    methods_before = harness.methods()
    await client.thread_start()
    added = harness.methods()[len(methods_before) :]
    assert added.count("account/read") == 1
    assert added.count("configRequirements/read") == 1
    assert added.count("config/read") == 1
    # Stale or not, the read consumed it — nothing is left to ride.
    assert client._warm_audit_process is None
    await client.close()


@pytest.mark.asyncio
async def test_prime_reuses_a_fresh_audit_stamp_without_new_rpcs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A prime microseconds after the cold start must not repeat the audit —
    it re-mints the ride from the stamp of the audit that just passed."""
    harness = SpawnHarness(monkeypatch)
    client = CodexAppServerClient()

    await client.ensure_started()
    methods_before = harness.methods()
    await client.prime_startup_audit()
    assert harness.methods() == methods_before
    assert client._warm_audit_process is client._process
    assert client._warm_audit_at == client._startup_audit_at
    await client.close()
    assert client._warm_audit_process is None


# --- (b) per-host STUN media-path memory ------------------------------------


def test_stun_memory_prefers_stun_only_after_a_successful_stun_connect() -> None:
    assert transport.media_path_prefers_stun("chatgpt.com") is False

    transport.record_media_path_outcome("chatgpt.com", needed_stun=True)
    assert transport.media_path_prefers_stun("chatgpt.com") is True
    # Normalized per host: case and surrounding whitespace do not split keys.
    assert transport.media_path_prefers_stun("  CHATGPT.COM ") is True
    assert transport.media_path_prefers_stun("other.example") is False

    # A later successful host-candidate connect clears the memory.
    transport.record_media_path_outcome("ChatGPT.com", needed_stun=False)
    assert transport.media_path_prefers_stun("chatgpt.com") is False


def test_stun_memory_expires_after_its_ttl() -> None:
    transport.record_media_path_outcome("chatgpt.com", needed_stun=True)
    transport._stun_media_path_memory["chatgpt.com"] = (
        time.monotonic() - transport._STUN_MEDIA_PATH_TTL_S - 1.0
    )
    assert transport.media_path_prefers_stun("chatgpt.com") is False
    # The stale entry is dropped, not merely ignored.
    assert "chatgpt.com" not in transport._stun_media_path_memory


def test_stun_memory_ignores_empty_hosts() -> None:
    transport.record_media_path_outcome("", needed_stun=True)
    transport.record_media_path_outcome("   ", needed_stun=True)
    assert transport._stun_media_path_memory == {}
    assert transport.media_path_prefers_stun("") is False


# --- (c) protocol levers ----------------------------------------------------


@pytest.mark.asyncio
async def test_realtime_list_voices_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = SpawnHarness(monkeypatch)
    client = CodexAppServerClient()
    await client.ensure_started()

    voices = await client.realtime_list_voices("thread-1")

    assert voices["v2"] == ["cove", "maple"]
    assert voices["defaultV2"] == "maple"
    frame = next(
        message
        for message in harness.processes[0].stdin.messages
        if message.get("method") == "thread/realtime/listVoices"
    )
    assert frame["params"] == {"threadId": "thread-1"}
    await client.close()


@pytest.mark.asyncio
async def test_realtime_list_voices_raises_cleanly_when_the_server_lacks_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def responder(process: FakeProcess, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        if not isinstance(request_id, int):
            return
        if message.get("method") == "thread/realtime/listVoices":
            asyncio.get_running_loop().call_soon(
                process.emit,
                {
                    "id": request_id,
                    "error": {"code": -32601, "message": "method not found"},
                },
            )
            return
        _default_responder(process, message)

    SpawnHarness(monkeypatch, responder)
    client = CodexAppServerClient()
    await client.ensure_started()

    with pytest.raises(CodexAppServerRPCError) as excinfo:
        await client.realtime_list_voices("thread-1")
    assert excinfo.value.code == -32601
    await client.close()


def _realtime_start_params(harness: SpawnHarness) -> dict[str, Any]:
    return next(
        frame["params"]
        for frame in harness.processes[0].stdin.messages
        if frame.get("method") == "thread/realtime/start"
    )


@pytest.mark.asyncio
async def test_realtime_start_sends_audited_fields_when_set_and_version_trusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = SpawnHarness(monkeypatch)
    client = CodexAppServerClient()
    await client.ensure_started()
    client._trusted_binary_version = transport._SUPPORTED_CODEX_VERSION

    await client.realtime_start(
        "thread-1",
        version="v3",
        trusted_prompt="Voice contract",
        realtime_start_instructions="  Speak as Jarvis.  ",
        realtime_end_instructions="Close the call politely.",
        codex_responses_as_items=True,
        codex_response_item_prefix="jarvis:",
        codex_response_handoff_mode="items",
        codex_response_handoff_channel_prefixes=(" delegate ", "handoff"),
    )

    params = _realtime_start_params(harness)
    assert params["prompt"] == "Voice contract"
    assert params["realtimeStartInstructions"] == "Speak as Jarvis."
    assert params["realtimeEndInstructions"] == "Close the call politely."
    assert params["codexResponsesAsItems"] is True
    assert params["codexResponseItemPrefix"] == "jarvis:"
    assert params["codexResponseHandoffMode"] == "items"
    assert params["codexResponseHandoffChannelPrefixes"] == ["delegate", "handoff"]
    assert params["delegationAckFiller"] is False
    await client.close()


@pytest.mark.asyncio
async def test_realtime_start_omits_audited_fields_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = SpawnHarness(monkeypatch)
    client = CodexAppServerClient()
    await client.ensure_started()
    client._trusted_binary_version = transport._SUPPORTED_CODEX_VERSION

    await client.realtime_start("thread-1", version="v3", trusted_prompt="Contract")

    params = _realtime_start_params(harness)
    for field_name in (
        "realtimeStartInstructions",
        "realtimeEndInstructions",
        "codexResponsesAsItems",
        "codexResponseItemPrefix",
        "codexResponseHandoffMode",
        "codexResponseHandoffChannelPrefixes",
    ):
        assert field_name not in params
    await client.close()


@pytest.mark.asyncio
async def test_realtime_start_never_sends_audited_fields_to_an_untrusted_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = SpawnHarness(monkeypatch)
    client = CodexAppServerClient()
    await client.ensure_started()
    client._trusted_binary_version = transport._LEGACY_CODEX_VERSION

    await client.realtime_start(
        "thread-1",
        version="v3",
        trusted_prompt="Contract",
        realtime_start_instructions="Speak as Jarvis.",
        codex_response_handoff_mode="items",
    )

    params = _realtime_start_params(harness)
    assert "realtimeStartInstructions" not in params
    assert "codexResponseHandoffMode" not in params
    assert "delegationAckFiller" not in params
    await client.close()


# --- (d) best-effort warm-audit priming inside the login gate ---------------


def _fail_config_requirements_after(first_ok: int) -> Callable[[FakeProcess, dict[str, Any]], None]:
    """Responder whose ``configRequirements/read`` errors after ``first_ok`` calls."""
    seen = {"count": 0}

    def responder(process: FakeProcess, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        if not isinstance(request_id, int):
            return
        if message.get("method") == "configRequirements/read":
            seen["count"] += 1
            if seen["count"] > first_ok:
                asyncio.get_running_loop().call_soon(
                    process.emit,
                    {
                        "id": request_id,
                        "error": {"code": -32000, "message": "transient hiccup"},
                    },
                )
                return
        _default_responder(process, message)

    return responder


@pytest.mark.asyncio
async def test_login_survives_a_transient_rpc_failure_in_warm_audit_priming(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A transient RPC error inside the priming audit must not fail the login
    result or touch the just-verified live process — only a WARNING is left."""
    harness = SpawnHarness(monkeypatch, _fail_config_requirements_after(first_ok=1))
    client = CodexAppServerClient()

    # Cold start audits in full (its configRequirements/read succeeds).
    await client.ensure_started()
    _age_stamp(client)
    client._warm_audit_process = None
    client._warm_audit_at = 0.0

    with caplog.at_level(logging.WARNING, logger="jarvis.codex_app_server"):
        await client.require_chatgpt_login()

    assert client.running is True
    assert harness.processes[0].returncode is None
    assert any(
        "warm-audit priming failed" in record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING and record.exc_info is not None
    )
    # No ride was minted from an audit that never completed.
    assert client._warm_audit_process is None
    await client.close()


@pytest.mark.asyncio
async def test_login_survives_a_subscription_refusal_in_warm_audit_priming(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A non-plan ``CodexSubscriptionUnavailable`` from the priming audit is
    ambiguous right after a proven account: log-and-continue, never close."""
    seen = {"count": 0}

    def responder(process: FakeProcess, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        if not isinstance(request_id, int):
            return
        if message.get("method") == "configRequirements/read":
            seen["count"] += 1
            if seen["count"] > 1:
                # A managed-requirements payload makes the audit refuse.
                _respond(process, request_id, {"requirements": {"managed": True}})
                return
        _default_responder(process, message)

    harness = SpawnHarness(monkeypatch, responder)
    client = CodexAppServerClient()

    await client.ensure_started()
    _age_stamp(client)
    client._warm_audit_process = None
    client._warm_audit_at = 0.0

    with caplog.at_level(logging.WARNING, logger="jarvis.codex_app_server"):
        await client.require_chatgpt_login()

    assert client.running is True
    assert harness.processes[0].returncode is None
    assert any(
        "warm-audit priming failed" in record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    )
    assert client._warm_audit_process is None
    await client.close()


# --- (e) initialItems trusted-binary gate ------------------------------------


@pytest.mark.asyncio
async def test_realtime_start_sends_initial_items_to_the_trusted_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = SpawnHarness(monkeypatch)
    client = CodexAppServerClient()
    await client.ensure_started()
    client._trusted_binary_version = transport._SUPPORTED_CODEX_VERSION

    await client.realtime_start(
        "thread-1",
        version="v3",
        trusted_prompt="Contract",
        initial_items=({"role": "user", "text": "hello"},),
    )

    params = _realtime_start_params(harness)
    assert params["initialItems"] == [{"role": "user", "text": "hello"}]
    await client.close()


@pytest.mark.asyncio
async def test_realtime_start_drops_initial_items_for_an_untrusted_binary(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An older approved binary never receives the unknown ``initialItems``
    field: a rebuild restores the call without history, with one warning."""
    harness = SpawnHarness(monkeypatch)
    client = CodexAppServerClient()
    await client.ensure_started()
    client._trusted_binary_version = transport._LEGACY_CODEX_VERSION

    with caplog.at_level(logging.WARNING, logger="jarvis.codex_app_server"):
        await client.realtime_start(
            "thread-1",
            version="v3",
            trusted_prompt="Contract",
            initial_items=({"role": "user", "text": "hello"},),
        )

    params = _realtime_start_params(harness)
    assert "initialItems" not in params
    dropped = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING and "initialItems" in record.getMessage()
    ]
    assert len(dropped) == 1
    await client.close()


# --- (f) realtimeStartInstructions capability probe --------------------------


def test_realtime_start_instructions_supported_tracks_the_trusted_binary() -> None:
    client = CodexAppServerClient()

    assert client.realtime_start_instructions_supported() is False
    client._trusted_binary_version = transport._LEGACY_CODEX_VERSION
    assert client.realtime_start_instructions_supported() is False
    client._trusted_binary_version = transport._SUPPORTED_CODEX_VERSION
    assert client.realtime_start_instructions_supported() is True


@pytest.mark.asyncio
async def test_realtime_start_rejects_invalid_audited_field_values() -> None:
    client = CodexAppServerClient()
    invalid_requests: tuple[dict[str, Any], ...] = (
        {"realtime_start_instructions": 42},
        {"realtime_end_instructions": "x" * (transport._MAX_REALTIME_START_PROMPT_BYTES + 1)},
        {"codex_responses_as_items": "yes"},
        {"codex_response_item_prefix": 7},
        {"codex_response_handoff_mode": ["items"]},
        {"codex_response_handoff_channel_prefixes": "not-a-sequence"},
        {"codex_response_handoff_channel_prefixes": ("ok", "")},
    )

    for kwargs in invalid_requests:
        with pytest.raises(CodexSubscriptionUnavailable):
            await client.realtime_start("thread-1", version="v3", **kwargs)
