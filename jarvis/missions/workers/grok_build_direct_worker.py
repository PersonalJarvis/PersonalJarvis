"""GrokBuildDirectWorker — drive the official Grok Build ``grok`` CLI.

Bills mission work against the user's SuperGrok / X Premium+ subscription
(OAuth via ``~/.grok/auth.json``). Sibling of :class:`CodexDirectWorker` and
:class:`GoogleCliWorker`.

Headless invocation (official docs):

    grok --no-auto-update --no-alt-screen --always-approve \\
         --output-format streaming-json --cwd <worktree> [-m <model>] -p <prompt>

The worker:

* isolates ``GROK_HOME`` to an auth-only copy so user hooks/plugins from the
  interactive TUI cannot run inside a mission;
* drops ``XAI_API_KEY`` / ``GROK_API_KEY`` so the subscription login wins;
* translates streaming-json NDJSON into the WorkerProtocol's Claude-shaped
  events so the Critic loop stays CLI-agnostic.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path
from typing import Any, ClassVar, Literal

from jarvis.grok_build_auth import grok_home, prepare_worker_home

from .capabilities import WorkerCapabilityInventory
from .process_utils import create_worker_subprocess
from .stream_consumer import ClaudeAssistantMessage, ClaudeResult, ClaudeSystemInit

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S: float = 1200.0
_DEFAULT_FIRST_OUTPUT_TIMEOUT_S: float = 120.0
_STREAM_READLINE_LIMIT: int = 8 * 1024 * 1024
_HARDCAP_GRACE_S: float = 30.0
_DEFAULT_GROK_BUILD_MODEL: str = "grok-4.6"

_DROP_ENV: tuple[str, ...] = ("XAI_API_KEY", "GROK_API_KEY")

_AUTH_FAILURE_MARKERS: tuple[str, ...] = (
    "not logged in",
    "please log in",
    "please login",
    "run grok login",
    "unauthorized",
    "401",
    "authentication failed",
    "auth error",
    "token expired",
    "expired token",
    "invalid token",
)

_USAGE_LIMIT_MARKERS: tuple[str, ...] = (
    "usage limit",
    "rate limit",
    "rate_limit",
    "too many requests",
    "429",
    "quota",
    "try again later",
    "try again at",
)

_FOREIGN_MODEL_PREFIXES: tuple[str, ...] = (
    "claude",
    "gpt-",
    "o1",
    "o3",
    "o4",
    "gemini",
    "sonnet",
    "opus",
    "haiku",
    "fable",
)


def _resolve_grok_binary() -> str | None:
    try:
        from jarvis.core.path_augment import ensure_cli_paths

        ensure_cli_paths()
    except Exception as exc:  # noqa: BLE001 — PATH augmentation is best-effort
        logger.debug("CLI PATH augmentation failed during grok discovery: %s", exc)
    for name in ("grok", "grok.exe", "grok.cmd"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _normalize_model_for_grok_build(model: str) -> str:
    """Keep a Grok-family model; drop foreign slugs so the CLI default wins."""
    raw = (model or "").strip()
    if not raw:
        return ""
    low = raw.lower()
    if any(low.startswith(prefix) for prefix in _FOREIGN_MODEL_PREFIXES):
        return ""
    if low.startswith("x-ai/"):
        return raw.split("/", 1)[1]
    return raw


def _build_grok_build_cmd(
    *,
    binary: str,
    prompt: str,
    worktree: Path,
    model: str = "",
    extra_args: tuple[str, ...] = (),
) -> list[str]:
    """Headless Grok Build argv. Prompt is one ``-p`` argument (official flag)."""
    safe_prompt = " ".join(prompt.split())
    cmd: list[str] = [
        binary,
        "--no-auto-update",
        "--no-alt-screen",
        "--always-approve",
        "--output-format",
        "streaming-json",
        "--cwd",
        str(worktree),
    ]
    effective = _normalize_model_for_grok_build(model)
    if effective:
        cmd.extend(["-m", effective])
    cmd.extend(["-p", safe_prompt])
    cmd.extend(extra_args)
    return cmd


def _build_grok_build_env(base_env: dict[str, str]) -> dict[str, str]:
    """Drop API keys so the SuperGrok login wins; isolate GROK_HOME."""
    env = {k: v for k, v in base_env.items() if k not in _DROP_ENV}
    isolated = prepare_worker_home()
    if isolated is not None:
        env["GROK_HOME"] = str(isolated)
    else:
        env["GROK_HOME"] = str(grok_home())
    return env


def _coerce_error_text(obj: dict[str, Any]) -> str:
    for key in ("message", "error", "detail", "text"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, dict):
            for inner in ("message", "error", "text", "detail"):
                iv = val.get(inner)
                if isinstance(iv, str) and iv.strip():
                    return iv.strip()
    return str(obj.get("type") or "grok-build error")


def _is_auth_failure(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _AUTH_FAILURE_MARKERS)


def _is_usage_limited(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _USAGE_LIMIT_MARKERS)


def _text_from_event(obj: dict[str, Any]) -> str | None:
    """Best-effort assistant text from a Grok Build streaming-json event."""
    for key in ("text", "delta", "content", "message"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val
        if isinstance(val, dict):
            for inner in ("text", "delta", "content"):
                iv = val.get(inner)
                if isinstance(iv, str) and iv.strip():
                    return iv
    update = obj.get("update")
    if not isinstance(update, dict):
        params = obj.get("params")
        if isinstance(params, dict):
            update = params.get("update")
    if isinstance(update, dict):
        content = update.get("content")
        if isinstance(content, dict):
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                return text
        if isinstance(content, str) and content.strip():
            return content
    return None


def _event_looks_like_tool(obj: dict[str, Any]) -> str | None:
    """Return a tool name when the event is a successful file/shell action."""
    kind = str(obj.get("type") or obj.get("event") or "").lower()
    update = obj.get("update")
    session_update = ""
    if isinstance(update, dict):
        session_update = str(update.get("sessionUpdate") or update.get("type") or "").lower()
        kind = kind or session_update
    if any(token in kind for token in ("file", "write", "edit", "patch")):
        return "Write"
    if any(token in kind for token in ("command", "shell", "bash", "tool")):
        return "Bash"
    return None


class GrokBuildDirectWorker:
    """Heavy worker that calls ``grok -p`` over the SuperGrok subscription.

    ``cli`` is declared ``"codex"`` so the Phase-6 telemetry schema needs no
    migration (same reason as GoogleCliWorker). The synthetic init event's
    ``model`` field carries ``grok-build/<model>`` so debugging stays unambiguous.

    Broker grant: issued and closed for a truthful capability report. Grok
    Build's isolated worker home deliberately does not ingest user hooks or
    plugins; the official CLI has no documented equivalent of Codex's
    ``-c mcp_servers.*`` injection, so marketplace MCP is an honest
    degradation (reported unavailable unless a future grok MCP write lands).
    """

    cli: ClassVar[Literal["claude", "codex", "python", "browser"]] = "codex"

    def __init__(
        self,
        *,
        capability_inventory: WorkerCapabilityInventory | None = None,
    ) -> None:
        self.last_pid: int | None = None
        self.last_session_id: str | None = None
        self.capability_inventory = capability_inventory or WorkerCapabilityInventory.build()

    async def spawn(
        self,
        prompt: str,
        *,
        worktree: Path,
        env: dict[str, str],
        job: Any,
        worker_id: str,
        log_dir: Path,
        model: str = "",
        allowed_tools: str = "",
        permission_mode: str = "",
        max_turns: int = 20,
        resume_session_id: str | None = None,
        extra_args: tuple[str, ...] = (),
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        first_output_timeout_s: float = _DEFAULT_FIRST_OUTPUT_TIMEOUT_S,
        mission_id: str = "",
        allow_backend_fallback: bool = True,
        _broker_binding: Any | None = None,
        **_unused: Any,
    ) -> AsyncIterator[Any]:
        del allowed_tools, permission_mode, max_turns, allow_backend_fallback
        broker_binding = _broker_binding
        issued_here = broker_binding is None
        if issued_here:
            broker_binding = self.capability_inventory.bind_broker(
                ttl_s=timeout_s + _HARDCAP_GRACE_S + 60.0,
                mission_id=mission_id or None,
                worker_id=worker_id,
            )
        try:
            async for event in self._spawn_bound(
                prompt,
                worktree=worktree,
                env=env,
                job=job,
                worker_id=worker_id,
                log_dir=log_dir,
                model=model,
                resume_session_id=resume_session_id,
                extra_args=extra_args,
                timeout_s=timeout_s,
                first_output_timeout_s=first_output_timeout_s,
                broker_binding=broker_binding,
            ):
                yield event
        finally:
            if issued_here and broker_binding is not None:
                try:
                    broker_binding.close()
                except Exception:  # noqa: BLE001 - cleanup must not mask cancellation
                    logger.exception(
                        "GrokBuildDirectWorker: broker binding cleanup failed"
                    )

    async def _spawn_bound(
        self,
        prompt: str,
        *,
        worktree: Path,
        env: dict[str, str],
        job: Any,
        worker_id: str,
        log_dir: Path,
        model: str = "",
        resume_session_id: str | None = None,
        extra_args: tuple[str, ...] = (),
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        first_output_timeout_s: float = _DEFAULT_FIRST_OUTPUT_TIMEOUT_S,
        broker_binding: Any | None = None,
    ) -> AsyncIterator[Any]:
        log_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        stdout_log = log_dir / "stream.jsonl"
        stderr_log = log_dir / "stderr.log"
        with suppress(OSError):
            stdout_log.write_bytes(b"")

        session_id = resume_session_id or str(uuid.uuid4())
        self.last_session_id = session_id
        effective_model = _normalize_model_for_grok_build(model) or _DEFAULT_GROK_BUILD_MODEL

        binary = _resolve_grok_binary()
        yield ClaudeSystemInit(
            session_id=session_id,
            model=f"grok-build/{effective_model}",
            tools=[],
            cwd=str(worktree),
            external_capabilities=self.capability_inventory.report_for(
                "grok-build", binding=broker_binding
            ),
        )
        if binary is None:
            yield ClaudeResult(
                subtype="error_during_execution",
                is_error=True,
                session_id=session_id,
                duration_ms=0,
                result="GrokBuildDirectWorker: grok binary not found",
            )
            return

        cmd = _build_grok_build_cmd(
            binary=binary,
            prompt=prompt,
            worktree=worktree,
            model=effective_model,
            extra_args=extra_args,
        )
        env_for_grok = _build_grok_build_env(env)
        if broker_binding is not None:
            env_for_grok = broker_binding.apply_environment(env_for_grok)
        logger.info(
            "GrokBuildDirectWorker[%s] spawn: cwd=%s model=%s argv=%s",
            worker_id,
            worktree,
            effective_model,
            [part if part != prompt else "<prompt>" for part in cmd],
        )

        t0 = time.perf_counter()
        try:
            proc = await create_worker_subprocess(
                cmd,
                cwd=str(worktree),
                env=env_for_grok,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=_STREAM_READLINE_LIMIT,
            )
        except FileNotFoundError as exc:
            yield ClaudeResult(
                subtype="error_during_execution",
                is_error=True,
                session_id=session_id,
                duration_ms=int((time.perf_counter() - t0) * 1000),
                result=f"GrokBuildDirectWorker: grok binary not found: {exc}",
            )
            return

        self.last_pid = proc.pid
        try:
            job.assign(proc.pid)
        except Exception:  # noqa: BLE001
            logger.warning(
                "GrokBuildDirectWorker[%s]: job.assign(pid=%d) failed",
                worker_id,
                proc.pid,
                exc_info=True,
            )

        timed_out = False
        timeout_message = ""
        text_acc: list[str] = []
        any_tool_use = False
        terminal_kind = "success"
        terminal_message: str | None = None
        got_first_line = False
        deadline = time.monotonic() + timeout_s + _HARDCAP_GRACE_S
        assert proc.stdout is not None  # noqa: S101 — PIPE always created
        assert proc.stderr is not None  # noqa: S101 — PIPE always created
        stderr_task = asyncio.create_task(proc.stderr.read())

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                timeout_message = (
                    f"GrokBuildDirectWorker: subprocess wall-clock timeout "
                    f"({timeout_s + _HARDCAP_GRACE_S:.0f}s) exceeded while streaming"
                )
                break
            read_cap = remaining if got_first_line else min(first_output_timeout_s, remaining)
            try:
                raw = await asyncio.wait_for(proc.stdout.readline(), timeout=read_cap)
            except TimeoutError:
                if got_first_line:
                    continue
                timed_out = True
                timeout_message = (
                    f"GrokBuildDirectWorker: subprocess produced no output within "
                    f"{first_output_timeout_s:.0f}s startup timeout; killed for retry"
                )
                break
            except ValueError:
                terminal_kind = "error"
                terminal_message = (
                    "GrokBuildDirectWorker: a stdout line exceeded the "
                    f"{_STREAM_READLINE_LIMIT // (1024 * 1024)} MiB stream limit"
                )
                logger.error("%s", terminal_message)
                break
            if not raw:
                break
            got_first_line = True
            try:
                with stdout_log.open("ab") as stream_fh:
                    stream_fh.write(raw)
            except OSError as exc:
                logger.warning("GrokBuildDirectWorker: stream.jsonl append failed: %s", exc)
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            event_type = str(obj.get("type") or obj.get("event") or "").lower()
            if event_type in {"error", "turn.failed", "failed"}:
                terminal_kind = "error"
                terminal_message = _coerce_error_text(obj)
                continue
            if event_type in {"result", "turn.completed", "completed", "done"}:
                text = _text_from_event(obj)
                if text:
                    text_acc.append(text)
                continue
            tool_name = _event_looks_like_tool(obj)
            if tool_name:
                any_tool_use = True
                yield ClaudeAssistantMessage(
                    message={
                        "role": "assistant",
                        "content": [{"type": "tool_use", "name": tool_name, "input": {}}],
                    },
                    session_id=session_id,
                )
                continue
            text = _text_from_event(obj)
            if text:
                text_acc.append(text)
                yield ClaudeAssistantMessage(
                    message={
                        "role": "assistant",
                        "content": [{"type": "text", "text": text}],
                    },
                    session_id=session_id,
                )

        stderr_bytes = b""
        with suppress(Exception):
            stderr_bytes = await asyncio.wait_for(stderr_task, timeout=2.0)
        if timed_out:
            with suppress(ProcessLookupError, OSError):
                proc.kill()
            with suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=5.0)
        else:
            with suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=10.0)

        if stderr_bytes:
            with suppress(OSError):
                stderr_log.write_bytes(stderr_bytes)
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        combined = " ".join(part for part in (terminal_message, stderr_text) if part)
        if _is_auth_failure(combined):
            terminal_kind = "error"
            terminal_message = (
                terminal_message
                or "Grok Build is not logged in — run grok login on the Agents card."
            )
        elif _is_usage_limited(combined):
            terminal_kind = "error"
            terminal_message = terminal_message or combined.strip() or "Grok Build usage limit"

        duration_ms = int((time.perf_counter() - t0) * 1000)
        result_text = "\n".join(text_acc).strip()
        if timed_out:
            yield ClaudeResult(
                subtype="error_during_execution",
                is_error=True,
                session_id=session_id,
                duration_ms=duration_ms,
                result=timeout_message,
            )
            return
        exit_code = proc.returncode
        is_error = terminal_kind == "error" or (exit_code not in (None, 0) and not result_text)
        if is_error and not result_text:
            result_text = terminal_message or stderr_text.strip() or (
                f"GrokBuildDirectWorker: grok exited {exit_code}"
            )
        if not result_text and any_tool_use:
            result_text = "Grok Build completed tool work in the worktree."
        yield ClaudeResult(
            subtype="success" if not is_error else "error_during_execution",
            is_error=is_error,
            session_id=session_id,
            duration_ms=duration_ms,
            result=result_text,
        )
