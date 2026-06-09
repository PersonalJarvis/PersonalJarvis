"""ClaudeDirectWorker — drives the `claude` CLI directly, bypassing OpenClaw.

Live forensics on 2026-05-16 (Verifier-Agent 5/5) proved that OpenClaw
2026.5.7 silently ignores ``agents.defaults.cliBackends["claude-cli"].args``
when running ``openclaw agent --local --json --model claude-cli/<model>``.
The injected ``--permission-mode bypassPermissions`` never reaches the
claude binary, so the worker boots in chat-only mode and produces
plausible text ("Habe Datei erstellt") without invoking any write tool.

Empirical proof at /tmp/probe5 and /tmp/probe6 on 2026-05-16:

    echo "<prompt>" | claude --print --permission-mode bypassPermissions \
        --add-dir <cwd>

    -> actual Write tool invocation, actual file on disk

vs the identical request through ``openclaw agent``:

    -> single text block, no tool_use, no file

This worker spawns ``claude`` directly with the right argv and the prompt
on stdin, preserving the WorkerProtocol contract so the Kontrollierer
sees the same ClaudeSystemInit / ClaudeResult events as it does for the
OpenClaw-backed SubJarvisWorker. The provider chain is read from
``[brain.sub_jarvis]`` for parity with SubJarvisWorker — we only act
when the resolved primary provider is ``claude-api``; other providers
fall through to SubJarvisWorker via ``worker_factory`` routing in
``jarvis.missions.init``.

Spawn discipline mirrors GeminiWorker / SubJarvisWorker:

- ``asyncio.create_subprocess_exec`` (no shell, no PTY).
- Win32 ``CREATE_BREAKAWAY_FROM_JOB`` creationflags so the per-mission
  Windows Job Object can take ownership.
- Strict ``env=...`` from ``build_worker_env`` (allowlist only).
- ``ANTHROPIC_OAUTH_TOKEN`` + ``ANTHROPIC_API_KEY`` injected by
  ``_env_builder`` in ``jarvis.missions.init``.

Authentication: claude CLI reads OAuth tokens from ``~/.claude/.credentials.json``
in the user's profile. The env-builder copies the bearer to both
``ANTHROPIC_API_KEY`` and ``ANTHROPIC_OAUTH_TOKEN`` so the binary works
regardless of which env var the CLI happens to check first.
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
    ClaudeStreamDelta,
    ClaudeSystemInit,
    ClaudeUserMessage,
)
from .provider_chain import _resolve_provider_chain

logger = logging.getLogger(__name__)


_DEFAULT_TIMEOUT_S: float = 600.0
# First-output ("startup") timeout. ClaudeDirectWorker kills + flags a retry
# when ``claude`` emits ZERO bytes within this window — the signature of a
# Claude Max OAuth-contention hang: when several claude-direct workers/critics
# are spawned at once the subscription throttles and the CLI blocks BEFORE
# emitting a single byte, then the old single-``communicate()`` cap burned the
# full 630s (live 2026-05-28: 0-byte stream.jsonl, mission FAILED with no work;
# 82% of task_error rows overlapped another mission within 90s). Distinct from
# the hard cap above so a task that HAS started streaming is never cut off
# mid-work. The result text carries "timeout" so the orchestrator labels the
# WorkerKilled reason "timeout" and retries on a fresh (serialised) spawn.
_DEFAULT_FIRST_OUTPUT_TIMEOUT_S: float = 120.0
# Bytes pulled on the first read; the opening stream-json system-init line is
# far under this, so the read returns as soon as claude emits anything.
_FIRST_READ_BYTES: int = 65536
# The per-worker claude-cli MCP config. It inlines RESOLVED plugin secrets
# (e.g. a GitHub PAT in the github MCP server's env block), so it is written to
# the mission log dir (kept out of the git diff) AND deleted as soon as the
# subprocess exits — claude only reads it at startup, so it never needs to
# persist as a plaintext secret-at-rest (security 2026-05-28: a real ghp_ token
# was found lingering across 6 mission log dirs).
_MCP_CONFIG_FILENAME: str = ".jarvis-mcp.json"

# The claude-cli ``--model`` used when ClaudeDirectWorker runs as the universal
# heavy-worker fallback for a non-claude sub_jarvis provider (see spawn()). A
# real claude model — NEVER a foreign slug like ``grok-4.3``, which the claude
# CLI rejects. Overridden by ``[brain.providers.claude-api].deep_model`` when
# the config is readable.
_DEFAULT_CLAUDE_MODEL: str = "claude-opus-4-8"


def _resolve_claude_model(primary: Any) -> str:
    """Resolve the claude-cli ``--model`` this worker actually runs.

    * If the resolved chain primary IS ``claude-api`` with a model, honour it
      verbatim (unchanged behaviour for a claude-api sub_jarvis config).
    * Otherwise — the universal-fallback case where ``_worker_factory`` routed a
      ``grok`` / ``gemini`` / ``openai`` / ``openrouter`` / unset provider here
      because none of them has a direct worker anymore — resolve the
      ``[brain.providers.claude-api].deep_model`` from config, falling back to a
      sane Opus default. We must NEVER pass the foreign chain model (e.g.
      ``grok-4.3``) to ``claude --model``: that is the 2026-06-08 instant-fail
      bug (``primary provider is grok, expected claude-api``).
    """
    provider = getattr(primary, "provider", None)
    model = getattr(primary, "model", None)
    if provider == "claude-api" and model:
        return str(model)
    # Config read is best-effort — a missing/unreadable config falls through to
    # the sane Opus default below rather than crashing the worker spawn.
    with suppress(Exception):
        from jarvis.core.config import load_config

        cfg = load_config()
        providers = getattr(cfg.brain, "providers", {}) or {}
        pcfg = providers.get("claude-api")
        if pcfg is not None:
            resolved = (
                getattr(pcfg, "deep_model", None)
                or getattr(pcfg, "model", None)
            )
            if resolved:
                return str(resolved)
    return _DEFAULT_CLAUDE_MODEL


def _resolve_claude_binary() -> str | None:
    """Returns the absolute path to the ``claude`` CLI shim.

    On Windows, npm-installed CLIs ship as ``.cmd``. We do NOT call
    ``claude.cmd`` directly — same metachar-trap as openclaw.cmd — but
    instead invoke ``node ...claude.mjs``. Resolution mirrors the
    pattern from ``_resolve_worker_argv_prefix`` in provider_chain.

    Returns the *single string* a caller can append the argv after.
    """
    # Walk through likely names; the user has claude-code installed.
    for name in ("claude.cmd", "claude.exe", "claude"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _resolve_claude_argv_prefix() -> list[str]:
    """Returns the argv prefix used to launch claude.

    Strategy mirrors ``_resolve_worker_argv_prefix`` — prefer
    ``node <path>/claude.mjs`` over the ``.cmd`` batch shim so prompts
    with newlines / apostrophes / ampersands survive verbatim.
    Falls back to the bare shim only when node + mjs aren't both
    resolvable.
    """
    node = shutil.which("node") or shutil.which("node.exe")
    if node:
        for name in ("claude.cmd", "claude.exe", "claude"):
            cli = shutil.which(name)
            if not cli:
                continue
            cli_dir = Path(cli).resolve().parent
            for candidate in (
                cli_dir / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js",
                cli_dir / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.mjs",
                cli_dir / "cli.js",
                cli_dir / "claude.mjs",
            ):
                if candidate.is_file():
                    return [node, str(candidate)]
    bare = _resolve_claude_binary()
    return [bare] if bare else ["claude"]


def _build_mcp_config_args(
    config_dir: Path, mcp_servers: dict[str, Any] | None
) -> list[str]:
    """Write the assembled MCP servers to ``config_dir`` and return claude-cli
    flags so the worker can call connected marketplace plugins / mcp.json
    servers.

    The config holds RESOLVED plugin tokens, so it is written to ``config_dir``
    (the mission log dir) — NOT the git worktree. Writing it into the worktree
    would leak the token into ``_capture_diff`` (``git add -N`` + diff), the
    safety scan, and the archived ``diff.patch`` (AP-2 / AP-12).

    Returns ``[]`` when there are no servers, so a plain mission is byte-for-byte
    unchanged. ``--strict-mcp-config`` ensures only this config is honoured (a
    stray project ``.mcp.json`` cannot inject extra servers into the worker).
    """
    if not mcp_servers:
        return []
    # NOTE: this file inlines resolved plugin secrets — spawn() deletes it the
    # moment the subprocess exits (see _MCP_CONFIG_FILENAME). Do not move it
    # into the worktree (AP-2 / AP-12) and do not keep it after the run.
    cfg_path = Path(config_dir) / _MCP_CONFIG_FILENAME
    cfg_path.write_text(
        json.dumps({"mcpServers": mcp_servers}, ensure_ascii=False),
        encoding="utf-8",
    )
    return ["--mcp-config", str(cfg_path), "--strict-mcp-config"]


class ClaudeDirectWorker:
    """Heavy worker that calls ``claude`` directly without OpenClaw.

    Conforms to ``WorkerProtocol`` structurally (no inheritance — see
    ``jarvis/missions/workers/base.py``). Yields the same Pydantic
    Claude-stream events as ``SubJarvisWorker`` so the existing
    Kontrollierer / Critic loop works unchanged.
    """

    cli: ClassVar[Literal["claude", "codex", "python", "browser"]] = "claude"

    def __init__(self, mcp_servers: dict[str, Any] | None = None) -> None:
        self.last_pid: int | None = None
        self.last_session_id: str | None = None
        # Assembled claude-cli ``mcpServers`` map (connected marketplace plugins
        # + user mcp.json). Empty -> no MCP wiring. See jarvis.marketplace.mcp_bridge.
        self._mcp_servers: dict[str, Any] = dict(mcp_servers or {})

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
        permission_mode: str = "bypassPermissions",
        max_turns: int = 20,
        resume_session_id: str | None = None,
        extra_args: tuple[str, ...] = (),
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        first_output_timeout_s: float = _DEFAULT_FIRST_OUTPUT_TIMEOUT_S,
        mission_id: str = "",
        **_unused: Any,
    ) -> AsyncIterator[Any]:
        """Spawn ``claude --print --output-format=stream-json`` with the prompt on stdin.

        Yields a synthetic ``ClaudeSystemInit`` first (so WorkerSpawned
        fires upstream with the session-id), then forwards every
        Anthropic stream-json record verbatim, ending with the final
        ``ClaudeResult``.
        """
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_log = log_dir / "stream.jsonl"
        stderr_log = log_dir / "stderr.log"

        # 1. Resolve which claude model this worker runs.
        #
        # Post-Welle-4, ClaudeDirectWorker is the UNIVERSAL heavy-worker
        # fallback: the OpenClaw-backed SubJarvisWorker was removed (it caused
        # the ~92% nested-claude hang), so jarvis.missions.init._worker_factory
        # routes EVERY [brain.sub_jarvis].provider that is not
        # openai-codex/chatgpt (grok, gemini, openai, openrouter,
        # openclaw-claude, AND the unset default — whose chain resolver falls
        # back to the ("grok", "grok-4.3") stub) to THIS worker, which always
        # executes on the Claude Max OAuth claude-cli backend.
        #
        # The old "refuse unless the resolved provider is claude-api" guard
        # assumed a SubJarvisWorker fall-through that NO LONGER EXISTS — so it
        # turned every non-claude sub_jarvis setting into an INSTANT mission
        # failure (forensic 2026-06-08: missions 019ea82e* / 019ea830* died in
        # ~3 s with "primary provider is grok, expected claude-api"). We now
        # always run on Claude and only LOG when the configured provider
        # differs, so the fallback is legible, never a silent swap
        # (anti-silent-fallback mandate).
        chain = _resolve_provider_chain()
        primary = chain[0] if chain else None
        claude_model = _resolve_claude_model(primary)
        if primary is not None and primary.provider != "claude-api":
            logger.warning(
                "ClaudeDirectWorker[%s]: configured sub_jarvis provider is %r, "
                "which has no direct worker (OpenClaw path removed in Welle 4) — "
                "running heavy work on the Claude Max OAuth backend (model=%s) "
                "instead of failing the mission.",
                worker_id, primary.provider, claude_model,
            )

        session_id = resume_session_id or str(uuid.uuid4())
        self.last_session_id = session_id

        # 2. Build the claude argv.
        argv_prefix = _resolve_claude_argv_prefix()
        cmd: list[str] = [
            *argv_prefix,
            "--print",
            "--output-format", "stream-json",
            "--verbose",  # required by --print + stream-json
            "--permission-mode", permission_mode,
            "--add-dir", str(worktree),
            "--model", claude_model,
        ]
        if allowed_tools:
            cmd.extend(["--allowedTools", allowed_tools])
        # Connected marketplace plugins / mcp.json -> claude-cli MCP config so
        # the delegated worker can actually call the plugins (AD-OE4). Written to
        # log_dir (NOT the worktree) so the resolved token never lands in the diff.
        cmd.extend(_build_mcp_config_args(log_dir, self._mcp_servers))
        cmd.extend(extra_args)

        yield ClaudeSystemInit(
            session_id=session_id,
            model=f"claude-cli/{claude_model}",
            tools=[],
            cwd=str(worktree),
        )

        logger.info(
            "ClaudeDirectWorker[%s] spawn: cwd=%s model=%s",
            worker_id, worktree, claude_model,
        )

        # 3. Spawn the subprocess with the prompt on stdin.
        t0 = time.perf_counter()
        creationflags = _win32_creationflags() if sys.platform == "win32" else 0
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(worktree),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creationflags,
            )
        except FileNotFoundError as exc:
            # The MCP config (with its inlined secret) was already written when
            # we built the argv — scrub it even though the subprocess never ran.
            with suppress(OSError):
                (log_dir / _MCP_CONFIG_FILENAME).unlink(missing_ok=True)
            yield ClaudeResult(
                subtype="error_during_execution",
                is_error=True,
                session_id=session_id,
                duration_ms=int((time.perf_counter() - t0) * 1000),
                result=f"ClaudeDirectWorker: claude binary not found: {exc}",
            )
            return

        self.last_pid = proc.pid
        try:
            job.assign(proc.pid)
        except Exception:  # noqa: BLE001
            logger.warning(
                "ClaudeDirectWorker[%s]: job.assign(pid=%d) failed",
                worker_id, proc.pid, exc_info=True,
            )

        # Write the prompt then close stdin to signal EOF.
        try:
            assert proc.stdin is not None  # noqa: S101 — Pipe always created above
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError) as exc:
            logger.warning(
                "ClaudeDirectWorker[%s]: prompt stdin write failed: %s",
                worker_id, exc,
            )

        # 4. Drain stdout/stderr. A first-output ("startup") gate catches the
        # Claude Max OAuth-contention hang: under concurrent claude-direct
        # load the CLI blocks before emitting a single byte and the old single
        # communicate() cap burned the full 630s (0-byte stream.jsonl ->
        # task_error). We kill fast on a silent startup so the orchestrator can
        # retry on a fresh, serialised spawn; once streaming has begun we allow
        # the full hard cap so a legitimately long task is never cut off.
        timed_out = False
        timeout_message = ""
        stderr_bytes = b""
        first_chunk = b""
        assert proc.stdout is not None  # noqa: S101 — PIPE always created above
        try:
            first_chunk = await asyncio.wait_for(
                proc.stdout.read(_FIRST_READ_BYTES),
                timeout=first_output_timeout_s,
            )
        except asyncio.TimeoutError:
            timed_out = True
            timeout_message = (
                f"ClaudeDirectWorker: subprocess produced no output within "
                f"{first_output_timeout_s:.0f}s startup timeout (claude emitted "
                f"zero bytes — likely Claude Max OAuth contention); killed for retry"
            )

        if not timed_out:
            # First bytes arrived — drain the remainder under the hard cap.
            try:
                rest_stdout, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_s + 30.0
                )
            except asyncio.TimeoutError:
                timed_out = True
                rest_stdout = b""
                timeout_message = (
                    f"ClaudeDirectWorker: subprocess wait_for timeout "
                    f"({timeout_s + 30.0}s) after streaming started"
                )
            stdout_bytes = first_chunk + rest_stdout

        if timed_out:
            with suppress(ProcessLookupError):
                proc.kill()
            # Audit-2 H3 (2026-05-17): kill() without wait() leaves the
            # asyncio transport attached + an open Win32 handle. Wait
            # briefly so the process is reaped before we drop our
            # reference.
            with suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            stdout_bytes = first_chunk
            stderr_bytes = timeout_message.encode("utf-8")

        try:
            stdout_log.write_bytes(stdout_bytes)
        except OSError as exc:
            logger.warning("ClaudeDirectWorker: stream.jsonl write failed: %s", exc)
        if stderr_bytes:
            try:
                stderr_log.write_bytes(stderr_bytes)
            except OSError as exc:
                logger.warning("ClaudeDirectWorker: stderr.log write failed: %s", exc)

        # Scrub the MCP config now that the subprocess has exited — it inlined a
        # plaintext plugin secret (e.g. a GitHub PAT) and is not needed post-run
        # (claude read it at startup). This keeps the token from persisting in
        # the mission logs as a secret-at-rest (security 2026-05-28).
        with suppress(OSError):
            (log_dir / _MCP_CONFIG_FILENAME).unlink(missing_ok=True)

        # 5. Parse the NDJSON stdout for the terminal result.
        final_result: ClaudeResult | None = None
        text_acc: list[str] = []
        any_tool_use = False
        for raw_line in stdout_bytes.decode("utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            obj_type = obj.get("type")
            if obj_type == "result":
                final_result = ClaudeResult(
                    subtype=obj.get("subtype") or "success",
                    is_error=bool(obj.get("is_error", False)),
                    cost_usd=obj.get("total_cost_usd") or obj.get("cost_usd"),
                    num_turns=obj.get("num_turns"),
                    session_id=obj.get("session_id") or session_id,
                    duration_ms=obj.get("duration_ms")
                    or int((time.perf_counter() - t0) * 1000),
                    result=obj.get("result")
                    or obj.get("text")
                    or "\n".join(text_acc),
                )
                continue
            if obj_type == "assistant":
                msg = obj.get("message", {})
                # Inspect content blocks for tool_use detection.
                for blk in msg.get("content", []) or []:
                    if isinstance(blk, dict):
                        if blk.get("type") == "tool_use":
                            any_tool_use = True
                        elif blk.get("type") == "text":
                            text_acc.append(blk.get("text", ""))
                yield ClaudeAssistantMessage(message=msg, session_id=session_id)
                continue
            if obj_type == "user":
                yield ClaudeUserMessage(
                    message=obj.get("message", {}), session_id=session_id
                )
                continue
            if obj_type == "stream_event":
                yield ClaudeStreamDelta(
                    event=obj.get("event", {}), session_id=session_id
                )
                continue
            # ignore "system" events — we already emitted our synthetic init.

        if final_result is None:
            if timed_out:
                # Surface the timeout verbatim so the orchestrator's worker_error
                # handling labels the kill reason "timeout" and retries (vs the
                # generic "claude exited with code -1" which read as a hard error).
                final_result = ClaudeResult(
                    subtype="error_during_execution",
                    is_error=True,
                    cost_usd=None,
                    num_turns=None,
                    session_id=session_id,
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                    result=timeout_message,
                )
            else:
                exit_code = proc.returncode if proc.returncode is not None else -1
                final_result = ClaudeResult(
                    subtype="success" if exit_code == 0 else "error_during_execution",
                    is_error=(exit_code != 0),
                    cost_usd=None,
                    num_turns=None,
                    session_id=session_id,
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                    result="\n".join(text_acc)
                    if text_acc
                    else f"claude exited with code {exit_code}",
                )

        logger.info(
            "ClaudeDirectWorker[%s] done: exit=%s wall_ms=%s tool_use_seen=%s "
            "session=%s",
            worker_id,
            proc.returncode,
            int((time.perf_counter() - t0) * 1000),
            any_tool_use,
            session_id,
        )

        yield final_result


__all__ = [
    "ClaudeDirectWorker",
    "_resolve_claude_argv_prefix",
    "_resolve_claude_binary",
]
