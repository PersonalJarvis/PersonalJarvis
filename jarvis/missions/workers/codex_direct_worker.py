"""CodexDirectWorker -- drives `codex exec` directly via ChatGPT-OAuth.

Welle 6 (2026-05-18): user switched from Claude Max subscription to
ChatGPT subscription as the canonical worker backend. The ``codex`` CLI
(OpenAI's official agent CLI) supports the same OAuth-bearer flow as
``claude``: ``codex login`` stores the access/refresh tokens in
``~/.codex/auth.json`` and every subsequent ``codex exec`` invocation
picks them up automatically -- no API key required.

This worker mirrors :class:`ClaudeDirectWorker` but adapts to Codex's
event format (``thread.started`` / ``turn.started`` / ``item.completed``
/ ``turn.completed`` JSONL frames instead of Anthropic's
``system`` / ``assistant`` / ``result`` shape). Translates the Codex
events into the WorkerProtocol's Claude-shaped events so the
Kontrollierer/Critic loop does not have to know which CLI produced the
trace.

Spawn discipline mirrors :class:`ClaudeDirectWorker`:

* ``asyncio.create_subprocess_exec`` (no shell, no PTY).
* Win32 ``CREATE_BREAKAWAY_FROM_JOB`` creationflags so the per-mission
  Windows Job Object can take ownership of the process tree.
* Strict ``env=...`` allowlist; explicitly omits ``OPENAI_API_KEY`` so
  the binary falls back to the ``~/.codex/auth.json`` OAuth path.
* Prompt passed on stdin -- avoids Windows command-line length cap.

Authentication: when the env does not carry ``OPENAI_API_KEY`` the
codex binary reads OAuth bearer + refresh tokens from
``~/.codex/auth.json`` in the user's profile. ``codex login`` (run
once interactively) is the canonical way to populate that file. We
NEVER write API keys here -- the user explicitly chose the ChatGPT
subscription path.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sys
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any, AsyncIterator, ClassVar, Literal

from .process_utils import worker_creationflags as _win32_creationflags
from .stream_consumer import (
    ClaudeAssistantMessage,
    ClaudeResult,
    ClaudeSystemInit,
)

logger = logging.getLogger(__name__)


_DEFAULT_TIMEOUT_S: float = 600.0


def _resolve_codex_binary() -> str | None:
    """Return the on-PATH ``codex`` binary, considering Windows extensions."""
    for name in ("codex", "codex.cmd", "codex.exe"):
        path = shutil.which(name)
        if path:
            return path
    return None


# Anthropic-flavoured model aliases the MissionDecomposer emits as a
# legacy default (`kontrollierer/decomposer.py:57` -> `model: str = "sonnet"`).
# Codex with a ChatGPT account rejects these with HTTP 400 -- "The
# 'sonnet' model is not supported when using Codex with a ChatGPT
# account." Live repro 2026-05-18 mission_019e3c52-0acd. The
# normalisation converts them to empty string so codex falls back to
# the user's CLI default (configured under ~/.codex/config.toml or
# whatever the ChatGPT subscription exposes today).
_CLAUDE_MODEL_ALIASES: frozenset[str] = frozenset({
    "sonnet", "opus", "haiku",
})


def _normalize_model_for_codex(model: str | None) -> str:
    """Drop Anthropic-flavoured model slugs that codex would reject.

    Returns the empty string when ``model`` is None, falsy, in the
    decomposer's three legacy aliases, or starts with ``claude``.
    The caller must omit ``--model`` from the codex argv when this
    helper returns empty -- ``_build_codex_direct_cmd`` already does so
    via ``if model:`` gating.
    """
    if not model:
        return ""
    stripped = model.strip()
    if not stripped:  # whitespace-only input
        return ""
    lowered = stripped.lower()
    if lowered in _CLAUDE_MODEL_ALIASES:
        return ""
    if lowered.startswith("claude") or lowered.startswith("anthropic"):
        return ""
    return model


def _build_codex_direct_cmd(
    *,
    worktree: Path,
    model: str | None,
    sandbox: str = "workspace-write",
    approval_policy: str = "never",
    extra_args: tuple[str, ...] = (),
) -> list[str]:
    """Compose the codex argv. Prompt arrives on stdin, not here.

    ``--skip-git-repo-check`` is mandatory because per-task worktrees are
    technically inside the parent repo; without it codex refuses to run.
    ``--sandbox workspace-write`` + ``-c approval_policy=never`` gives a
    non-interactive write-capable agent without the
    ``--dangerously-bypass-approvals-and-sandbox`` blast radius.
    ``--add-dir`` exposes the worktree to codex's filesystem layer.
    """
    cmd: list[str] = [
        _resolve_codex_binary() or "codex",
        "exec",
        "--json",
        "--skip-git-repo-check",
        # Welle 6 (2026-05-18 live debug): worker keeps the user's
        # ~/.codex/config.toml so the default sandbox + tool registry
        # apply. The Cloudflare MCP plugin errors loudly in stderr
        # when its OAuth token is expired, but the worker still
        # completes the turn successfully (file_write + turn.completed).
        # The Critic path strips user config because the critic's
        # long prompt + plugin-bootstrap race actually does swallow
        # the agent_message frame -- worker doesn't.
        "--sandbox", sandbox,
        "-c", f"approval_policy={approval_policy}",
        "--add-dir", str(worktree),
    ]
    if model:
        cmd.extend(["--model", model])
    cmd.extend(extra_args)
    return cmd


class CodexDirectWorker:
    """Heavy worker that calls ``codex exec`` directly via ChatGPT-OAuth.

    Conforms to ``WorkerProtocol`` structurally (no inheritance -- see
    ``jarvis/missions/workers/base.py``). Yields ``ClaudeSystemInit``,
    ``ClaudeAssistantMessage``, and ``ClaudeResult`` so the existing
    Kontrollierer / Critic loop works unchanged. Codex's native event
    types (``thread.started``, ``item.completed``, ``turn.completed``)
    are translated in-line so the rest of the codebase stays
    CLI-agnostic.
    """

    cli: ClassVar[Literal["claude", "codex", "python", "browser"]] = "codex"

    def __init__(self) -> None:
        self.last_pid: int | None = None
        self.last_session_id: str | None = None
        self.last_thread_id: str | None = None

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
        allowed_tools: str = "",        # unused -- codex has its own toolset
        permission_mode: str = "",      # unused
        max_turns: int = 20,             # unused -- codex turn cap is internal
        resume_session_id: str | None = None,
        extra_args: tuple[str, ...] = (),
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        mission_id: str = "",
        **_unused: Any,
    ) -> AsyncIterator[Any]:
        """Spawn ``codex exec --json`` with the prompt on stdin.

        Yields a synthetic ``ClaudeSystemInit`` first (so WorkerSpawned
        fires upstream with a session-id), then forwards translated
        codex events, ending with a synthetic ``ClaudeResult``.
        """
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_log = log_dir / "stream.jsonl"
        stderr_log = log_dir / "stderr.log"

        session_id = resume_session_id or str(uuid.uuid4())
        self.last_session_id = session_id

        # Normalize Anthropic-flavoured aliases ("sonnet", "claude-...",
        # ...) to empty so codex uses its ChatGPT-subscription default.
        # See _normalize_model_for_codex docstring + the 2026-05-18 audit
        # at docs/openclaw-spawn-failure-analysis-2026-05-18.md.
        effective_model = _normalize_model_for_codex(model) or None
        cmd = _build_codex_direct_cmd(
            worktree=worktree,
            model=effective_model,
            extra_args=extra_args,
        )

        # CRITICAL: strip OPENAI_API_KEY so codex falls back to OAuth.
        # The user explicitly chose ChatGPT-subscription auth; an
        # accidentally-set API key would steer codex onto the paid
        # Platform API instead.
        #
        # Also strip CODEX_HOME: build_worker_env sets it to a per-mission
        # path so two parallel codex workers do not share state, but
        # OAuth bearer + refresh tokens live in the user's *global*
        # ~/.codex/auth.json. Pointing CODEX_HOME at an empty per-mission
        # dir makes codex error with "Error finding codex home" and exit
        # immediately. Per the ChatGPT-subscription auth path we want
        # codex to use the default ~/.codex, so just drop the override.
        env_for_codex = {
            k: v for k, v in env.items()
            if k not in ("OPENAI_API_KEY", "CODEX_HOME")
        }

        yield ClaudeSystemInit(
            session_id=session_id,
            model=f"codex-cli/{model or 'default'}",
            tools=[],
            cwd=str(worktree),
        )

        logger.info(
            "CodexDirectWorker[%s] spawn: cwd=%s model=%s argv=%s",
            worker_id, worktree, model or "<default>", cmd,
        )

        t0 = time.perf_counter()
        creationflags = _win32_creationflags() if sys.platform == "win32" else 0
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(worktree),
                env=env_for_codex,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creationflags,
            )
        except FileNotFoundError as exc:
            yield ClaudeResult(
                subtype="error_during_execution",
                is_error=True,
                session_id=session_id,
                duration_ms=int((time.perf_counter() - t0) * 1000),
                result=f"CodexDirectWorker: codex binary not found: {exc}",
            )
            return

        self.last_pid = proc.pid
        try:
            job.assign(proc.pid)
        except Exception:  # noqa: BLE001
            logger.warning(
                "CodexDirectWorker[%s]: job.assign(pid=%d) failed",
                worker_id, proc.pid, exc_info=True,
            )

        # Write the prompt to stdin then close to signal EOF.
        try:
            assert proc.stdin is not None  # noqa: S101 -- PIPE always present
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError) as exc:
            logger.warning(
                "CodexDirectWorker[%s]: prompt stdin write failed: %s",
                worker_id, exc,
            )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_s + 30.0
            )
        except asyncio.TimeoutError:
            with suppress(ProcessLookupError):
                proc.kill()
            with suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            stdout_bytes = b""
            stderr_bytes = (
                f"CodexDirectWorker: subprocess wait_for timeout "
                f"({timeout_s + 30.0}s)"
            ).encode("utf-8")

        try:
            stdout_log.write_bytes(stdout_bytes)
        except OSError as exc:
            logger.warning(
                "CodexDirectWorker: stream.jsonl write failed: %s", exc,
            )
        if stderr_bytes:
            try:
                stderr_log.write_bytes(stderr_bytes)
            except OSError as exc:
                logger.warning("CodexDirectWorker: stderr.log write failed: %s", exc)

        # Translate codex events to Claude-shaped events for downstream
        # parity. Codex frames we care about:
        #
        #   thread.started   -> capture thread_id (resume anchor)
        #   item.completed (type=agent_message) -> emit ClaudeAssistantMessage
        #                                          with a synthetic content
        #                                          block carrying the text
        #   item.completed (type=file_change)   -> not surfaced to brain --
        #                                          the diff is captured via
        #                                          git after the worker exits
        #   turn.completed  -> terminal ClaudeResult(success)
        #   turn.failed | error -> terminal ClaudeResult(error)
        #
        # We also detect ``item.completed`` rows of type=command_execution
        # or file_change so the existing log-summarizer / tool-use
        # heuristics in the Critic can see "the worker did do tool calls".
        text_acc: list[str] = []
        any_tool_use = False
        terminal_kind: str = "success"
        terminal_message: str | None = None
        cost_usd: float | None = None
        tokens_used: int | None = None

        for raw_line in stdout_bytes.decode("utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = obj.get("type")
            if t == "thread.started":
                tid = obj.get("thread_id")
                if isinstance(tid, str):
                    self.last_thread_id = tid
                continue
            if t == "item.completed":
                item = obj.get("item", {}) or {}
                item_type = item.get("type")
                if item_type == "agent_message":
                    txt = item.get("text", "")
                    if txt:
                        text_acc.append(txt)
                    yield ClaudeAssistantMessage(
                        message={
                            "role": "assistant",
                            "content": [{"type": "text", "text": txt}],
                        },
                        session_id=session_id,
                    )
                    continue
                if item_type in ("file_change", "command_execution"):
                    any_tool_use = True
                    # Synthesize a tool_use Claude-style event so the
                    # Critic's tool-use detection (which greps the
                    # stream for `"type":"tool_use"`) recognises that
                    # the worker actually invoked tools. Without this
                    # the Critic's read-only-task carve-out misfires
                    # and an empty-diff (which is valid for read-only)
                    # gets a deterministic revise.
                    yield ClaudeAssistantMessage(
                        message={
                            "role": "assistant",
                            "content": [{
                                "type": "tool_use",
                                "name": (
                                    "Write" if item_type == "file_change"
                                    else "Bash"
                                ),
                                "input": (
                                    item.get("changes", [{}])[0]
                                    if item_type == "file_change"
                                    else {"command": item.get("command", "")}
                                ),
                            }],
                        },
                        session_id=session_id,
                    )
                    continue
                continue
            if t == "turn.completed":
                terminal_kind = "success"
                usage = obj.get("usage", {}) or {}
                if isinstance(usage, dict):
                    in_tok = usage.get("input_tokens") or 0
                    out_tok = usage.get("output_tokens") or 0
                    tokens_used = int(in_tok) + int(out_tok)
                continue
            if t in ("turn.failed", "error"):
                terminal_kind = "error"
                terminal_message = obj.get("message") or obj.get("error") or t
                continue

        wall_ms = int((time.perf_counter() - t0) * 1000)
        exit_code = proc.returncode if proc.returncode is not None else -1

        final = ClaudeResult(
            subtype="success" if terminal_kind == "success" and exit_code == 0
            else "error_during_execution",
            is_error=(terminal_kind != "success" or exit_code != 0),
            cost_usd=cost_usd,
            num_turns=None,
            session_id=session_id,
            duration_ms=wall_ms,
            result=(
                terminal_message
                or ("\n".join(text_acc) if text_acc else "")
                or f"codex exited with code {exit_code}"
            ),
        )

        logger.info(
            "CodexDirectWorker[%s] done: exit=%s wall_ms=%s tool_use_seen=%s "
            "thread=%s tokens=%s",
            worker_id, exit_code, wall_ms, any_tool_use,
            self.last_thread_id, tokens_used,
        )

        yield final


__all__ = [
    "CodexDirectWorker",
    "_build_codex_direct_cmd",
    "_resolve_codex_binary",
]
