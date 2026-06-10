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


# Per-attempt wall-clock cap (20 min). Raised from 600 s so a genuinely
# complex multi-step sub-agent can FINISH, not just deliver partials (user
# mandate 2026-06-09: "more time for complex tasks"). Preserve-partial-work +
# the first-output gate keep a true hang from burning the full cap.
_DEFAULT_TIMEOUT_S: float = 1200.0
# First-output ("startup") gate — mirrors ClaudeDirectWorker. codex is killed +
# flagged for retry when it emits ZERO bytes within this window (a hung startup
# / OAuth contention) instead of burning the full hard cap. Once streaming has
# begun the full cap applies so a legitimately long task is never cut off.
_DEFAULT_FIRST_OUTPUT_TIMEOUT_S: float = 120.0
# Bytes pulled on the first read; codex's opening thread.started line is far
# under this, so the read returns as soon as codex emits anything.
_FIRST_READ_BYTES: int = 65536


def _resolve_codex_binary() -> str | None:
    """Return the on-PATH ``codex`` binary, considering Windows extensions."""
    for name in ("codex", "codex.cmd", "codex.exe"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _codex_oauth_available() -> bool:
    """True when the user's global ``~/.codex`` carries a ChatGPT (OAuth) login.

    Read from the GLOBAL codex home (not the per-mission ``CODEX_HOME``), since
    subscription tokens live in the user's profile. Best-effort: a missing codex
    CLI or any read error degrades to False -> API-key mode keeps OPENAI_API_KEY.
    """
    try:
        from jarvis.codex_auth import CodexAuthService

        status = CodexAuthService().status()
        return bool(status.connected and status.mode == "chatgpt")
    except Exception:  # noqa: BLE001
        return False


def _build_codex_env(env: dict[str, str], *, oauth_available: bool) -> dict[str, str]:
    """Env for ``codex exec``, honoring both auth models.

    Always drops ``CODEX_HOME``: build_worker_env points it at a per-mission dir
    so parallel workers don't share state, but the global OAuth tokens live in
    the user's ``~/.codex``; a per-mission home makes codex error "Error finding
    codex home". ``OPENAI_API_KEY`` is dropped ONLY when OAuth is available, so
    the free subscription wins; an API-key-only setup keeps the key and runs
    codex in API mode. Returns a new dict — the input is not mutated.
    """
    drop = {"CODEX_HOME"}
    if oauth_available:
        drop.add("OPENAI_API_KEY")
    return {k: v for k, v in env.items() if k not in drop}


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


# Markers in a codex ``turn.failed`` / ``error`` event that mean the ChatGPT
# OAuth session is dead and cannot be refreshed — the user must re-run
# ``codex login``. ``codex status`` still reports connected=True (it only checks
# token PRESENCE, not validity), so the mission would otherwise fail opaquely.
# When we see these we fall back to the Claude Max worker so the mission still
# completes (the 2026-06-08 "all sub-missions fail on codex" incident).
_CODEX_AUTH_EXPIRED_MARKERS: tuple[str, ...] = (
    "log in again",
    "login again",
    "refresh token",
    "not logged in",
    "please login",
    "please log in",
    "unauthorized",
    "401",
    "authentication failed",
    "auth error",
    "token expired",
    "expired token",
)

# Markers that mean the codex ChatGPT plan is temporarily UNAVAILABLE (usage /
# rate / credit cap), even though the login is valid — e.g. "You've hit your
# usage limit … try again at 7:40 PM". The login is fine (no `codex login`
# needed); codex just can't run right now. We fall back to the Claude Max worker
# so the mission completes, and the user keeps using codex once the cap resets
# (2026-06-09: a re-authenticated codex hit its usage limit and the mission
# failed task_error instead of falling back).
_CODEX_USAGE_LIMIT_MARKERS: tuple[str, ...] = (
    "usage limit",
    "hit your usage limit",
    "purchase more credits",
    "try again at",
    "try again later",
    "upgrade to pro",
    "rate limit",
    "rate_limit",
    "too many requests",
    "429",
    "quota",
    "insufficient_quota",
)


def _coerce_codex_error_text(obj: dict[str, Any]) -> str:
    """Extract a plain string from a codex ``error`` / ``turn.failed`` event.

    Codex sometimes nests the message as a dict, e.g.
    ``{"type": "error", "message": {"message": "Failed to refresh token. ...
    Please log in again."}}``. Feeding that dict straight into
    ``ClaudeResult(result=...)`` (a ``str`` field) raised a Pydantic
    ``ValidationError`` and CRASHED the worker mid-spawn → opaque ``task_error``
    (forensic 2026-06-08, mission 019ea8db). Always return a plain string so the
    real cause (expired ChatGPT login) survives instead of a crash.
    """
    for key in ("message", "error"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, dict):
            for inner in ("message", "error", "text", "detail"):
                iv = val.get(inner)
                if isinstance(iv, str) and iv.strip():
                    return iv.strip()
            try:
                return json.dumps(val, ensure_ascii=False)
            except (TypeError, ValueError):
                return str(val)
    return str(obj.get("type") or "codex error")


def _codex_error_is_auth_expired(text: str) -> bool:
    """True when a codex error string signals a dead/unrefreshable ChatGPT login."""
    low = (text or "").lower()
    return any(marker in low for marker in _CODEX_AUTH_EXPIRED_MARKERS)


def _codex_error_is_usage_limited(text: str) -> bool:
    """True when codex is temporarily unavailable (usage/rate/credit cap), even
    though the login is valid — fall back to Claude Max, no `codex login` needed."""
    low = (text or "").lower()
    return any(marker in low for marker in _CODEX_USAGE_LIMIT_MARKERS)


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
        first_output_timeout_s: float = _DEFAULT_FIRST_OUTPUT_TIMEOUT_S,
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

        # Honor both auth models (see _build_codex_env). CODEX_HOME is always
        # dropped (per-mission dir breaks the global OAuth home). OPENAI_API_KEY
        # is dropped only when a ChatGPT (OAuth) login exists, so the free
        # subscription wins; an API-key-only setup keeps the key (API mode).
        env_for_codex = _build_codex_env(env, oauth_available=_codex_oauth_available())

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

        # First-output gate + preserve-partial-work on a hard-cap timeout
        # (mirrors ClaudeDirectWorker). codex that emits ZERO bytes within the
        # startup window is killed for a fast retry; once streaming has begun, a
        # hard-cap timeout PRESERVES first_chunk so the item.completed /
        # file_change events survive parsing (the deliverable itself is already
        # on disk in the worktree, captured by git after the worker exits).
        # `timed_out` is the STRUCTURED signal the orchestrator reads — not the
        # result-text wording — so a timed-out codex run that left a real diff is
        # GRADED, not discarded as task_error (live bug, mission 019eacb8).
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
                f"CodexDirectWorker: subprocess produced no output within "
                f"{first_output_timeout_s:.0f}s startup timeout (codex emitted "
                f"zero bytes); killed for retry"
            )

        if not timed_out:
            try:
                rest_stdout, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_s + 30.0
                )
            except asyncio.TimeoutError:
                timed_out = True
                rest_stdout = b""
                timeout_message = (
                    f"CodexDirectWorker: subprocess wait_for timeout "
                    f"({timeout_s + 30.0}s) after streaming started"
                )
            stdout_bytes = first_chunk + rest_stdout

        if timed_out:
            with suppress(ProcessLookupError):
                proc.kill()
            with suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            stdout_bytes = first_chunk  # PRESERVE partial output, never discard
            stderr_bytes = timeout_message.encode("utf-8")

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
                # codex ran successfully -> its ChatGPT login is alive; clear any
                # stale needs_reauth flag so the session uses codex natively again
                # (e.g. after the user ran `codex login`).
                from jarvis.codex_auth_state import clear_codex_needs_reauth

                clear_codex_needs_reauth()
                usage = obj.get("usage", {}) or {}
                if isinstance(usage, dict):
                    in_tok = usage.get("input_tokens") or 0
                    out_tok = usage.get("output_tokens") or 0
                    tokens_used = int(in_tok) + int(out_tok)
                continue
            if t in ("turn.failed", "error"):
                terminal_kind = "error"
                terminal_message = _coerce_codex_error_text(obj)
                continue

        wall_ms = int((time.perf_counter() - t0) * 1000)
        exit_code = proc.returncode if proc.returncode is not None else -1

        # Codex temporarily can't run → fall back to the Claude Max worker so the
        # mission COMPLETES instead of failing. Two cases, both guarded to codex
        # having done NO real work (so delivered work is never discarded):
        #   * login DEAD (400/401/"log in again") — needs `codex login`; flag the
        #     session so we don't re-spawn the dead provider every mission
        #     (2026-06-08 incident).
        #   * usage/rate/credit CAP ("hit your usage limit … try again at 7:40 PM")
        #     — login is fine, codex just resumes once the cap resets; fall back
        #     but do NOT flag, so the next mission retries codex automatically
        #     (2026-06-09: a re-authed codex hit its cap and failed task_error).
        # We intentionally do NOT yield the codex error event: _spawn_worker_collect
        # only sets worker_error on an is_error event, so skipping it lets the
        # claude result drive the outcome; the git diff is captured after.
        _err = terminal_message or ""
        _auth_dead = (
            terminal_kind == "error" and not any_tool_use
            and _codex_error_is_auth_expired(_err)
        )
        _usage_capped = (
            terminal_kind == "error" and not any_tool_use
            and not _auth_dead
            and _codex_error_is_usage_limited(_err)
        )
        if _auth_dead or _usage_capped:
            if _auth_dead:
                logger.warning(
                    "CodexDirectWorker[%s]: codex ChatGPT login expired (%r) — "
                    "falling back to the Claude Max OAuth worker so the mission "
                    "completes. Run `codex login` to use codex again.",
                    worker_id, _err[:160],
                )
                from jarvis.codex_auth_state import mark_codex_needs_reauth

                mark_codex_needs_reauth()
            else:
                logger.warning(
                    "CodexDirectWorker[%s]: codex usage/rate limit hit (%r) — "
                    "falling back to the Claude Max OAuth worker so the mission "
                    "completes. codex resumes automatically once the cap resets.",
                    worker_id, _err[:160],
                )
            from .claude_direct_worker import ClaudeDirectWorker

            async for ev in ClaudeDirectWorker().spawn(
                prompt,
                worktree=worktree,
                env=env,
                job=job,
                worker_id=worker_id,
                log_dir=log_dir,
                model="",  # let ClaudeDirectWorker resolve a valid claude model
                allowed_tools=allowed_tools,
                permission_mode="bypassPermissions",
                max_turns=max_turns,
                timeout_s=timeout_s,
                mission_id=mission_id,
            ):
                yield ev
            return

        final = ClaudeResult(
            subtype="success"
            if terminal_kind == "success" and exit_code == 0 and not timed_out
            else "error_during_execution",
            is_error=(timed_out or terminal_kind != "success" or exit_code != 0),
            timed_out=timed_out,
            cost_usd=cost_usd,
            num_turns=None,
            session_id=session_id,
            duration_ms=wall_ms,
            result=str(
                # On timeout the message wins (carries "timeout" + the structured
                # timed_out flag); otherwise the parsed terminal message / text.
                timeout_message
                or terminal_message
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
