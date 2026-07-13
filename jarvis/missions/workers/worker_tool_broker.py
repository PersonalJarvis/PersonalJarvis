"""Mission-scoped bridge from worker tools to the supervisor ToolExecutor.

CLI workers run the companion stdio MCP process and reach this broker over an
authenticated loopback-only HTTP endpoint.  API workers use the same binding
directly as an async callback.  In both cases the live supervisor owns the
actual tool object and executes it through ``ToolExecutor``; a worker never
receives connector credentials or a direct ``Tool.execute`` handle.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import sys
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Literal
from uuid import uuid4

from jarvis.core import runtime_refs
from jarvis.core.protocols import SupervisorToolDescriptor, SupervisorToolRequest
from jarvis.core.redact import safe_preview

logger = logging.getLogger(__name__)

BROKER_SERVER_ID = "jarvis_worker_tools"
BROKER_TOKEN_ENV = "JARVIS_WORKER_BROKER_TOKEN"  # noqa: S105 - env variable name
BROKER_URL_ENV = "JARVIS_WORKER_BROKER_URL"

_DEFAULT_TTL_S = 25 * 60.0
# Approval alone may consume ToolExecutor's 60-second window. Keep the process
# boundary open for the subsequent action and let mission cancellation revoke
# the grant and cancel the in-flight task. A 65-second cap left only five
# seconds for the real MCP/app call after a last-moment approval.
BROKER_EXECUTION_TIMEOUT_S = 15 * 60.0
_MAX_REQUEST_BYTES = 1024 * 1024

_FORBIDDEN_EXACT = frozenset(
    {
        "dispatch-with-review",
        "dispatch_with_review",
        "multi-spawn",
        "multi_spawn",
        "run-skill",
        "run_skill",
        "spawn-worker",
        "spawn_worker",
    }
)
_FORBIDDEN_NAME_FRAGMENTS = (
    "api-key",
    "api_key",
    "credential",
    "reveal-key",
    "reveal_key",
    "secret",
    "set-config",
    "set_config",
)

BrokerCallStatus = Literal[
    "active",
    "success",
    "denied",
    "cancelled",
    "timed_out",
    "error",
    "outcome_unknown",
]


@dataclass(frozen=True, slots=True)
class WorkerToolCallOutcome:
    """Credential-free terminal record for one supervisor tool call."""

    trace_id: str
    tool_name: str
    status: BrokerCallStatus
    error: str = ""


@dataclass(frozen=True, slots=True)
class WorkerToolExecutionSummary:
    """Completion certificate consumed by the mission controller."""

    calls: tuple[WorkerToolCallOutcome, ...] = ()

    @property
    def clean(self) -> bool:
        return all(call.status == "success" for call in self.calls)

    @property
    def active_count(self) -> int:
        return sum(call.status == "active" for call in self.calls)

    @property
    def failure_summary(self) -> str | None:
        failed = [call for call in self.calls if call.status != "success"]
        if not failed:
            return None
        preview = "; ".join(
            f"{call.tool_name}: {call.status}"
            + (f" ({call.error})" if call.error else "")
            for call in failed[:3]
        )
        if len(failed) > 3:
            preview += f"; +{len(failed) - 3} more"
        return safe_preview(preview, max_chars=300)


def worker_tool_name_allowed(name: str) -> bool:
    """Fail closed for recursion and credential/config mutation surfaces."""
    normalized = str(name or "").strip().lower()
    leaf = normalized.rsplit("/", 1)[-1].rsplit(":", 1)[-1].rsplit(".", 1)[-1]
    canonical_leaf = leaf.replace("_", "-")
    forbidden_leafs = {item.replace("_", "-") for item in _FORBIDDEN_EXACT}
    if not normalized or canonical_leaf in forbidden_leafs:
        return False
    return not any(part in normalized for part in _FORBIDDEN_NAME_FRAGMENTS)


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return json.loads(json.dumps(value, default=str))
    return value


def _tool_spec(descriptor: SupervisorToolDescriptor) -> dict[str, Any]:
    return {
        "name": descriptor.name,
        "description": descriptor.description,
        "input_schema": _json_safe(descriptor.input_schema),
    }


class _ScopeCancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._reason: str | None = None

    def cancel(self, reason: str) -> None:
        self._reason = reason
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    async def wait_until_cancelled(self) -> None:
        await asyncio.to_thread(self._event.wait)


@dataclass(slots=True)
class _BrokerScope:
    task_text: str
    gateway: Any
    loop: asyncio.AbstractEventLoop
    expires_at: float
    mcp_server_ids: tuple[str, ...]
    app_commands: tuple[str, ...]
    native_tool_names: tuple[str, ...]
    mission_id: str | None = None
    worker_id: str | None = None
    _revoked: bool = False
    _state_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _active_tasks: set[asyncio.Task[Any]] = field(default_factory=set, repr=False)
    _cancel_token: _ScopeCancelToken = field(default_factory=_ScopeCancelToken, repr=False)
    _calls: dict[str, WorkerToolCallOutcome] = field(default_factory=dict, repr=False)

    @property
    def active(self) -> bool:
        with self._state_lock:
            return not self._revoked and time.monotonic() < self.expires_at

    def _descriptors(self) -> tuple[SupervisorToolDescriptor, ...]:
        if not self.active:
            return ()
        allowed_exact = set(self.app_commands) | set(self.native_tool_names)
        prefixes = tuple(f"{server_id}/" for server_id in self.mcp_server_ids)
        return tuple(
            descriptor
            for descriptor in self.gateway.catalog()
            if (
                descriptor.name in allowed_exact
                or any(descriptor.name.startswith(prefix) for prefix in prefixes)
            )
            and worker_tool_name_allowed(descriptor.name)
        )

    @property
    def specs(self) -> tuple[dict[str, Any], ...]:
        return tuple(_tool_spec(descriptor) for descriptor in self._descriptors())

    def revoke(self, reason: str) -> None:
        with self._state_lock:
            if self._revoked:
                return
            self._revoked = True
            active_tasks = tuple(self._active_tasks)
            for trace_id, call in tuple(self._calls.items()):
                if call.status == "active":
                    self._calls[trace_id] = WorkerToolCallOutcome(
                        trace_id=trace_id,
                        tool_name=call.tool_name,
                        status="outcome_unknown",
                        error=safe_preview(reason, max_chars=120),
                    )
        self._cancel_token.cancel(reason)
        for task in active_tasks:
            self.loop.call_soon_threadsafe(task.cancel)

    def _start_call(self, trace_id: str, tool_name: str) -> None:
        with self._state_lock:
            self._calls[trace_id] = WorkerToolCallOutcome(
                trace_id=trace_id,
                tool_name=tool_name,
                status="active",
            )

    def _finish_call(
        self,
        trace_id: str,
        tool_name: str,
        status: BrokerCallStatus,
        error: str = "",
    ) -> None:
        with self._state_lock:
            current = self._calls.get(trace_id)
            if current is not None and current.status == "outcome_unknown":
                return
            self._calls[trace_id] = WorkerToolCallOutcome(
                trace_id=trace_id,
                tool_name=tool_name,
                status=status,
                error=safe_preview(error, max_chars=160),
            )

    @property
    def execution_summary(self) -> WorkerToolExecutionSummary:
        with self._state_lock:
            calls = tuple(self._calls.values())
        return WorkerToolExecutionSummary(calls=calls)

    async def wait_for_idle(self, timeout_s: float = 1.0) -> None:
        """Bounded quiescence wait after cancellation/revocation."""
        deadline = self.loop.time() + max(0.0, timeout_s)
        while self.loop.time() < deadline:
            with self._state_lock:
                if not self._active_tasks:
                    return
            await asyncio.sleep(0.01)

    @staticmethod
    def _denied(error: str = "Mission tool grant has expired.") -> dict[str, Any]:
        return {
            "status": "denied",
            "success": False,
            "output": None,
            "error": error,
        }

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        trace_id = uuid4()
        trace_key = str(trace_id)
        if not self.active:
            self._start_call(trace_key, name)
            self._finish_call(trace_key, name, "denied", "grant expired")
            return {**self._denied(), "trace_id": trace_key}
        granted_names = {descriptor.name for descriptor in self._descriptors()}
        if name not in granted_names or not worker_tool_name_allowed(name):
            self._start_call(trace_key, name)
            self._finish_call(trace_key, name, "denied", "tool not granted")
            return {
                **self._denied("Tool is not granted to this mission."),
                "trace_id": trace_key,
            }

        task = asyncio.current_task()
        if task is None:
            self._start_call(trace_key, name)
            self._finish_call(trace_key, name, "error", "missing task context")
            return {
                **self._denied("Mission tool execution has no task context."),
                "trace_id": trace_key,
            }
        with self._state_lock:
            if self._revoked or time.monotonic() >= self.expires_at:
                self._calls[trace_key] = WorkerToolCallOutcome(
                    trace_id=trace_key,
                    tool_name=name,
                    status="denied",
                    error="grant expired",
                )
                return {**self._denied(), "trace_id": trace_key}
            self._active_tasks.add(task)
            self._calls[trace_key] = WorkerToolCallOutcome(
                trace_id=trace_key,
                tool_name=name,
                status="active",
            )
        try:
            result = await self.gateway.execute(
                name,
                dict(arguments),
                SupervisorToolRequest(
                    trace_id=trace_id,
                    origin="mission_worker",
                    user_utterance=self.task_text,
                    rationale="Mission worker requested a supervisor-granted tool.",
                    mission_id=self.mission_id,
                    worker_id=self.worker_id,
                    config_snapshot={
                        "voice_confirm": False,
                        "worker_broker": True,
                    },
                    cancel_token=self._cancel_token,
                ),
            )
            if not self.active:
                self._finish_call(
                    trace_key,
                    name,
                    "outcome_unknown",
                    "grant ended during execution",
                )
                return {
                    **self._denied("Mission tool grant ended during execution."),
                    "status": "outcome_unknown" if result.success else "denied",
                    "trace_id": str(trace_id),
                }
            status = "ok" if result.success else "error"
            call_status: BrokerCallStatus = "success" if result.success else "error"
            if result.error and result.error.startswith("approval-denied"):
                status = "approval_denied"
                call_status = "denied"
            elif result.error and "cancelled" in result.error.lower():
                call_status = "cancelled"
            elif result.error and "timeout" in result.error.lower():
                call_status = "timed_out"
            self._finish_call(
                trace_key,
                name,
                call_status,
                result.error or "",
            )
            return {
                "status": status,
                "success": bool(result.success),
                "output": _json_safe(result.output),
                "error": result.error,
                "trace_id": str(trace_id),
            }
        except asyncio.CancelledError:
            self._finish_call(
                trace_key,
                name,
                "outcome_unknown",
                self._cancel_token.reason or "execution cancelled",
            )
            raise
        except TimeoutError as exc:
            self._finish_call(trace_key, name, "timed_out", str(exc))
            raise
        except Exception as exc:
            self._finish_call(trace_key, name, "error", str(exc))
            raise
        finally:
            with self._state_lock:
                self._active_tasks.discard(task)


@dataclass(slots=True)
class WorkerToolBrokerBinding:
    """One short-lived mission grant shared by an API or CLI worker."""

    url: str
    _token: str = field(repr=False)
    _scope: _BrokerScope = field(repr=False)
    _broker: Any = field(repr=False)
    _closed: bool = field(default=False, repr=False)

    @property
    def available(self) -> bool:
        return not self._closed and self._scope.active and bool(self.tool_specs)

    @property
    def tool_specs(self) -> tuple[dict[str, Any], ...]:
        if self._closed or not self._scope.active:
            return ()
        return self._scope.specs

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(str(spec["name"]) for spec in self.tool_specs)

    @property
    def execution_summary(self) -> WorkerToolExecutionSummary:
        return self._scope.execution_summary

    def apply_environment(self, env: dict[str, str]) -> dict[str, str]:
        """Return a copy with the bearer in env only, never argv/config/logs."""
        out = dict(env)
        if self.available:
            out[BROKER_URL_ENV] = self.url
            out[BROKER_TOKEN_ENV] = self._token
        return out

    def mcp_server_config(self) -> dict[str, dict[str, Any]]:
        if not self.available:
            return {}
        if bool(getattr(sys, "frozen", False)):
            adapter_args = ["--worker-tool-broker-stdio"]
        else:
            adapter_args = ["-m", "jarvis.missions.workers.broker_stdio"]
        return {
            BROKER_SERVER_ID: {
                "command": sys.executable,
                "args": adapter_args,
            }
        }

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._closed or not self._scope.active:
            self.close()
            return self._scope._denied()
        return await self._scope.execute(name, arguments)

    def close(self) -> None:
        if not self._closed:
            self._broker.revoke(self._token)
            self._closed = True

    async def aclose(self) -> None:
        self.close()
        await self._scope.wait_for_idle()

    def __del__(self) -> None:
        # Generator cancellation and provider fallback can skip a normal return;
        # finalization revokes the grant while the TTL remains the hard backstop.
        with suppress(Exception):
            self.close()


@dataclass(slots=True)
class EmptyWorkerToolBrokerBinding:
    """Clean, inert grant used when an iteration has no supervisor tools."""

    available: bool = False
    tool_specs: tuple[dict[str, Any], ...] = ()
    tool_names: tuple[str, ...] = ()

    @property
    def execution_summary(self) -> WorkerToolExecutionSummary:
        return WorkerToolExecutionSummary()

    def apply_environment(self, env: dict[str, str]) -> dict[str, str]:
        return dict(env)

    def mcp_server_config(self) -> dict[str, dict[str, Any]]:
        return {}

    async def execute(self, _name: str, _arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "denied",
            "success": False,
            "output": None,
            "error": "No supervisor tools are granted to this mission.",
        }

    def close(self) -> None:
        return

    async def aclose(self) -> None:
        return


class _LoopbackServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, broker: WorkerToolBroker) -> None:
        self.broker = broker
        super().__init__(("127.0.0.1", 0), _BrokerRequestHandler)


class _BrokerRequestHandler(BaseHTTPRequestHandler):
    server: _LoopbackServer

    def log_message(self, _format: str, *_args: Any) -> None:
        # The default handler logs request paths and client data to stderr.
        # Worker grants are intentionally silent; never risk logging a bearer.
        return

    def _scope(self) -> _BrokerScope | None:
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            return None
        return self.server.broker.lookup(header[len(prefix) :])

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        scope = self._scope()
        if scope is None:
            self._send(401, {"error": "unauthorized"})
            return
        if self.path != "/v1/tools":
            self._send(404, {"error": "not found"})
            return
        self._send(200, {"tools": list(scope.specs)})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        scope = self._scope()
        if scope is None:
            self._send(401, {"error": "unauthorized"})
            return
        if self.path != "/v1/execute":
            self._send(404, {"error": "not found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = -1
        if size < 0 or size > _MAX_REQUEST_BYTES:
            self._send(413, {"error": "request too large"})
            return
        try:
            payload = json.loads(self.rfile.read(size) or b"{}")
            name = str(payload["name"])
            arguments = payload.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise TypeError("arguments must be an object")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._send(400, {"error": str(exc)})
            return
        future = asyncio.run_coroutine_threadsafe(
            scope.execute(name, arguments), scope.loop
        )
        try:
            result = future.result(timeout=BROKER_EXECUTION_TIMEOUT_S)
        except TimeoutError:
            future.cancel()
            self._send(504, {"error": "supervisor tool execution timed out"})
            return
        except Exception as exc:  # noqa: BLE001 - fail closed at process boundary
            logger.warning(
                "Worker tool broker request failed: %s",
                safe_preview(type(exc).__name__, max_chars=80),
            )
            self._send(500, {"error": "supervisor tool execution failed"})
            return
        self._send(200, result)


class WorkerToolBroker:
    """Process-local registry plus a loopback-only authenticated HTTP seam."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._scopes: dict[str, _BrokerScope] = {}
        self._server: _LoopbackServer | None = None
        self._thread: threading.Thread | None = None

    def _ensure_server(self) -> _LoopbackServer:
        with self._lock:
            if self._server is None:
                server = _LoopbackServer(self)
                thread = threading.Thread(
                    target=server.serve_forever,
                    name="jarvis-worker-tool-broker",
                    daemon=True,
                )
                thread.start()
                self._server = server
                self._thread = thread
            return self._server

    def issue(
        self,
        *,
        task_text: str,
        mcp_server_ids: tuple[str, ...],
        app_commands: tuple[str, ...],
        native_tool_names: tuple[str, ...],
        ttl_s: float = _DEFAULT_TTL_S,
        mission_id: str | None = None,
        worker_id: str | None = None,
    ) -> WorkerToolBrokerBinding | None:
        """Resolve a live, credential-aware grant from the supervisor tools."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        gateway = runtime_refs.get_supervisor_tool_gateway()
        if gateway is None:
            return None

        scope = _BrokerScope(
            task_text=task_text,
            gateway=gateway,
            loop=loop,
            expires_at=time.monotonic() + max(1.0, float(ttl_s)),
            mcp_server_ids=tuple(dict.fromkeys(mcp_server_ids)),
            app_commands=tuple(dict.fromkeys(app_commands)),
            native_tool_names=tuple(dict.fromkeys(native_tool_names)),
            mission_id=mission_id,
            worker_id=worker_id,
        )
        if not scope.specs:
            return None
        server = self._ensure_server()
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._reap_locked()
            self._scopes[token] = scope
        host, port = server.server_address[:2]
        return WorkerToolBrokerBinding(
            url=f"http://{host}:{port}",
            _token=token,
            _scope=scope,
            _broker=self,
        )

    def _reap_locked(self) -> None:
        now = time.monotonic()
        expired = [token for token, scope in self._scopes.items() if scope.expires_at <= now]
        for token in expired:
            scope = self._scopes.pop(token, None)
            if scope is not None:
                scope.revoke("grant_expired")

    def lookup(self, token: str) -> _BrokerScope | None:
        with self._lock:
            self._reap_locked()
            # Iterate with compare_digest so token lookup has no early-exit
            # comparison on a secret supplied by an untrusted worker process.
            for expected, scope in self._scopes.items():
                if secrets.compare_digest(expected, token):
                    return scope
        return None

    def revoke(self, token: str) -> None:
        with self._lock:
            scope = self._scopes.pop(token, None)
        if scope is not None:
            scope.revoke("grant_revoked")

    def reset_for_tests(self) -> None:
        with self._lock:
            scopes = tuple(self._scopes.values())
            self._scopes.clear()
            server = self._server
            self._server = None
            self._thread = None
        for scope in scopes:
            scope.revoke("broker_reset")
        if server is not None:
            server.shutdown()
            server.server_close()


_BROKER = WorkerToolBroker()


def issue_worker_tool_binding(
    *,
    task_text: str,
    mcp_server_ids: tuple[str, ...],
    app_commands: tuple[str, ...],
    native_tool_names: tuple[str, ...],
    ttl_s: float = _DEFAULT_TTL_S,
    mission_id: str | None = None,
    worker_id: str | None = None,
) -> WorkerToolBrokerBinding | None:
    return _BROKER.issue(
        task_text=task_text,
        mcp_server_ids=mcp_server_ids,
        app_commands=app_commands,
        native_tool_names=native_tool_names,
        ttl_s=ttl_s,
        mission_id=mission_id,
        worker_id=worker_id,
    )


__all__ = [
    "BROKER_EXECUTION_TIMEOUT_S",
    "BROKER_SERVER_ID",
    "BROKER_TOKEN_ENV",
    "BROKER_URL_ENV",
    "EmptyWorkerToolBrokerBinding",
    "WorkerToolCallOutcome",
    "WorkerToolBrokerBinding",
    "WorkerToolExecutionSummary",
    "issue_worker_tool_binding",
    "worker_tool_name_allowed",
]
