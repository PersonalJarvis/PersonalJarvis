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
import os
import shutil
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path
from typing import Any, ClassVar, Literal

from .capabilities import WorkerCapabilityInventory
from .process_utils import create_worker_subprocess, resolve_node_executable
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
# StreamReader buffer limit for line-by-line reads. asyncio's default is
# 64 KiB; a single codex ``item.completed`` line carrying a command's
# ``aggregated_output`` routinely exceeds that (multi-hundred-KB lines seen
# live), and ``readline()`` raises ValueError past the limit. 8 MiB keeps
# even pathological lines readable without buffering the whole run.
_STREAM_READLINE_LIMIT: int = 8 * 1024 * 1024
# Grace added on top of ``timeout_s`` for the wall-clock hard cap (parity with
# the pre-streaming ``communicate(timeout=timeout_s + 30)`` behaviour). Module
# constant so tests can shrink it instead of sleeping 30 real seconds.
_HARDCAP_GRACE_S: float = 30.0

# Codex reasoning-effort tier for MISSION workers, overriding ~/.codex/config.toml
# for the worker subprocess ONLY. The user's interactive config may pin a very
# high tier ("xhigh"), but a sub-agent mission re-runs the worker across up to
# MAX_CRITIC_LOOPS iterations, so a 7-minute xhigh reasoning pass per run makes a
# mission drag for 10-15 min (live mission 019ec742, 2026-06-14: 452s + 399s
# worker runs -> 899s critic_loop_exhausted). "medium" keeps strong code/analysis
# quality at a fraction of the latency. Passed as `-c model_reasoning_effort=...`,
# which a CLI override wins over config.toml.
_MISSION_REASONING_EFFORT: str = "medium"

# ``--ignore-user-config`` is required so a mission cannot inherit arbitrary
# user MCP servers or plugins, but it also removes the native Windows sandbox
# selection.  Codex then starts in a partial state where shell reads work while
# the file-change tool reports the workspace as read-only.  The unelevated
# implementation is available without administrator-approved setup and still
# confines shell writes to the worktree through Windows ACLs.
_WINDOWS_SANDBOX_MODE: str = "unelevated"
_WINDOWS_WRITE_GUIDANCE: str = (
    "Native Windows execution note: if the patch or file-change tool reports "
    "that the workspace is read-only, use a PowerShell shell command to write "
    "inside the current working directory instead. Write text as UTF-8 without "
    "a byte-order mark; Windows PowerShell's Set-Content can add one, so prefer "
    "System.IO.File.WriteAllText with System.Text.UTF8Encoding(false). Never "
    "write outside the current worktree."
)


def _resolve_codex_binary() -> str | None:
    """Return the on-PATH ``codex`` binary, considering Windows extensions.

    Kept for the fallback path in :func:`_resolve_codex_argv_prefix` and for
    tests that pin a single string. Production spawns should call
    :func:`_resolve_codex_argv_prefix` instead — it returns the full argv
    prefix (``node`` + ``bin/codex.js``) that sidesteps the ``codex.CMD`` shim.
    """
    for name in ("codex", "codex.cmd", "codex.exe"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _resolve_codex_argv_prefix() -> list[str]:
    """Return the argv prefix for invoking codex, preferring ``node <codex.js>``.

    On Windows the npm-installed CLI ships as ``codex.CMD`` — a batch shim whose
    tail line resolves the interpreter as the *bare* command ``node`` via PATH:

        ... || title %COMSPEC% & "%_prog%" "...\\bin\\codex.js" %*   (_prog="node")

    When jarvis is launched with a degraded PATH that lacks the Node.js dir
    (live forensic 2026-06-20: jarvis started by the hermes-agent runtime),
    cmd.exe dies with ``'node' is not recognized`` and exits 1 in ~25 ms —
    BEFORE codex starts — so every mission fails ``task_error``
    ("Der Worker ist abgebrochen.").  # i18n-allow: actual DE voice readback
    Invoking the JS entrypoint with an ABSOLUTE ``node`` path
    bypasses the .CMD shim, the cmd.exe layer, and the inherited-PATH
    dependency entirely. Mirrors ``gemini_worker._resolve_gemini_argv_prefix``
    and ``provider_chain._resolve_worker_argv_prefix`` (same class of fix).

    Falls back to the bare codex binary only when ``node`` + the JS entrypoint
    cannot be located together — the prompt is passed on stdin, so the cmd.exe
    metacharacter trap does not apply to that fallback.

    Node is resolved via :func:`resolve_node_executable`, which probes well-known
    install locations when the inherited PATH is degraded — otherwise the very
    PATH gap that breaks ``codex.CMD`` would also hide node from this bypass.
    """
    node = resolve_node_executable()
    if node:
        for name in ("codex", "codex.cmd", "codex.exe"):
            cli = shutil.which(name)
            if not cli:
                continue
            cli_dir = Path(cli).resolve().parent
            # npm shim layout: ``<npm-root>/codex.cmd`` →
            # ``<npm-root>/node_modules/@openai/codex/bin/codex.js``.
            candidate = cli_dir / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
            if candidate.is_file():
                return [node, str(candidate)]
    return [_resolve_codex_binary() or "codex"]


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
_CLAUDE_MODEL_ALIASES: frozenset[str] = frozenset(
    {
        "sonnet",
        "opus",
        "haiku",
    }
)


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
    mcp_servers: dict[str, Any] | None = None,
    windows_sandbox: str | None = None,
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
        *_resolve_codex_argv_prefix(),
        "exec",
        "--json",
        "--skip-git-repo-check",
        # Auth still comes from CODEX_HOME, but mission capabilities must never
        # come from the user's machine-global config. Explicit unsupported MCP
        # inventory is safer than silently inheriting unrelated plugins.
        "--ignore-user-config",
        "--sandbox",
        sandbox,
        "-c",
        f"approval_policy={approval_policy}",
        # Cap reasoning effort for SPEED, overriding the user's interactive
        # config (often "xhigh" -> ~7 min/run). A CLI `-c` override wins over
        # config.toml. See _MISSION_REASONING_EFFORT (live mission 019ec742).
        "-c",
        f"model_reasoning_effort={_MISSION_REASONING_EFFORT}",
        # Enable web search so research / current-events missions can produce
        # SOURCED work instead of fabricating it. Without it the worker has no
        # live data, invents current events (GPT-5.5, a 2026 AI Agent Index),
        # and the critic correctly rejects the hallucinations 3x ->
        # critic_loop_exhausted (live mission 019ecb56, 2026-06-15: "research
        # the AI news of the last years" failed at 1042s). codex's web_search is
        # a server-routed, read-only tool — a live probe confirmed it returns
        # current data inside the workspace-write sandbox WITHOUT opening raw
        # network access, so this is the targeted capability, not a blast-radius
        # network grant.
        "-c",
        "tools.web_search=true",
        # D9 recursion guard at the codex level. A mission worker IS the
        # sub-agent — it must NEVER use codex's native multi-agent collaboration
        # tools (spawn_agent / wait) to spawn a NESTED codex agent and block on
        # it. Live mission 019ec708 (2026-06-14): a prompt phrased "spawn a
        # sub-agent which will plan a trip" made the worker call
        # spawn_agent("Hooke") then `wait`, freezing the mission for the full
        # worker timeout. Jarvis's tool-layer guard (AP-5 / AP-14: no spawn tool
        # in any worker set) can't see codex's built-in feature, so we turn it
        # off here. `--disable <FEATURE>` == `-c features.<name>=false`;
        # multi_agent is `stable`/on-by-default, multi_agent_v2 is the
        # in-development successor (future-proofing).
        "--disable",
        "multi_agent",
        "--disable",
        "multi_agent_v2",
        "--add-dir",
        str(worktree),
        "--cd",
        str(worktree),
    ]
    if windows_sandbox:
        cmd.extend(["-c", f"windows.sandbox={json.dumps(windows_sandbox)}"])
    if model:
        cmd.extend(["--model", model])
    for server_id, server in sorted((mcp_servers or {}).items()):
        # Server ids are generated by Jarvis and contain only identifier-safe
        # characters. Values are TOML literals passed as separate argv entries;
        # the mission token is inherited via env and never appears here.
        command = str(server.get("command") or "")
        args = [str(value) for value in (server.get("args") or [])]
        if not command:
            continue
        cmd.extend(
            [
                "-c",
                f"mcp_servers.{server_id}.command={json.dumps(command)}",
                "-c",
                f"mcp_servers.{server_id}.args={json.dumps(args)}",
            ]
        )
    cmd.extend(extra_args)
    return cmd


def _prepare_codex_prompt(
    prompt: str,
    *,
    native_windows: bool | None = None,
) -> str:
    """Add the bounded Windows write recovery instruction when applicable."""
    on_windows = os.name == "nt" if native_windows is None else native_windows
    if not on_windows:
        return prompt
    return f"{_WINDOWS_WRITE_GUIDANCE}\n\n{prompt}"


def _codex_sandbox_write_rejected(stderr_text: str) -> bool:
    """Return whether Codex reported a rejected workspace write."""
    low = stderr_text.lower()
    return any(
        marker in low
        for marker in (
            "patch rejected: writing is blocked by read-only sandbox",
            "failed to write file",
        )
    )


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
    # Claude Max five-hour-window phrasing — the marker list is shared with
    # ClaudeDirectWorker's mirror fallback (live mission 019eb2fd,
    # 2026-06-10: "You've hit your session limit · resets 11:10pm").
    "session limit",
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

    def __init__(
        self,
        *,
        capability_inventory: WorkerCapabilityInventory | None = None,
    ) -> None:
        self.last_pid: int | None = None
        self.last_session_id: str | None = None
        self.last_thread_id: str | None = None
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
        allowed_tools: str = "",  # unused -- codex has its own toolset
        permission_mode: str = "",  # unused
        max_turns: int = 20,  # unused -- codex turn cap is internal
        resume_session_id: str | None = None,
        extra_args: tuple[str, ...] = (),
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        first_output_timeout_s: float = _DEFAULT_FIRST_OUTPUT_TIMEOUT_S,
        mission_id: str = "",
        allow_backend_fallback: bool = True,
        _broker_binding: Any | None = None,
        **_unused: Any,
    ) -> AsyncIterator[Any]:
        """Run one Codex worker lifecycle under one mission-scoped grant."""
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
                allowed_tools=allowed_tools,
                permission_mode=permission_mode,
                max_turns=max_turns,
                resume_session_id=resume_session_id,
                extra_args=extra_args,
                timeout_s=timeout_s,
                first_output_timeout_s=first_output_timeout_s,
                mission_id=mission_id,
                allow_backend_fallback=allow_backend_fallback,
                broker_binding=broker_binding,
                **_unused,
            ):
                yield event
        finally:
            if issued_here and broker_binding is not None:
                try:
                    broker_binding.close()
                except Exception:  # noqa: BLE001 - cleanup must not mask cancellation
                    logger.exception("CodexDirectWorker: broker binding cleanup failed")

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
        allowed_tools: str = "",  # unused -- codex has its own toolset
        permission_mode: str = "",  # unused
        max_turns: int = 20,  # unused -- codex turn cap is internal
        resume_session_id: str | None = None,
        extra_args: tuple[str, ...] = (),
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        first_output_timeout_s: float = _DEFAULT_FIRST_OUTPUT_TIMEOUT_S,
        mission_id: str = "",
        allow_backend_fallback: bool = True,
        broker_binding: Any | None,
        **_unused: Any,
    ) -> AsyncIterator[Any]:
        """Spawn ``codex exec --json`` with the prompt on stdin.

        Yields a synthetic ``ClaudeSystemInit`` first (so WorkerSpawned
        fires upstream with a session-id), then forwards translated
        codex events, ending with a synthetic ``ClaudeResult``.
        """
        # This single setup mkdir happens before subprocess streaming starts.
        log_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        stdout_log = log_dir / "stream.jsonl"
        stderr_log = log_dir / "stderr.log"
        # The orchestrator reuses one log directory across critic iterations.
        # Clear it before any subprocess setup so even a spawn failure cannot
        # leave stale evidence masquerading as the current worker attempt.
        with suppress(OSError):
            stdout_log.write_bytes(b"")

        session_id = resume_session_id or str(uuid.uuid4())
        self.last_session_id = session_id
        broker_servers = (
            broker_binding.mcp_server_config() if broker_binding is not None else {}
        )

        # Normalize Anthropic-flavoured aliases ("sonnet", "claude-...",
        # ...) to empty so codex uses its ChatGPT-subscription default.
        # See _normalize_model_for_codex docstring + the 2026-05-18 audit
        # at docs/jarvis-agents-spawn-failure-analysis-2026-05-18.md.
        effective_model = _normalize_model_for_codex(model) or None
        cmd = _build_codex_direct_cmd(
            worktree=worktree,
            model=effective_model,
            extra_args=extra_args,
            mcp_servers=broker_servers,
            windows_sandbox=_WINDOWS_SANDBOX_MODE if os.name == "nt" else None,
        )

        # Honor both auth models (see _build_codex_env). CODEX_HOME is always
        # dropped (per-mission dir breaks the global OAuth home). OPENAI_API_KEY
        # is dropped only when a ChatGPT (OAuth) login exists, so the free
        # subscription wins; an API-key-only setup keeps the key (API mode).
        env_for_codex = _build_codex_env(env, oauth_available=_codex_oauth_available())
        if broker_binding is not None:
            env_for_codex = broker_binding.apply_environment(env_for_codex)

        yield ClaudeSystemInit(
            session_id=session_id,
            model=f"codex-cli/{model or 'default'}",
            tools=[],
            cwd=str(worktree),
            external_capabilities=self.capability_inventory.report_for(
                "codex-cli", binding=broker_binding
            ),
        )

        logger.info(
            "CodexDirectWorker[%s] spawn: cwd=%s model=%s argv=%s",
            worker_id,
            worktree,
            model or "<default>",
            cmd,
        )

        # The helper sources the Windows creation flags itself and degrades
        # CREATE_BREAKAWAY_FROM_JOB gracefully when the host process is in a job
        # that forbids breakaway (WinError 5, live mission 019ec602 2026-06-14).
        t0 = time.perf_counter()
        try:
            proc = await create_worker_subprocess(
                cmd,
                cwd=str(worktree),
                env=env_for_codex,
                stdin=asyncio.subprocess.PIPE,
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
                result=f"CodexDirectWorker: codex binary not found: {exc}",
            )
            return

        self.last_pid = proc.pid
        try:
            job.assign(proc.pid)
        except Exception:  # noqa: BLE001
            logger.warning(
                "CodexDirectWorker[%s]: job.assign(pid=%d) failed",
                worker_id,
                proc.pid,
                exc_info=True,
            )

        # Write the prompt to stdin then close to signal EOF.
        try:
            assert proc.stdin is not None  # noqa: S101 -- PIPE always present
            proc.stdin.write(_prepare_codex_prompt(prompt).encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError) as exc:
            logger.warning(
                "CodexDirectWorker[%s]: prompt stdin write failed: %s",
                worker_id,
                exc,
            )

        # Live line-by-line streaming (2026-06-10 root-cause fix, missions
        # 019eb27f/019eb288): the previous first_chunk-read + communicate()
        # collected ALL stdout until process exit, so during the worker's whole
        # runtime there was NO stream.jsonl on disk, NO translated events
        # upstream, and NO visible progress. A gpt-5.5 xhigh worker
        # legitimately "thinks" for many minutes between NDJSON lines — that
        # silence was indistinguishable from a hang, the user pressed the
        # app's Restart button mid-run (jarvis_desktop.log 19:24:12), and the
        # orphaned missions surfaced 30 min later as opaque crash_recovery /
        # ERROR cards with zero artifacts. Now every raw line is tee'd to
        # stream.jsonl IMMEDIATELY (forensics survive ANY exit, including a
        # hard kill) and translated events are yielded while the process runs
        # (UI progress + DB events + heartbeats).
        #
        # Timeout semantics are unchanged: zero bytes within
        # ``first_output_timeout_s`` -> killed for a fast retry; once streaming
        # has begun only the wall-clock hard cap (``timeout_s`` + 30 s grace)
        # applies — there is deliberately NO idle-gap limit between lines, a
        # long silent reasoning phase is legitimate (BUG-032 class).
        # ``timed_out`` stays the STRUCTURED signal the orchestrator reads, and
        # partial output is never discarded (live bug, mission 019eacb8).
        #
        # Translated codex frames (same mapping as before):
        #   thread.started   -> capture thread_id (resume anchor)
        #   item.completed (type=agent_message) -> ClaudeAssistantMessage
        #   item.completed (type=file_change|command_execution) -> synthetic
        #       tool_use ClaudeAssistantMessage (Critic tool-use detection)
        #   turn.completed  -> terminal ClaudeResult(success)
        #   turn.failed | error -> terminal ClaudeResult(error)
        timed_out = False
        timeout_message = ""
        stderr_bytes = b""
        text_acc: list[str] = []
        any_tool_use = False
        terminal_kind: str = "success"
        terminal_message: str | None = None
        cost_usd: float | None = None
        tokens_used: int | None = None
        got_first_line = False
        deadline = time.monotonic() + timeout_s + _HARDCAP_GRACE_S
        assert proc.stdout is not None  # noqa: S101 — PIPE always created above
        assert proc.stderr is not None  # noqa: S101 — PIPE always created above
        # Drain stderr concurrently: codex writes progress/errors there, and a
        # full stderr pipe would deadlock the child once the buffer fills.
        stderr_task = asyncio.create_task(proc.stderr.read())

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                timeout_message = (
                    f"CodexDirectWorker: subprocess wall-clock timeout "
                    f"({timeout_s + _HARDCAP_GRACE_S:.0f}s) exceeded while streaming"
                )
                break
            read_cap = remaining if got_first_line else min(first_output_timeout_s, remaining)
            try:
                raw = await asyncio.wait_for(proc.stdout.readline(), timeout=read_cap)
            except TimeoutError:
                if got_first_line:
                    # read_cap == remaining here, so the deadline check at the
                    # top of the loop turns this into the hard-cap timeout.
                    continue
                timed_out = True
                timeout_message = (
                    f"CodexDirectWorker: subprocess produced no output within "
                    f"{first_output_timeout_s:.0f}s startup timeout (codex emitted "
                    f"zero bytes); killed for retry"
                )
                break
            except ValueError:
                # One NDJSON line exceeded _STREAM_READLINE_LIMIT — the stream
                # buffer is poisoned and cannot be resynced. Fail LOUDLY with
                # the real cause instead of hanging or returning garbage.
                terminal_kind = "error"
                terminal_message = (
                    "CodexDirectWorker: a stdout line exceeded the "
                    f"{_STREAM_READLINE_LIMIT // (1024 * 1024)} MiB stream "
                    "limit; aborting the read loop"
                )
                logger.error("%s", terminal_message)
                break
            if not raw:
                break  # EOF — codex closed stdout.
            got_first_line = True
            try:
                # Append-per-line (open/close each time): no long-lived handle
                # to leak when the generator is cancelled mid-run, and the
                # forensics file is complete up to the very last line no
                # matter how this coroutine ends.
                with stdout_log.open("ab") as stream_fh:
                    stream_fh.write(raw)
            except OSError as exc:
                logger.warning(
                    "CodexDirectWorker: stream.jsonl append failed: %s",
                    exc,
                )
            line = raw.decode("utf-8", errors="replace").strip()
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
                    status = str(item.get("status") or "").lower()
                    exit_code = item.get("exit_code")
                    tool_succeeded = status != "failed" and (
                        item_type != "command_execution" or exit_code in (None, 0)
                    )
                    if not tool_succeeded:
                        continue
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
                            "content": [
                                {
                                    "type": "tool_use",
                                    "name": ("Write" if item_type == "file_change" else "Bash"),
                                    "input": (
                                        item.get("changes", [{}])[0]
                                        if item_type == "file_change"
                                        else {"command": item.get("command", "")}
                                    ),
                                }
                            ],
                        },
                        session_id=session_id,
                    )
                    continue
                continue
            if t == "turn.completed":
                terminal_kind = "success"
                # codex ran successfully -> its ChatGPT login is alive; clear any
                # stale needs_reauth flag so the session uses codex natively again
                # (e.g. after the user ran `codex login`). Same for the quota
                # cooldown: a success proves the cap reset.
                from jarvis.codex_auth_state import clear_codex_needs_reauth
                from jarvis.codex_quota_state import clear_codex_quota_cooldown

                clear_codex_needs_reauth()
                clear_codex_quota_cooldown()
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

        if timed_out:
            with suppress(ProcessLookupError):
                proc.kill()
        with suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=10.0)
        with suppress(Exception):
            stderr_bytes = await asyncio.wait_for(stderr_task, timeout=5.0)
        if timed_out:
            # Surface the timeout verdict in stderr.log alongside whatever
            # codex itself wrote — never lose the real stderr to the synthetic
            # message (pre-fix behaviour overwrote it).
            joiner = b"\n" if stderr_bytes else b""
            stderr_bytes = stderr_bytes + joiner + timeout_message.encode("utf-8")
        if stderr_bytes:
            try:
                stderr_log.write_bytes(stderr_bytes)
            except OSError as exc:
                logger.warning("CodexDirectWorker: stderr.log write failed: %s", exc)

        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        if (
            terminal_kind == "success"
            and not any_tool_use
            and _codex_sandbox_write_rejected(stderr_text)
        ):
            terminal_kind = "error"
            terminal_message = (
                "Codex could not write to the mission worktree because its "
                "sandbox rejected the file operation."
            )

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
            terminal_kind == "error" and not any_tool_use and _codex_error_is_auth_expired(_err)
        )
        _usage_capped = (
            terminal_kind == "error"
            and not any_tool_use
            and not _auth_dead
            and _codex_error_is_usage_limited(_err)
        )
        # `allow_backend_fallback=False` marks a NESTED fallback run (the
        # claude worker already fell back to codex) — surface the error
        # honestly instead of bouncing codex->claude->codex forever.
        if (_auth_dead or _usage_capped) and allow_backend_fallback:
            if _auth_dead:
                from jarvis.codex_auth_state import mark_codex_needs_reauth

                mark_codex_needs_reauth()
            else:
                # Proactive complement to this reactive fallback (2026-07-07,
                # mission_019f3cd8-1dd4): remember the cap so the worker
                # factory skips codex until the cooldown self-expires, instead
                # of burning ~28 s per mission re-proving it. A codex success
                # clears it immediately.
                from jarvis.codex_quota_state import mark_codex_quota_cooldown

                mark_codex_quota_cooldown()
            # Viability gate on the nested Claude spawn (2026-07-07 incident):
            # the fallback used to be HARDCODED to ClaudeDirectWorker, so a
            # usage-capped codex + a dead Claude login looped codex->claude->
            # fail on every iteration while a healthy API key sat unused
            # (AP-22). Only fall back when Claude can actually authenticate;
            # otherwise surface the honest codex error below — the orchestrator
            # classifies it transient/auth and retries, and the factory (seeing
            # the flags armed above) crosses to the user's API-key family.
            from .claude_direct_worker import (
                ClaudeDirectWorker,
                _resolve_claude_binary,
            )

            _claude_viable = _resolve_claude_binary() is not None
            if _claude_viable:
                try:
                    from jarvis.missions import init as _missions_init

                    _claude_viable = _missions_init._claude_cli_auth_viable()
                except Exception:  # noqa: BLE001 — unreadable probe => not viable
                    _claude_viable = False
            if _claude_viable:
                logger.warning(
                    "CodexDirectWorker[%s]: codex %s (%r) — falling back to the "
                    "Claude Max OAuth worker so the mission completes. %s",
                    worker_id,
                    "ChatGPT login expired" if _auth_dead else "usage/rate limit hit",
                    _err[:160],
                    "Run `codex login` to use codex again."
                    if _auth_dead
                    else "codex resumes automatically once the cap resets.",
                )
                async for ev in ClaudeDirectWorker(
                    capability_inventory=self.capability_inventory
                ).spawn(
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
                    allow_backend_fallback=False,  # no codex->claude->codex loop
                    _broker_binding=broker_binding,
                ):
                    yield ev
                return
            logger.error(
                "CodexDirectWorker[%s]: codex %s (%r) and the Claude fallback "
                "is not auth-viable — surfacing the error honestly; the worker "
                "factory will cross to another provider family on the retry.",
                worker_id,
                "ChatGPT login expired" if _auth_dead else "usage/rate limit hit",
                _err[:160],
            )

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
            "CodexDirectWorker[%s] done: exit=%s wall_ms=%s tool_use_seen=%s thread=%s tokens=%s",
            worker_id,
            exit_code,
            wall_ms,
            any_tool_use,
            self.last_thread_id,
            tokens_used,
        )

        yield final


__all__ = [
    "CodexDirectWorker",
    "_build_codex_direct_cmd",
    "_resolve_codex_argv_prefix",
    "_resolve_codex_binary",
]
