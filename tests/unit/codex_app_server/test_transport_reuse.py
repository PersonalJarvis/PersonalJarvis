"""Regression tests for what makes a SECOND Codex voice call slow.

Three defects made every repeat call pay a cold start or an avoidable audit:

* a closed/poisoned client stayed in the shared registry and was handed to the
  next caller, whose ``ensure_started`` respawned the whole process tree;
* the ~100 MB native-binary digest expired on a wall-clock timer, so a call
  outside that window re-hashed the executable several times;
* ``thread_start`` repeated the identical startup audit microseconds after
  ``ensure_started`` had just performed it.

Every subprocess, pipe, and process-tree container here is a local fake; the
suite never starts the Codex CLI or touches the network.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

import jarvis.codex_app_server as transport
from jarvis.codex_app_server import (
    CodexAppServerCapability,
    CodexAppServerClient,
    CodexSubscriptionUnavailable,
)

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
    monkeypatch.setattr(transport, "_shared_clients", {})
    monkeypatch.setattr(transport, "_verify_spawn_binary", lambda _path: None)


class FakeStdin:
    def __init__(self, process: FakeProcess) -> None:
        self.process = process
        self.messages: list[dict[str, Any]] = []
        self.closed = False

    def write(self, payload: bytes) -> None:
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
    _next_pid = 7100

    def __init__(self) -> None:
        type(self)._next_pid += 1
        self.pid = type(self)._next_pid
        self.returncode: int | None = None
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdin = FakeStdin(self)
        self._waited = asyncio.Event()

    def on_client_message(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        if not isinstance(request_id, int):
            return
        method = message.get("method")
        if method == "account/read":
            result: Any = {
                "account": {"type": "chatgpt", "planType": "plus"},
                "requiresOpenaiAuth": True,
            }
        elif method == "configRequirements/read":
            result = {"requirements": None}
        elif method == "config/read":
            result = {
                "config": {},
                "layers": [{"name": "sessionFlags", "config": {}}],
            }
        elif method == "thread/start":
            result = {"thread": {"id": "thread-1"}}
        else:
            result = {}
        asyncio.get_running_loop().call_soon(
            self.emit, {"id": request_id, "result": result}
        )

    def emit(self, message: dict[str, Any]) -> None:
        self.stdout.feed_data(
            json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
        )

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


class Harness:
    """Fake spawn plumbing plus a counter for every capability probe."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.processes: list[FakeProcess] = []
        self.trees: list[FakeProcessTree] = []
        self.capability_probes = 0
        self.home_validations = 0
        self.codex_home = Path("C:/isolated-jarvis-codex-voice-test")
        self._pending_tree: FakeProcessTree | None = None

        capability = CodexAppServerCapability(
            available=True,
            chatgpt_authenticated=True,
            binary_path="codex-test",
            version="codex-test 1.0",
            reason="ready",
        )

        def probe(_binary: str | None) -> CodexAppServerCapability:
            self.capability_probes += 1
            return capability

        def validate_home(**_kwargs: object) -> Path:
            self.home_validations += 1
            return self.codex_home

        def make_tree(_name: str) -> FakeProcessTree:
            tree = FakeProcessTree()
            self._pending_tree = tree
            return tree

        async def spawn(*_args: Any, **_kwargs: Any) -> FakeProcess:
            process = FakeProcess()
            tree = self._pending_tree
            assert tree is not None
            self._pending_tree = None
            self.processes.append(process)
            self.trees.append(tree)
            return process

        monkeypatch.setattr(transport, "_read_codex_capability", probe)
        monkeypatch.setattr(transport, "_validated_subscription_home", validate_home)
        monkeypatch.setattr(transport, "make_process_tree", make_tree)
        monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", spawn)
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

    def methods(self, index: int = 0) -> list[str]:
        return [
            str(message.get("method"))
            for message in self.processes[index].stdin.messages
        ]


@pytest.mark.asyncio
async def test_a_poisoned_client_is_never_handed_out_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Poisoning retires the client instead of leaving a corpse in the registry."""
    Harness(monkeypatch)
    client = transport.get_shared_codex_app_server("codex-test")
    await client.ensure_started()

    # What the provider does when a cleanup RPC times out.
    await client.poison()

    replacement = transport.get_shared_codex_app_server("codex-test")
    assert replacement is not client
    assert replacement.ready is False
    await replacement.ensure_started()
    assert replacement.ready is True
    await replacement.close()


@pytest.mark.asyncio
async def test_close_is_idempotent_and_a_closed_client_refuses_to_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale holder must not resurrect a retired client and fight its
    successor for the single-owner profile."""
    Harness(monkeypatch)
    client = transport.get_shared_codex_app_server("codex-test")
    await client.ensure_started()

    await client.close()
    await client.close()

    with pytest.raises(CodexSubscriptionUnavailable, match="was closed"):
        await client.ensure_started()
    with transport._shared_clients_lock:
        assert client not in transport._shared_clients.values()


@pytest.mark.asyncio
async def test_a_failed_start_also_retires_the_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The provider closes the client it started when activation fails; that
    close must retire it even though the start never completed."""
    harness = Harness(monkeypatch)
    monkeypatch.setattr(
        transport,
        "_read_codex_capability",
        lambda _binary: CodexAppServerCapability(
            available=False,
            chatgpt_authenticated=False,
            binary_path=None,
            version=None,
            reason="Codex CLI is not installed.",
            reason_code="not_installed",
        ),
    )
    client = transport.get_shared_codex_app_server("codex-test")

    with pytest.raises(CodexSubscriptionUnavailable, match="not installed"):
        await client.ensure_started()
    await client.close()

    assert harness.processes == []
    assert transport.get_shared_codex_app_server("codex-test") is not client


@pytest.mark.asyncio
async def test_shutdown_sweep_still_closes_and_clears_the_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Eviction inside close must not break the shutdown sweep."""
    harness = Harness(monkeypatch)
    client = transport.get_shared_codex_app_server("codex-test")
    await client.ensure_started()

    await transport.close_shared_codex_app_servers()

    assert harness.processes[0].returncode is not None
    with transport._shared_clients_lock:
        assert transport._shared_clients == {}
    with transport._subscription_login_lock:
        assert transport._subscription_active_transports == set()
        assert transport._subscription_transport_owner is None


@pytest.mark.asyncio
async def test_second_event_loop_is_refused_with_an_actionable_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Harness(monkeypatch)
    owner = transport.get_shared_codex_app_server("codex-test")
    await owner.ensure_started()

    def acquire_elsewhere() -> BaseException | None:
        async def acquire() -> BaseException | None:
            try:
                transport.get_shared_codex_app_server("codex-test")
            except BaseException as exc:  # noqa: BLE001 - returned for assertion
                return exc
            return None

        return asyncio.run(acquire())

    error = await asyncio.to_thread(acquire_elsewhere)

    assert isinstance(error, CodexSubscriptionUnavailable)
    message = str(error)
    # Actionable: it names what to do, not an internal ownership concept.
    assert "already running elsewhere" in message
    assert "End the active voice session" in message
    await owner.close()


@pytest.mark.asyncio
async def test_a_client_whose_loop_died_is_replaced_and_its_claim_reclaimed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A loop that dies mid-call must not park subscription voice forever."""
    Harness(monkeypatch)
    dead_loop = asyncio.new_event_loop()
    orphan = CodexAppServerClient("codex-test")
    orphan._owner_loop = dead_loop
    epoch = await asyncio.to_thread(
        transport._reserve_subscription_transport, orphan
    )
    assert epoch == 1
    with transport._shared_clients_lock:
        transport._shared_clients["codex-test"] = orphan
    dead_loop.close()

    replacement = transport.get_shared_codex_app_server("codex-test")

    assert replacement is not orphan
    await replacement.ensure_started()
    assert replacement.ready is True
    with transport._subscription_login_lock:
        assert transport._subscription_active_transports == {id(replacement)}
        assert transport._subscription_transport_owner is replacement
    await replacement.close()


@pytest.mark.asyncio
async def test_first_thread_start_rides_the_startup_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cold start audits profile, account, requirements and config; the
    thread start it was triggered by must not repeat all four."""
    harness = Harness(monkeypatch)
    client = CodexAppServerClient("codex-test")

    await client.thread_start()

    methods = harness.methods()
    assert methods.count("account/read") == 1
    assert methods.count("configRequirements/read") == 1
    assert methods.count("config/read") == 1
    assert methods.count("thread/start") == 1
    # Two profile walks belong to startup (pre-spawn and post-audit); the
    # thread start added none.
    assert harness.home_validations == 2

    await client.thread_start()

    methods = harness.methods()
    assert methods.count("account/read") == 2
    assert methods.count("configRequirements/read") == 2
    assert methods.count("config/read") == 2
    assert methods.count("thread/start") == 2
    assert harness.home_validations == 3
    await client.close()


@pytest.mark.asyncio
async def test_a_respawn_never_lets_a_thread_start_ride_a_stale_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The audit token is bound to the process it covers."""
    harness = Harness(monkeypatch)
    client = CodexAppServerClient("codex-test")
    await client.ensure_started()
    assert client._startup_audit_process is harness.processes[0]

    harness.processes[0].finish(1)
    for _ in range(200):
        if client._process is None:
            break
        await asyncio.sleep(0.01)
    assert client._process is None
    assert client._startup_audit_process is None

    await client.thread_start()

    # The restart audited the NEW process, and the thread start rode that one.
    assert len(harness.processes) == 2
    assert harness.methods(1).count("account/read") == 1
    assert harness.methods(1).count("thread/start") == 1
    await client.close()


@pytest.mark.asyncio
async def test_teardown_rpcs_never_restart_a_dead_app_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup must not pay a cold start for a thread that died with its
    process — that is what made the caller's short cleanup budget expire and
    poison the client on the way out of every rough call."""
    harness = Harness(monkeypatch)
    client = CodexAppServerClient("codex-test")
    await client.ensure_started()

    harness.processes[0].finish(1)
    for _ in range(200):
        if client._process is None:
            break
        await asyncio.sleep(0.01)

    assert await client.realtime_stop("thread-1") == {}
    assert await client.thread_unsubscribe("thread-1") == {}
    assert await client.turn_interrupt("thread-1", "turn-1") == {}
    assert len(harness.processes) == 1

    # A LIVE process still gets the real frames.
    await client.ensure_started()
    await client.realtime_stop("thread-1")
    assert "thread/realtime/stop" in harness.methods(1)
    await client.close()


def test_binary_digest_is_memoized_for_the_process_lifetime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The ~100 MB hash is paid once per binary identity, not once per minute."""
    binary = tmp_path / "codex.exe"
    binary.write_bytes(b"approved-content")
    hashes = 0
    real_sha256_file = transport._sha256_file

    def counting_sha256(path: Path) -> str:
        nonlocal hashes
        hashes += 1
        return real_sha256_file(path)

    monkeypatch.setattr(transport, "_sha256_file", counting_sha256)

    digest = transport._sha256_file_cached(binary)
    for _ in range(20):
        assert transport._sha256_file_cached(binary) == digest
    assert hashes == 1

    # No wall-clock expiry left to wait out.
    assert not hasattr(transport, "_SHA256_CACHE_TTL_S")


def test_binary_digest_is_recomputed_when_the_binary_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Identity is re-read on EVERY call: a changed file is never served from
    the memo, whatever its age."""
    binary = tmp_path / "codex.exe"
    binary.write_bytes(b"approved-content")
    hashes = 0
    real_sha256_file = transport._sha256_file

    def counting_sha256(path: Path) -> str:
        nonlocal hashes
        hashes += 1
        return real_sha256_file(path)

    monkeypatch.setattr(transport, "_sha256_file", counting_sha256)

    first = transport._sha256_file_cached(binary)
    binary.write_bytes(b"swapped-content-of-a-different-size")
    second = transport._sha256_file_cached(binary)

    assert second != first
    assert hashes == 2
    assert second == real_sha256_file(binary)


def test_a_refused_spawn_verification_purges_the_memo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The un-memoized spawn check is the memo's corrector: once it refuses a
    binary, no later status read may serve the approved digest from cache."""
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
    transport._sha256_file_cached(binary)
    key = transport.os.path.normcase(str(binary))
    assert transport._sha256_cache[key][1] == approved

    _REAL_VERIFY_SPAWN_BINARY(str(binary))
    assert transport._sha256_cache[key][1] == approved

    # Forge the identity so the memo would otherwise keep serving "approved".
    identity = binary.stat()
    binary.write_bytes(b"swapped-content")
    transport.os.utime(binary, ns=(identity.st_atime_ns, identity.st_mtime_ns))
    with transport._sha256_cache_lock:
        transport._sha256_cache[key] = (
            transport._file_identity_signature(identity),
            approved,
        )

    with pytest.raises(CodexSubscriptionUnavailable, match="changed since"):
        _REAL_VERIFY_SPAWN_BINARY(str(binary))
    assert key not in transport._sha256_cache


def test_billing_tokens_never_reach_the_child_even_via_the_allowlist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The declared billing denylist is enforced, not merely documented."""
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-child")
    monkeypatch.setattr(
        transport,
        "_SUBSCRIPTION_ENV_ALLOWLIST",
        frozenset({*transport._SUBSCRIPTION_ENV_ALLOWLIST, "OPENAI_API_KEY"}),
    )
    workspace = _fake_workspace(tmp_path)

    environment = transport._subscription_environment(tmp_path / "home", workspace)

    for name in transport._OPENAI_BILLING_ENV_NAMES:
        assert name not in environment


def test_locale_and_tls_trust_variables_reach_the_transport_child(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The login child keeps these; the transport dropping them meant `codex
    login status` succeeded (a local file read) while every call died in the
    TLS handshake on hosts whose trust roots live in the environment."""
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    monkeypatch.setenv("LC_CTYPE", "de_DE.UTF-8")
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("SSL_CERT_FILE", "/etc/ssl/certs/ca-bundle.crt")
    monkeypatch.setenv("SSL_CERT_DIR", "/etc/ssl/certs")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/etc/ssl/certs/ca-bundle.crt")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-reach-child")
    monkeypatch.setenv("HTTPS_PROXY", "http://must-not-reach-child.invalid")
    workspace = _fake_workspace(tmp_path)

    environment = transport._subscription_environment(tmp_path / "home", workspace)

    for name in (
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TERM",
        "NO_COLOR",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
    ):
        assert environment[name] == transport.os.environ[name]
    for name in ("AWS_SECRET_ACCESS_KEY", "HTTPS_PROXY"):
        assert name not in environment


def test_desktop_sidecar_files_do_not_invalidate_the_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Opening the profile in Finder or Explorer must not cost the login."""
    import jarvis.core.paths as paths

    monkeypatch.setattr(paths, "user_data_dir", lambda: tmp_path)
    home = transport._prepare_subscription_login_home()
    (home / "auth.json").write_text('{"tokens":{}}', encoding="utf-8")
    if transport.os.name == "posix":
        transport.os.chmod(home / "auth.json", 0o600)
    for sidecar in (".DS_Store", "Thumbs.db", "desktop.ini", "._auth.json"):
        (home / sidecar).write_bytes(b"shell metadata")

    assert (
        transport._validated_subscription_home(create=False, require_marker=True)
        == home
    )
    # The Connect path must not treat them as "profile contains configuration".
    assert transport._prepare_subscription_login_home() == home
    assert (home / "auth.json").exists()


def test_a_missing_optional_runtime_alias_is_not_a_broken_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The exact file set was only ever measured on Windows; one alias fewer
    on another OS must not read as tampering."""
    binary, runtime = _runtime_fixture(monkeypatch, tmp_path, alias_count=1)

    transport._validate_arg0_runtime_dir(
        runtime, trusted_binary_path=str(binary)
    )

    # An unknown NAME is still refused — the allowlist did not become "anything".
    (runtime / "rogue-helper").write_text("leftover", encoding="utf-8")
    with pytest.raises(transport.CodexSubscriptionRuntimeStateInvalid):
        transport._validate_arg0_runtime_dir(
            runtime, trusted_binary_path=str(binary)
        )


def test_an_unknown_runtime_file_never_deletes_the_login(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Codex's scratch is regenerated on the next run; the ChatGPT login next
    to it is not. Connect clears only the scratch."""
    import jarvis.core.paths as paths

    binary = _trusted_linux_binary(monkeypatch, tmp_path)
    monkeypatch.setattr(paths, "user_data_dir", lambda: tmp_path / "data")
    home = transport._prepare_subscription_login_home()
    (home / "auth.json").write_text('{"tokens":{}}', encoding="utf-8")
    if transport.os.name == "posix":
        transport.os.chmod(home / "auth.json", 0o600)
    runtime = home / "tmp" / "arg0" / "codex-arg0Ab12Cd"
    runtime.mkdir(parents=True)
    if transport.os.name == "posix":
        transport.os.chmod(runtime.parent, 0o700)
    (runtime / ".lock").write_bytes(b"")
    (runtime / "unexpected-helper").write_text("leftover", encoding="utf-8")

    with pytest.raises(transport.CodexSubscriptionRuntimeStateInvalid):
        transport._validated_subscription_home(
            create=False,
            require_marker=True,
            trusted_binary_path=str(binary),
        )

    recovered = transport._prepare_subscription_login_home()

    assert recovered == home
    assert (home / "auth.json").read_text(encoding="utf-8") == '{"tokens":{}}'
    assert not (home / "tmp").exists()


def test_rpc_error_forwards_status_and_type_but_nothing_else() -> None:
    """A plan/quota refusal and a broken transport need opposite reactions;
    the two machine-readable fields that tell them apart are forwarded."""
    status, token = transport._rpc_error_detail(
        {
            "code": -32000,
            "message": "account@example.invalid exceeded plan; bearer-secret",
            "data": {"httpStatus": 429, "type": "usage_limit_reached"},
        }
    )

    assert (status, token) == (429, "usage_limit_reached")
    error = transport.CodexAppServerRPCError(
        "thread/realtime/start", -32000, http_status=status, error_type=token
    )
    text = str(error)
    assert "429" in text
    assert "usage_limit_reached" in text
    assert "account@example.invalid" not in text
    assert "bearer-secret" not in text
    # The shared classifier now reads the account state instead of "error".
    from jarvis.brain.provider_test import classify_provider_error

    assert classify_provider_error(text) == "no_credits"


def test_rpc_error_redacts_free_text_and_out_of_range_values() -> None:
    status, token = transport._rpc_error_detail(
        {
            "code": -32000,
            "data": {
                "httpStatus": 9000,
                "type": "Sorry user@example.invalid, your card was declined",
            },
        }
    )

    assert (status, token) == (None, None)
    assert str(transport.CodexAppServerRPCError("turn/start", -32000)) == (
        "Codex app-server rejected turn/start (code -32000)."
    )


def _trusted_linux_binary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Pin a fake approved Codex binary for the host's own runtime layout."""
    host_platform = "win32" if transport.os.name == "nt" else "linux"
    monkeypatch.setattr(transport.sys, "platform", host_platform)
    monkeypatch.setattr(transport, "_normalized_machine", lambda: "x86_64")
    monkeypatch.setattr(
        transport,
        "_TRUSTED_CODEX_TARGETS",
        {
            (host_platform, "x86_64"): (
                "linux-x64",
                "x86_64-unknown-linux-musl",
                "codex",
                "approved",
            )
        },
    )
    binary = tmp_path / ("codex.exe" if host_platform == "win32" else "codex")
    binary.write_bytes(b"official binary")
    monkeypatch.setattr(
        transport,
        "_sha256_file",
        lambda path: (
            "approved" if Path(path).resolve() == binary.resolve() else "wrong"
        ),
    )
    return binary.resolve()


def _runtime_fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    alias_count: int,
) -> tuple[Path, Path]:
    """Build an arg0 scratch dir in the shape THIS host's Codex would write.

    The scratch lives inside a real Jarvis-owned profile so its entries carry
    the owner-only ACL the validator requires on Windows.
    """
    import jarvis.core.paths as paths

    binary = _trusted_linux_binary(monkeypatch, tmp_path)
    monkeypatch.setattr(paths, "user_data_dir", lambda: tmp_path / "data")
    home = transport._prepare_subscription_login_home()
    runtime = home / "tmp" / "arg0" / "codex-arg0Ab12Cd"
    runtime.mkdir(parents=True)
    if transport.os.name == "posix":
        transport.os.chmod(runtime, 0o700)
    (runtime / ".lock").write_bytes(b"")
    if transport.os.name == "nt":
        names = ("apply_patch.bat", "applypatch.bat")[:alias_count]
        for name in names:
            (runtime / name).write_text(
                f'@echo off\n"{binary}" --codex-run-as-apply-patch %*\n',
                encoding="utf-8",
            )
    else:
        names = ("apply_patch", "applypatch")[:alias_count]
        for name in names:
            try:
                transport.os.symlink(binary, runtime / name)
            except OSError as exc:
                pytest.skip(f"symlink creation is unavailable: {type(exc).__name__}")
    return binary, runtime


def _fake_workspace(root: Path) -> Any:
    def child(name: str) -> Path:
        path = root / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    return transport._SafeTransportWorkspace(
        root=root,
        instructions=root / "instructions.md",
        compact_prompt=root / "compact.md",
        model_catalog=root / "catalog.json",
        sqlite_home=child("sqlite"),
        log_dir=child("logs"),
        child_home=child("home"),
        child_appdata=child("appdata"),
        child_local_appdata=child("local-appdata"),
        child_tmp=child("tmp"),
    )
