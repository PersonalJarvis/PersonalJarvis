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
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any, AsyncIterator, ClassVar, Literal

from .process_utils import create_worker_subprocess
from .stream_consumer import (
    ClaudeAssistantMessage,
    ClaudeResult,
    ClaudeStreamDelta,
    ClaudeSystemInit,
    ClaudeUserMessage,
)
from .provider_chain import _resolve_provider_chain

logger = logging.getLogger(__name__)


# Per-attempt wall-clock cap (20 min). Raised from 600 s so complex tasks can
# finish (user mandate 2026-06-09); preserve-partial-work + the first-output
# gate keep a true hang from burning the full cap.
_DEFAULT_TIMEOUT_S: float = 1200.0
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
# StreamReader buffer limit for line-by-line reads (mirrors CodexDirectWorker).
# asyncio's default is 64 KiB; a single claude stream-json line carrying a large
# tool_result / assistant block can exceed that, and readline() raises ValueError
# past the limit. 8 MiB keeps even pathological lines readable without buffering
# the whole run.
_STREAM_READLINE_LIMIT: int = 8 * 1024 * 1024
# Grace added on top of timeout_s for the wall-clock hard cap (parity with the
# pre-streaming communicate(timeout=timeout_s + 30) behaviour). Module constant
# so tests can shrink it instead of sleeping 30 real seconds.
_HARDCAP_GRACE_S: float = 30.0
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
# the config is readable. Maintainer decision 2026-06-14 (supersedes the
# 2026-06-10 fable mandate): ``claude-fable-5`` is approved-access-only and the
# Claude Max subscription cannot reach it via the CLI ("Claude Fable 5 is
# currently unavailable", live mission 019ec615), so the last-resort default is
# ``claude-opus-4-8`` — a model the subscription CAN reach. The
# model-unavailable retry below is the safety net on top of this constant.
_DEFAULT_CLAUDE_MODEL: str = "claude-opus-4-8"


_MODEL_UNAVAILABLE_MARKERS: tuple[str, ...] = (
    "is currently unavailable",
    "issue with the selected model",
    "may not exist",
    "may not have access",
    "model not found",
    "invalid model",
    "unknown model",
)


def _claude_error_is_model_unavailable(text: str) -> bool:
    """True when ``claude --print`` rejected the requested ``--model``.

    Live mission 019ec615 (2026-06-14): the config pins the worker to
    ``claude-fable-5`` (an approved-access model the Claude Max subscription
    cannot reach via the CLI), so every claude worker died with "Claude Fable 5
    is currently unavailable". The CLI default model IS accessible, so a
    model-rejection is recoverable: retry without ``--model``.
    """
    low = (text or "").lower()
    return any(m in low for m in _MODEL_UNAVAILABLE_MARKERS)


# Markers that mean the claude CLI's AUTH surface is dead — an expired/stale
# OAuth bearer or an invalid API key. Distinct from the quota/usage markers
# (the login is not exhausted, it is INVALID) and from model-unavailable.
# Live shapes: "Failed to authenticate. API Error: 401 Invalid authentication
# credentials" (2026-07-06, expired OAuth token injected into the isolated
# worker), "Not logged in · Please run /login" (2026-05-29), "Invalid API key ·
# Fix external API key" (2026-05-18).
_CLAUDE_AUTH_FAILURE_MARKERS: tuple[str, ...] = (
    "failed to authenticate",
    "invalid authentication",
    "authentication_error",
    "invalid api key",
    "invalid x-api-key",
    "not logged in",
    "please run /login",
    "oauth token has expired",
    "401",
    "unauthorized",
)


def _claude_error_is_auth_failure(text: str) -> bool:
    """True when a claude error string signals dead auth (401 / not logged in).

    Dead auth does not reset on a clock (unlike a quota window) — retrying the
    same credential is a guaranteed second 401. The caller marks
    ``claude_auth_dead`` so the worker factory crosses provider families until
    the user runs ``claude /login`` or saves a fresh key (AP-22).
    """
    low = (text or "").lower()
    return any(m in low for m in _CLAUDE_AUTH_FAILURE_MARKERS)


def _resolve_claude_model(primary: Any) -> str:
    """Resolve the claude-cli ``--model`` this worker actually runs.

    * If the resolved chain primary IS ``claude-api`` with a model, honour it
      verbatim (unchanged behaviour for a claude-api sub_jarvis config).
    * Otherwise — the universal-fallback case where ``_worker_factory`` routed a
      ``grok`` / ``gemini`` / ``openai`` / ``openrouter`` / unset provider here
      because none of them has a direct worker anymore — resolve the
      ``[brain.providers.claude-api].deep_model`` from config, falling back to a
      sane fable default. We must NEVER pass the foreign chain model (e.g.
      ``grok-4.3``) to ``claude --model``: that is the 2026-06-08 instant-fail
      bug (``primary provider is grok, expected claude-api``).
    """
    provider = getattr(primary, "provider", None)
    model = getattr(primary, "model", None)
    if provider == "claude-api" and model:
        return str(model)
    # Config read is best-effort — a missing/unreadable config falls through to
    # the sane fable default below rather than crashing the worker spawn.
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
        allow_backend_fallback: bool = True,
        force_default_model: bool = False,
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
        # ClaudeDirectWorker runs the Claude Max OAuth claude-cli backend. The
        # selected [brain.sub_jarvis].provider is HONORED — a provider with its
        # own worker runs on THAT provider, picked in
        # jarvis.missions.init._worker_factory: codex -> CodexDirectWorker,
        # antigravity -> GoogleCliWorker, gemini -> GeminiWorker,
        # openai/openrouter -> ApiAgentWorker. This worker is reached only:
        #   1. when sub_jarvis.provider == "claude-api" (the deliberate choice), or
        #   2. as the UNIVERSAL FALLBACK when the chosen provider cannot run this
        #      mission — its API key is missing / the codex login is dead — or when
        #      an old/unknown provider is configured (openclaw-claude / unset / the
        #      removed OpenClaw "subjarvis" path that caused the ~92% nested-claude
        #      hang). The factory LOGS every such fallback; the choice is never
        #      silently swapped (anti-silent-fallback mandate).
        #
        # So this worker must NOT re-impose a "refuse unless provider==claude-api"
        # guard: the old guard assumed a SubJarvisWorker fall-through that NO LONGER
        # EXISTS and turned every non-claude sub_jarvis setting into an INSTANT
        # mission failure (forensic 2026-06-08: missions 019ea82e* / 019ea830* died
        # in ~3 s with "primary provider is <X>, expected claude-api"). We resolve
        # the claude model from the chain, run on Claude here, and only LOG when the
        # configured provider differs.
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

        # 2. Build the claude argv. ``force_default_model`` omits ``--model``
        # entirely so the CLI picks its own accessible default — the recovery
        # path when the configured model is approved-access-only and the
        # subscription can't reach it (live mission 019ec615, 2026-06-14:
        # claude-fable-5 "is currently unavailable").
        argv_prefix = _resolve_claude_argv_prefix()
        cmd: list[str] = [
            *argv_prefix,
            "--print",
            "--output-format", "stream-json",
            "--verbose",  # required by --print + stream-json
            "--permission-mode", permission_mode,
            "--add-dir", str(worktree),
        ]
        if not force_default_model:
            cmd.extend(["--model", claude_model])
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

        # 3. Spawn the subprocess with the prompt on stdin. The helper sources
        # the Windows creation flags itself and degrades CREATE_BREAKAWAY_FROM_JOB
        # gracefully when the host process is in a job that forbids breakaway
        # (WinError 5, live mission 019ec602 2026-06-14).
        t0 = time.perf_counter()
        try:
            proc = await create_worker_subprocess(
                cmd,
                cwd=str(worktree),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=_STREAM_READLINE_LIMIT,
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

        # 4. Drain stdout LINE-BY-LINE so progress is observable WHILE the
        # worker runs (parity with CodexDirectWorker, 2026-06-10). The old
        # first-chunk read + communicate() collected ALL stdout until process
        # exit: during the whole run there were NO incremental events upstream
        # and NO on-disk stream.jsonl, so a long-but-healthy worker looked
        # identical to a hang — the user pressed the app's Restart button
        # mid-run and the finished work was discarded as app_shutdown (live
        # missions 019ecb35 / 019ec708, forensic 2026-06-15). Now every raw
        # line is tee'd to stream.jsonl immediately and translated events are
        # yielded as they arrive, so the orchestrator can emit live
        # WorkerProgress and the forensics file survives any exit.
        #
        # Timeout semantics are unchanged: zero bytes within
        # first_output_timeout_s -> killed for a fast retry (Claude Max OAuth
        # contention); once streaming has begun only the wall-clock hard cap
        # (timeout_s + grace) applies — there is deliberately NO idle-gap limit
        # between lines, a long silent reasoning phase is legitimate (BUG-032
        # class). timed_out stays the STRUCTURED signal the orchestrator reads.
        timed_out = False
        timeout_message = ""
        stderr_bytes = b""
        final_result: ClaudeResult | None = None
        text_acc: list[str] = []
        any_tool_use = False
        got_first_line = False
        deadline = time.monotonic() + timeout_s + _HARDCAP_GRACE_S
        assert proc.stdout is not None  # noqa: S101 — PIPE always created above
        assert proc.stderr is not None  # noqa: S101 — PIPE always created above
        # Truncate any prior-iteration stream.jsonl (the orchestrator reuses one
        # log_dir across critic iterations) so the file holds only THIS spawn's
        # output — preserving the pre-streaming per-spawn overwrite semantics
        # the Critic's stream-evidence reader relies on.
        with suppress(OSError):
            stdout_log.write_bytes(b"")
        # Drain stderr concurrently: claude writes errors there, and a full
        # stderr pipe would deadlock the child once the OS buffer fills.
        stderr_task = asyncio.create_task(proc.stderr.read())

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                timeout_message = (
                    f"ClaudeDirectWorker: subprocess wall-clock timeout "
                    f"({timeout_s + _HARDCAP_GRACE_S:.0f}s) exceeded while streaming"
                )
                break
            read_cap = (
                remaining
                if got_first_line
                else min(first_output_timeout_s, remaining)
            )
            try:
                raw = await asyncio.wait_for(
                    proc.stdout.readline(), timeout=read_cap
                )
            except TimeoutError:
                if got_first_line:
                    # read_cap == remaining here, so the deadline check at the
                    # top of the loop turns this into the hard-cap timeout.
                    continue
                timed_out = True
                timeout_message = (
                    f"ClaudeDirectWorker: subprocess produced no output within "
                    f"{first_output_timeout_s:.0f}s startup timeout (claude emitted "
                    f"zero bytes — likely Claude Max OAuth contention); killed for retry"
                )
                break
            except ValueError:
                # One NDJSON line exceeded _STREAM_READLINE_LIMIT — the stream
                # buffer is poisoned and cannot be resynced. Fail loudly with
                # the real cause instead of hanging or returning garbage.
                final_result = ClaudeResult(
                    subtype="error_during_execution",
                    is_error=True,
                    session_id=session_id,
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                    result=(
                        "ClaudeDirectWorker: a stdout line exceeded the "
                        f"{_STREAM_READLINE_LIMIT // (1024 * 1024)} MiB stream "
                        "limit; aborting the read loop"
                    ),
                )
                logger.error("%s", final_result.result)
                break
            if not raw:
                break  # EOF — claude closed stdout.
            got_first_line = True
            try:
                # Append-per-line (open/close each time): no long-lived handle
                # to leak if the generator is cancelled mid-run, and the
                # forensics file is complete up to the very last line.
                with stdout_log.open("ab") as stream_fh:
                    stream_fh.write(raw)
            except OSError as exc:
                logger.warning(
                    "ClaudeDirectWorker: stream.jsonl append failed: %s", exc
                )
            line = raw.decode("utf-8", errors="replace").strip()
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

        # Reap the subprocess + collect stderr.
        if timed_out:
            with suppress(ProcessLookupError):
                proc.kill()
        # Audit-2 H3 (2026-05-17): always wait() after the loop so the asyncio
        # transport is torn down and we don't leak a zombie + open Win32 handle.
        with suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        with suppress(Exception):
            stderr_bytes = await asyncio.wait_for(stderr_task, timeout=5.0)
        if timed_out:
            # Surface the timeout verdict in stderr.log alongside whatever claude
            # itself wrote — never lose the real stderr to the synthetic message.
            joiner = b"\n" if stderr_bytes else b""
            stderr_bytes = stderr_bytes + joiner + timeout_message.encode("utf-8")
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

        # 5. Build the terminal result when the stream carried no `result` line.
        if final_result is None:
            if timed_out:
                # Surface the timeout verbatim so the orchestrator's worker_error
                # handling labels the kill reason "timeout" and retries. The
                # structured timed_out flag is the robust signal (the result-text
                # "timeout" match is a belt-and-suspenders fallback).
                final_result = ClaudeResult(
                    subtype="error_during_execution",
                    is_error=True,
                    timed_out=True,
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
        elif timed_out:
            # A `result` line was parsed but the hard cap fired before EOF — mark
            # the structured timeout flag so the orchestrator treats it as a
            # timeout (preserve-partial-work), never a clean success.
            final_result = final_result.model_copy(update={"timed_out": True})

        logger.info(
            "ClaudeDirectWorker[%s] done: exit=%s wall_ms=%s tool_use_seen=%s "
            "session=%s",
            worker_id,
            proc.returncode,
            int((time.perf_counter() - t0) * 1000),
            any_tool_use,
            session_id,
        )

        # A healthy Claude run means the Max window is available again — clear
        # any armed quota cooldown AND any auth-dead flag so the factory
        # resumes routing to Claude.
        if not final_result.is_error and not timed_out:
            from jarvis.claude_auth_state import clear_claude_auth_dead
            from jarvis.claude_quota_state import clear_claude_quota_cooldown

            clear_claude_quota_cooldown()
            clear_claude_auth_dead()

        # Model-unavailable recovery (live mission 019ec615, 2026-06-14): the
        # configured --model (claude-fable-5) is approved-access-only and the
        # Claude Max subscription can't reach it via the CLI. The CLI default
        # IS accessible, so retry once WITHOUT --model rather than failing.
        # Guarded on no work done (delivered work is graded, never re-run) and
        # not already on the default-model attempt (no infinite retry).
        # GAP-2 (2026-06-14): the CLI can write the model rejection to STDERR
        # while stdout carries no `result` record (so final_result.result is
        # just "claude exited with code 1"). Scan stderr too, otherwise the
        # rejection slips past and the mission fails instead of retrying on the
        # CLI default. (`not timed_out` above means stderr_bytes here is the
        # subprocess's real stderr, never the synthetic timeout message.)
        stderr_text = (
            stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
        )
        if (
            final_result.is_error
            and not timed_out
            and not any_tool_use
            and not force_default_model
            and _claude_error_is_model_unavailable(
                (final_result.result or "") + "\n" + stderr_text
            )
        ):
            logger.warning(
                "ClaudeDirectWorker[%s]: model %r rejected by the CLI (%r) — "
                "retrying without --model so the CLI picks an accessible "
                "default and the mission completes.",
                worker_id, claude_model, (final_result.result or "")[:160],
            )
            async for ev in self.spawn(
                prompt,
                worktree=worktree,
                env=env,
                job=job,
                worker_id=worker_id,
                log_dir=log_dir,
                model=model,
                allowed_tools=allowed_tools,
                permission_mode=permission_mode,
                max_turns=max_turns,
                resume_session_id=None,
                extra_args=extra_args,
                timeout_s=timeout_s,
                first_output_timeout_s=first_output_timeout_s,
                mission_id=mission_id,
                allow_backend_fallback=allow_backend_fallback,
                force_default_model=True,
            ):
                yield ev
            return

        # Auth-failure recovery (2026-07-06, missions 019f36e5 + 019f38b1 —
        # the exact mirror of the codex-side 2026-06-08 incident): the Claude
        # Max OAuth token expired in place (nothing refreshes ~/.claude on
        # this host anymore) and every spawn died "Failed to authenticate.
        # API Error: 401" while a healthy codex login + OpenRouter key were
        # available. Dead auth is not a quota window — it never resets on a
        # clock — so retrying the same credential is a guaranteed second 401:
        # 1. ALWAYS mark claude_auth_dead (fingerprinting the credential that
        #    401'd) so the worker factory routes the rest of the session
        #    cross-family until the user re-authenticates (AP-22).
        # 2. When codex is reachable, complete THIS mission on the codex
        #    worker in place (subscription-first, same shape as the quota
        #    fallback below). `allow_backend_fallback=False` on the nested
        #    spawn prevents claude->codex->claude ping-pong.
        if (
            final_result.is_error
            and not timed_out
            and not any_tool_use
            and _claude_error_is_auth_failure(
                (final_result.result or "") + "\n" + stderr_text
            )
        ):
            from jarvis.claude_auth_state import (
                credential_fingerprint,
                mark_claude_auth_dead,
            )

            mark_claude_auth_dead(
                fingerprint=credential_fingerprint(
                    env.get("CLAUDE_CODE_OAUTH_TOKEN")
                    or env.get("ANTHROPIC_API_KEY")
                )
            )
            if allow_backend_fallback:
                from jarvis.codex_auth_state import codex_needs_reauth
                from jarvis.codex_quota_state import codex_in_quota_cooldown

                from .codex_direct_worker import (
                    CodexDirectWorker,
                    _codex_oauth_available,
                )

                if (
                    _codex_oauth_available()
                    and not codex_needs_reauth()
                    and not codex_in_quota_cooldown()
                ):
                    logger.warning(
                        "ClaudeDirectWorker[%s]: claude auth is dead (%r) with "
                        "no work delivered — falling back to the codex "
                        "(ChatGPT) worker so the mission completes. Run "
                        "`claude /login` (or save a fresh Anthropic key in the "
                        "API-Keys view) to use Claude again.",
                        worker_id, (final_result.result or "")[:160],
                    )
                    async for ev in CodexDirectWorker().spawn(
                        prompt,
                        worktree=worktree,
                        env=env,
                        job=job,
                        worker_id=worker_id,
                        log_dir=log_dir,
                        model="",  # codex picks its ChatGPT default
                        allowed_tools=allowed_tools,
                        max_turns=max_turns,
                        timeout_s=timeout_s,
                        mission_id=mission_id,
                        allow_backend_fallback=False,
                    ):
                        yield ev
                    return
            logger.error(
                "ClaudeDirectWorker[%s]: claude auth is dead (%r) and no codex "
                "login is reachable — surfacing the auth error honestly; the "
                "worker factory will cross to another provider family on the "
                "retry. Run `claude /login` to restore Claude.",
                worker_id, (final_result.result or "")[:160],
            )

        # Claude Max quota fallback (mirror of the codex->claude direction).
        # Live mission 019eb2fd (2026-06-10 21:23): with the Claude Max
        # five-hour window exhausted ("You've hit your session limit · resets
        # 11:10pm"), every claude-routed mission died in ~16 s while codex (a
        # separate ChatGPT subscription) was healthy. When the limit hit
        # BEFORE any real work (no tool_use — delivered work is never
        # discarded, the orchestrator grades it instead), complete the
        # mission on the codex worker. `allow_backend_fallback=False` on the
        # nested spawn prevents claude->codex->claude ping-pong.
        if (
            allow_backend_fallback
            and final_result.is_error
            and not timed_out
            and not any_tool_use
        ):
            from jarvis.codex_auth_state import codex_needs_reauth
            from jarvis.codex_quota_state import codex_in_quota_cooldown

            from .codex_direct_worker import (
                CodexDirectWorker,
                _codex_error_is_usage_limited,
                _codex_oauth_available,
            )

            if _codex_error_is_usage_limited(final_result.result or ""):
                # Arm the session quota cooldown so the worker factory routes
                # subsequent missions STRAIGHT to codex (no wasted ~16 s Claude
                # probe) until the window resets — the proactive complement to
                # this reactive fallback.
                from jarvis.claude_quota_state import mark_claude_quota_cooldown

                mark_claude_quota_cooldown()

            if (
                _codex_error_is_usage_limited(final_result.result or "")
                and _codex_oauth_available()
                and not codex_needs_reauth()
                and not codex_in_quota_cooldown()
            ):
                logger.warning(
                    "ClaudeDirectWorker[%s]: Claude Max quota limit hit (%r) "
                    "with no work delivered — falling back to the codex "
                    "(ChatGPT) worker so the mission completes. claude "
                    "resumes automatically once its window resets.",
                    worker_id, (final_result.result or "")[:160],
                )
                async for ev in CodexDirectWorker().spawn(
                    prompt,
                    worktree=worktree,
                    env=env,
                    job=job,
                    worker_id=worker_id,
                    log_dir=log_dir,
                    model="",  # codex picks its ChatGPT default
                    allowed_tools=allowed_tools,
                    max_turns=max_turns,
                    timeout_s=timeout_s,
                    mission_id=mission_id,
                    allow_backend_fallback=False,
                ):
                    yield ev
                return

        yield final_result


__all__ = [
    "ClaudeDirectWorker",
    "_resolve_claude_argv_prefix",
    "_resolve_claude_binary",
]
