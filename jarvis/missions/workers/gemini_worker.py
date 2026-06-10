"""GeminiWorker — wrapt `gemini -p ... --yolo` als Phase-6-Worker-Subprocess.

Driven by the Gemini CLI
(<USER_HOME>\\AppData\\Roaming\\npm\\gemini.cmd, ships via
`@google/gemini-cli`). Lets a mission honor the brain provider the user
selected in the desktop app (`jarvis.toml [brain] primary = "gemini"`)
instead of always falling back to Claude.

Cmd-Layout:

    gemini --prompt <prompt>
           --model <gemini-3.1-pro-preview | gemini-3-flash-preview | ...>
           --yolo
           --output-format text

We deliberately use plain `text` output instead of `stream-json`: the
Gemini CLI's stream-json schema isn't a 1:1 match for the Claude
schema we already parse, and the Kontrollierer doesn't need
intermediate stream events — only WorkerSpawned + a terminal result so
the diff-collector + Critic can take over. Yielding two synthetic
`Claude*`-shaped events keeps the orchestrator code path unchanged
(field shape mirrors `ClaudeSystemInit` + `ClaudeResult`).

Spawn discipline:
- `asyncio.create_subprocess_exec` (NO shell=True, NO PTY).
- Win32 creationflags incl. CREATE_BREAKAWAY_FROM_JOB so the per-mission
  Windows Job Object can assign the subprocess.
- `env=...` strikt aus `build_worker_env` (Whitelist-only); we expect
  GEMINI_API_KEY / GOOGLE_API_KEY to be injected by the caller.
- `cwd=worktree` — Gemini CLI writes/reads files relative to cwd, so
  hello.py-style tasks land inside the git worktree and the
  Kontrollierer's `_capture_diff` picks them up (`git add -N .` + `git
  diff HEAD`).
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from .process_utils import worker_creationflags as _win32_creationflags
from .stream_consumer import ClaudeResult, ClaudeSystemInit

logger = logging.getLogger(__name__)


_GEMINI_BINARIES: tuple[str, ...] = ("gemini.cmd", "gemini")

# Quota-fallback chain: if the Frontier model returns 429 / QUOTA_EXHAUSTED
# we retry on Flash. Flash has its own quota bucket and stays available even
# when Pro is rolling-window-exhausted (verified live 2026-05-13: Pro had
# 6h+ reset while Flash answered immediately on the same key).
_QUOTA_BLOCKED_MARKERS: tuple[str, ...] = (
    "QUOTA_EXHAUSTED",
    "code: 429",
    "exhausted your capacity",
    "PERMISSION_DENIED",
)
_FALLBACK_MODEL: str = "gemini-3-flash-preview"


def _stderr_signals_quota_block(stderr_bytes: bytes) -> bool:
    """True if the CLI stderr indicates the Frontier model is unavailable
    (rolling-window 429 quota or 403 access denial). Used to decide whether
    to retry with the cheaper Flash variant before failing the mission.
    """
    if not stderr_bytes:
        return False
    text = stderr_bytes.decode("utf-8", errors="replace")
    return any(marker in text for marker in _QUOTA_BLOCKED_MARKERS)


def _resolve_gemini_argv_prefix() -> list[str]:
    """Returns the argv prefix for invoking the Gemini CLI.

    On Windows, the npm-installed CLI ships as `gemini.cmd` — a batch
    wrapper around `node bundle/gemini.js`. Calling `.cmd` from
    `asyncio.create_subprocess_exec` makes cmd.exe re-parse the full
    argv with batch tokenizer rules, and that tokenizer treats `<`,
    `>`, `&`, `|`, `^`, `%` as metacharacters. Embedding a JSON
    schema (which contains `<`) in `--prompt` then causes
    CreateProcess to fail with `Das System kann die angegebene Datei
    nicht finden` long before the model is ever invoked — verified
    live 2026-05-13.

    Skipping the .cmd wrapper entirely by invoking `node ...gemini.js`
    directly avoids the second-stage parser and lets any payload
    through verbatim. We only fall back to the bare CLI binary when
    we can't locate node + the JS entrypoint.
    """
    import shutil  # noqa: PLC0415

    node = shutil.which("node") or shutil.which("node.exe")
    if node:
        # The npm shim stores the actual JS bundle at a predictable
        # path relative to the .cmd shim. Search the canonical layout.
        for name in _GEMINI_BINARIES:
            cli = shutil.which(name)
            if not cli:
                continue
            cli_dir = Path(cli).resolve().parent
            candidate = (
                cli_dir / "node_modules" / "@google" / "gemini-cli"
                / "bundle" / "gemini.js"
            )
            if candidate.is_file():
                return [node, str(candidate)]

    # Fallback: bare CLI (still works for prompts with no metachars).
    for name in _GEMINI_BINARIES:
        cli = shutil.which(name)
        if cli:
            return [cli]
    return ["gemini"]


def _resolve_gemini_model(requested: str | None) -> str:
    """Returns the Gemini model slug to use for this worker call.

    Resolution rules (highest priority first):
        1. If `requested` is a Gemini slug already (starts with "gemini"),
           pass it through verbatim.
        2. Otherwise read `cfg.brain.providers.gemini.deep_model` from
           jarvis.toml — that's the Frontier slot the user owns.
        3. As a last resort (config unreachable, e.g. test environment
           without a project layout), fall back to the documented
           Frontier model at the time this code was written.

    Why not hardcode Flash: the user is on Pay-as-you-go and explicitly
    asked for Frontier-quality output, not the cheaper variant. The
    "deep_model" config field exists specifically so newer Frontier
    models (Gemini 4, etc.) can be adopted via a one-line TOML change.
    See `memory/feedback_frontier_models.md`.
    """
    if requested and requested.lower().startswith("gemini"):
        return requested
    try:
        from jarvis.core.config import load_config

        cfg = load_config()
        providers = cfg.brain.providers or {}
        gemini_cfg = providers.get("gemini")
        if gemini_cfg is not None:
            deep = getattr(gemini_cfg, "deep_model", None)
            if deep:
                return str(deep)
            model = getattr(gemini_cfg, "model", None)
            if model:
                return str(model)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "GeminiWorker: config lookup failed (%s) — using Frontier fallback",
            exc,
        )
    # Documented Frontier @ 2026-Q2; keep in sync with jarvis.toml defaults.
    return "gemini-3.1-pro-preview"


def _resolve_gemini_binary() -> str:
    """Returns the absolute path to the Gemini CLI executable.

    Kept for backward compatibility with tests that pin a single
    string. Production code should call `_resolve_gemini_argv_prefix()`
    instead — it returns the full argv prefix (node + bundle.js) that
    sidesteps the cmd.exe metacharacter trap.
    """
    for name in _GEMINI_BINARIES:
        path = shutil.which(name)
        if path:
            return path
    return "gemini"


def _build_gemini_cmd(
    prompt: str,
    *,
    model: str,
    yolo: bool = True,
    extra_args: tuple[str, ...] = (),
) -> list[str]:
    """Constructs the Gemini CLI argv.

    Stable order so the dry-run test can pin it argument-for-argument.
    `--yolo` is on by default — the worker is already running inside a
    per-mission git worktree + Windows Job Object, so "auto-approve all
    tools" is the safe shape for an unattended worker.

    Prompt sanitization (Windows-only nasty): newlines inside a
    `--prompt <text>` CLI argument get mangled by the `gemini.cmd`
    batch wrapper before they reach the Node entrypoint. Concretely
    `Create hello.py\\nThen exit.` arrives at the LLM as just
    `Create hello.py` (and the LLM helpfully replies "I am ready for
    your instructions, no explicit request was provided"). We collapse
    every newline to a single space — it preserves the LLM-visible
    intent (Gemini doesn't care about line breaks for plain
    imperative prompts) and dodges the cmd-wrapper bug. Verified
    live 2026-05-13: same prompt with newlines fails, without
    newlines succeeds, on both Flash and Pro.
    """
    safe_prompt = " ".join(prompt.split())
    cmd: list[str] = [
        *_resolve_gemini_argv_prefix(),
        "--prompt",
        safe_prompt,
        "--model",
        model,
        "--output-format",
        "text",
    ]
    if yolo:
        cmd.append("--yolo")
    cmd.extend(extra_args)
    return cmd


class GeminiWorker:
    """Phase-6-Worker that drives the Gemini CLI.

    Mirrors the `WorkerProtocol` contract — same async-iterator
    spawn(), same event shape consumed by
    `Kontrollierer._spawn_worker_collect`.

    `cli` is declared as `"claude"` rather than a new literal so the
    Phase-6 telemetry surfaces (WorkerSpawned.cli, missions UI) don't
    need a schema migration to recognise this worker. The
    `name=` field on the synthetic ClaudeSystemInit carries
    `"gemini"` so debugging stays unambiguous.
    """

    cli: Literal["claude"] = "claude"

    def __init__(self) -> None:
        self.last_pid: int | None = None
        self.last_session_id: str | None = None

    async def spawn(
        self,
        prompt: str,
        *,
        worktree: Path,
        env: dict[str, str],
        job: Any,
        worker_id: str,
        log_dir: Path,
        model: str = "gemini-3-flash-preview",
        allowed_tools: str = "",  # accepted for parity, ignored by Gemini CLI
        permission_mode: str = "yolo",
        max_turns: int = 20,
        resume_session_id: str | None = None,
        extra_args: tuple[str, ...] = (),
        **_unused: Any,
    ) -> AsyncIterator[Any]:
        """Spawn the Gemini CLI and emit a synthetic init + result.

        Returns events shaped like ClaudeSystemInit / ClaudeResult so
        the Kontrollierer's collector logic stays generic. The diff is
        NOT extracted from stream events — `_capture_diff(worktree)`
        runs `git diff` after this generator exits.
        """
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_log = log_dir / "stream.jsonl"
        stderr_log = log_dir / "stderr.log"

        # The Phase-6 decomposer defaults `Step.model="sonnet"` — that's
        # a Claude alias and Gemini CLI would error out on it. Read the
        # actual Frontier model from
        # `jarvis.toml [brain.providers.gemini].deep_model` instead of
        # hardcoding Flash. The user pays for Pay-as-you-go quota and
        # explicitly wants top-tier output quality (see
        # feedback_frontier_models.md). When a newer Frontier model ships
        # (Gemini 4, etc.) the user updates the TOML and we pick it up
        # automatically — no code change needed.
        effective_model = _resolve_gemini_model(model)

        cmd = _build_gemini_cmd(
            prompt, model=effective_model,
            yolo=(permission_mode == "yolo"),
            extra_args=extra_args,
        )

        creationflags = _win32_creationflags()
        session_id = resume_session_id or str(uuid.uuid4())
        self.last_session_id = session_id

        # Yield the synthetic init event so WorkerSpawned fires upstream
        # with a usable session_id (the orchestrator only needs SOMETHING
        # non-empty to log; resume semantics aren't supported by Gemini
        # CLI's stateless --prompt mode anyway).
        yield ClaudeSystemInit(
            session_id=session_id,
            model=f"gemini/{effective_model}",
            tools=[],
            cwd=str(worktree),
        )

        logger.info(
            "GeminiWorker[%s] spawn: cwd=%s model=%s yolo=%s",
            worker_id, worktree, effective_model,
            permission_mode == "yolo",
        )

        async def _spawn_once(spawn_cmd: list[str]) -> tuple[bytes, bytes, int]:
            """Spawn the Gemini CLI once and return (stdout, stderr, exit).

            Extracted so we can re-spawn with a fallback model when the
            primary returns 429/QUOTA_EXHAUSTED without duplicating the
            Job-Object assignment + log-tee logic in two places.
            """
            proc_local = await asyncio.create_subprocess_exec(
                *spawn_cmd, cwd=str(worktree), env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creationflags,
            )
            self.last_pid = proc_local.pid
            try:
                job.assign(proc_local.pid)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "GeminiWorker[%s]: job.assign(pid=%d) failed",
                    worker_id, proc_local.pid, exc_info=True,
                )
            # 1200 s (20 min) hard cap — raised from 600 s so complex tasks can
            # finish (user mandate 2026-06-09). The "timeout" substring in the
            # stderr below still flags is_timeout to the orchestrator, which then
            # grades the on-disk diff instead of discarding it.
            try:
                out, err = await asyncio.wait_for(
                    proc_local.communicate(), timeout=1200.0
                )
            except asyncio.TimeoutError:
                with suppress(ProcessLookupError):
                    proc_local.kill()
                # Audit-2 H3 (2026-05-17): always wait() after kill() so the
                # asyncio transport tears down and the Win32 handle drops.
                with suppress(Exception):
                    await asyncio.wait_for(proc_local.wait(), timeout=5.0)
                out, err = b"", b"GeminiWorker: subprocess wait_for timeout (1200s)"
            exit_local = (
                proc_local.returncode if proc_local.returncode is not None else -1
            )
            return out, err, exit_local

        t0 = time.perf_counter()
        stdout_bytes, stderr_bytes, exit_code = await _spawn_once(cmd)

        # Pro→Flash fallback. Google AI Studio enforces a rolling 6h
        # quota on Pro-Preview models that is account-scoped (not
        # project-scoped); when it triggers, the CLI returns
        # `429 QUOTA_EXHAUSTED` on Pro while Flash keeps responding on
        # the same key. Rather than fail the mission, retry once on
        # Flash. Frontier-Routing snaps back automatically as soon as
        # Pro quota refills, since `effective_model` is read from
        # config on every spawn.
        if (
            exit_code != 0
            and effective_model != _FALLBACK_MODEL
            and _stderr_signals_quota_block(stderr_bytes)
        ):
            logger.warning(
                "GeminiWorker[%s]: %s returned 429/quota — retrying on %s",
                worker_id, effective_model, _FALLBACK_MODEL,
            )
            fallback_cmd = _build_gemini_cmd(
                prompt, model=_FALLBACK_MODEL,
                yolo=(permission_mode == "yolo"),
                extra_args=extra_args,
            )
            effective_model = _FALLBACK_MODEL
            stdout_bytes, stderr_bytes, exit_code = await _spawn_once(fallback_cmd)

        # Tee both streams to disk for post-mortem.
        with suppress(OSError):
            stdout_log.write_bytes(stdout_bytes)
        with suppress(OSError):
            stderr_log.write_bytes(stderr_bytes)

        wall_ms = int((time.perf_counter() - t0) * 1000)
        is_error = exit_code != 0

        # Cost + tokens aren't reported by Gemini CLI in text mode. Leave
        # them at None; the orchestrator falls back to 0 via the
        # `tokens_used → total_tokens → num_turns` chain (also None here).
        # Stamp the assistant's textual reply into `result` so the Critic
        # can read it as part of the log summary if needed.
        result_text = stdout_bytes.decode("utf-8", errors="replace")
        if is_error and stderr_bytes:
            tail = stderr_bytes.decode("utf-8", errors="replace")[-300:]
            result_text = (
                (result_text or "")
                + f"\n[stderr-tail]\n{tail}"
            )

        yield ClaudeResult(
            subtype="success" if not is_error else "error_during_execution",
            is_error=is_error,
            cost_usd=None,
            num_turns=None,
            session_id=session_id,
            duration_ms=wall_ms,
            result=result_text[:4000],  # cap to keep DB payload manageable
        )
