"""GoogleCliWorker — drive the official Antigravity ``agy`` CLI as a Phase-6 worker.

Bills mission work against the user's Google subscription (OAuth, no API key).

Two backends, picked by the resolver:
- ``agy`` (the official successor): a TUI tool that emits **0 bytes over a plain
  pipe**, so it is driven over a PTY via :func:`run_cli_over_pty`, with agy's
  write-capable flag ``--dangerously-skip-permissions`` so it can create/edit
  files inside the per-mission git worktree. agy understands only ``--print`` /
  ``--model`` / ``--dangerously-skip-permissions`` — NOT the gemini-CLI flags
  (``--prompt``/``--yolo``/``--output-format``), which is why the GeminiWorker
  cannot drive it.
- the Gemini CLI (resolver fallback): writes clean output to a pipe, so we
  delegate to the proven :class:`GeminiWorker` unchanged.

agy is driven through an isolated, hook/mcp-free CLI home (:mod:`jarvis.google_cli.
isolated_home`) so it does not boot the user's per-turn PowerShell hooks + npm MCP
servers, and with the PATH repaired for its internal ``cmd.exe``/``npm`` spawns.

Isolation is unchanged: the worker runs in the caller's git worktree (``cwd``),
and the child PID is assigned to the per-mission Job Object via ``on_spawn`` so
the kernel reaps the tree on cancel/timeout/crash (Windows). Google ToS: only the
official binary is driven; the OAuth token is never read into our own client.
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
from typing import Any, Literal

from jarvis.google_cli.auth_service import _oauth_login_present
from jarvis.google_cli.isolated_home import (
    ensure_isolated_home,
    iso_home_root,
    real_gemini_dir,
    redirect_home_env,
)
from jarvis.google_cli.pty_runner import repair_agy_path, run_cli_over_pty
from jarvis.google_cli.resolver import resolve_google_cli

from .capabilities import WorkerCapabilityInventory
from .gemini_worker import GeminiWorker
from .stream_consumer import ClaudeResult, ClaudeSystemInit

logger = logging.getLogger(__name__)

# Dropped from the child so the subscription OAuth login wins and an accidental
# API key can never bill the wrong account / break the OAuth path.
_DROP_ENV: tuple[str, ...] = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_AISTUDIO_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
)

# 20 min hard cap — mirrors GeminiWorker's 1200 s (user mandate 2026-06-09).
_WORKER_TIMEOUT_S: float = 1200.0


class BrokerMcpConfigurationError(RuntimeError):
    """Raised when the isolated Google CLI cannot receive the broker config."""


def _build_agy_worker_argv(exe: str, prompt: str, worktree: Path) -> list[str]:
    """agy worker argv: one non-interactive prompt with auto-approved tools so it
    can write files in the worktree.

    ``--add-dir <worktree>`` makes agy treat the per-mission git worktree as its
    active workspace, so deliverables land there (and show up in the Critic's
    ``git diff HEAD``). Without it agy has no active workspace in ``--print`` mode
    and writes every file into its home-relative
    ``.gemini/antigravity-cli/brain/<session>/`` (or ``scratch/<project>/``) dir —
    the worktree then stays empty, the Critic sees an empty diff, and EVERY
    antigravity mission fails ``critic_loop_exhausted`` even though agy did the
    work (live forensic 2026-06-27: mission_019f07cb wrote
    ``…/brain/<session>/datenmenge_150_petabyte.md`` and was failed for an empty
    diff; agy itself reported "kein aktives Workspace-Verzeichnis geöffnet"  # i18n-allow (quotes agy's own literal CLI message)
    — English: "no active workspace directory open").

    ``--print-timeout`` is widened from agy's 5-minute default to the worker's own
    time budget so a long "production-quality" task is not cut short by agy before
    our :data:`_WORKER_TIMEOUT_S` cap.

    Newlines are collapsed to spaces — agy takes the whole prompt as a single
    ``--print`` argument.
    """
    safe_prompt = " ".join(prompt.split())
    return [
        exe,
        "--print",
        safe_prompt,
        "--add-dir",
        str(worktree),
        "--print-timeout",
        f"{int(_WORKER_TIMEOUT_S)}s",
        "--dangerously-skip-permissions",
    ]


def _build_agy_worker_env(
    base_env: dict[str, str], *, mcp_servers: dict[str, Any] | None = None
) -> dict[str, str]:
    """Worker child env for agy: drop API keys (OAuth wins), repair PATH for agy's
    internal cmd/npm spawns, and redirect HOME to the isolated hook/mcp-free home."""
    env = {k: v for k, v in base_env.items() if k not in _DROP_ENV}
    node = shutil.which("node") or shutil.which("node.exe")
    env["PATH"] = repair_agy_path(
        env.get("PATH", ""), node_dir=os.path.dirname(node) if node else None
    )
    iso = ensure_isolated_home(
        real_dir=real_gemini_dir(), dest_root=iso_home_root(), model="gemini-3.5-flash"
    )
    if iso:
        redirect_home_env(env, iso)
    if mcp_servers:
        if not iso:
            raise BrokerMcpConfigurationError(
                "the isolated Google CLI home is unavailable"
            )
        settings_path = Path(iso) / ".gemini" / "settings.json"
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            if not isinstance(settings, dict):
                raise TypeError("Google CLI settings must contain an object")
            settings["mcpServers"] = dict(mcp_servers)
            settings_path.write_text(json.dumps(settings), encoding="utf-8")
        except (OSError, ValueError, TypeError) as exc:
            raise BrokerMcpConfigurationError(
                "broker MCP settings could not be written in the isolated Google CLI home"
            ) from exc
    return env


class GoogleCliWorker:
    """Phase-6 worker driving the official Google CLI (agy over PTY, gemini over pipe).

    ``cli`` is declared ``"claude"`` (like GeminiWorker) so the Phase-6 telemetry
    schema needs no migration; the synthetic init event's ``model`` field carries
    ``antigravity/agy`` so debugging stays unambiguous.
    """

    cli: Literal["claude"] = "claude"

    def __init__(
        self,
        *,
        capability_inventory: WorkerCapabilityInventory | None = None,
    ) -> None:
        self.last_pid: int | None = None
        self.last_session_id: str | None = None
        self.capability_inventory = capability_inventory or WorkerCapabilityInventory.build()
        self._gemini_fallback = GeminiWorker(capability_inventory=self.capability_inventory)

    async def spawn(
        self,
        prompt: str,
        *,
        worktree: Path,
        env: dict[str, str],
        job: Any,
        worker_id: str,
        log_dir: Path,
        model: str = "gemini-3.5-flash",
        allowed_tools: str = "",  # parity, ignored by agy
        permission_mode: str = "yolo",
        max_turns: int = 20,
        resume_session_id: str | None = None,
        extra_args: tuple[str, ...] = (),
        _broker_binding: Any | None = None,
        **_unused: Any,
    ) -> AsyncIterator[Any]:
        """Run one Google worker lifecycle under one mission-scoped grant."""
        broker_binding = _broker_binding
        issued_here = broker_binding is None
        if issued_here:
            broker_binding = self.capability_inventory.bind_broker(
                ttl_s=_WORKER_TIMEOUT_S + 60.0,
                mission_id=_unused.get("mission_id"),
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
                broker_binding=broker_binding,
                **_unused,
            ):
                yield event
        finally:
            if issued_here and broker_binding is not None:
                try:
                    broker_binding.close()
                except Exception:  # noqa: BLE001 - cleanup must not mask cancellation
                    logger.exception("GoogleCliWorker: broker binding cleanup failed")

    async def _spawn_bound(
        self,
        prompt: str,
        *,
        worktree: Path,
        env: dict[str, str],
        job: Any,
        worker_id: str,
        log_dir: Path,
        model: str = "gemini-3.5-flash",
        allowed_tools: str = "",  # parity, ignored by agy
        permission_mode: str = "yolo",
        max_turns: int = 20,
        resume_session_id: str | None = None,
        extra_args: tuple[str, ...] = (),
        broker_binding: Any | None,
        **_unused: Any,
    ) -> AsyncIterator[Any]:
        cli = resolve_google_cli()
        session_id = resume_session_id or str(uuid.uuid4())
        self.last_session_id = session_id

        # API billing needs no Google CLI. Resolve it before the binary guard so
        # a headless/API-key-only installation can use the in-process-compatible
        # Gemini worker instead of failing on an absent ``agy`` executable.
        use_api_billing = not _oauth_login_present(Path(real_gemini_dir())) and bool(
            env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY")
        )

        if cli is None and not use_api_billing:
            yield ClaudeSystemInit(
                session_id=session_id,
                model="antigravity/none",
                tools=[],
                cwd=str(worktree),
                external_capabilities=self.capability_inventory.report_for("google-cli"),
            )
            yield ClaudeResult(
                subtype="error_during_execution",
                is_error=True,
                cost_usd=None,
                num_turns=None,
                session_id=session_id,
                duration_ms=0,
                result="No Google CLI found — install Antigravity (agy) or the Gemini CLI.",
            )
            return

        # Billing path (Antigravity dual billing, mirror of Codex): agy runs over
        # the Google subscription OAuth login. If there is NO OAuth login but a
        # Gemini API key is available, bill per token via the proven Gemini API
        # worker instead — same outcome the user asked for ("bill via the
        # API"), on the tested path rather than coercing agy to use a key.
        if use_api_billing:
            from .api_agent_worker import ApiAgentWorker

            logger.info(
                "GoogleCliWorker[%s] -> ApiAgentWorker(gemini) (API-key billing)",
                worker_id,
            )
            api_worker = ApiAgentWorker(
                "gemini", capability_inventory=self.capability_inventory
            )
            async for ev in api_worker.spawn(
                prompt,
                worktree=worktree,
                env=env,
                job=job,
                worker_id=worker_id,
                log_dir=log_dir,
                model=model,
                max_turns=max_turns,
                resume_session_id=resume_session_id,
                _broker_binding=broker_binding,
                **_unused,
            ):
                yield ev
            self.last_pid = api_worker.last_pid
            self.last_session_id = api_worker.last_session_id
            return

        if cli is not None and cli.kind != "agy":
            logger.info(
                "GoogleCliWorker[%s] -> GeminiWorker (resolver kind=%s)",
                worker_id,
                cli.kind,
            )
            async for ev in self._gemini_fallback.spawn(
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
                _broker_binding=broker_binding,
                **_unused,
            ):
                yield ev
            self.last_pid = self._gemini_fallback.last_pid
            self.last_session_id = self._gemini_fallback.last_session_id
            return

        # agy path: PTY + write-mode + isolated hook/mcp-free home.
        exe = cli.argv_prefix[0]
        log_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 — trivial sync mkdir (mirrors GeminiWorker)
        argv = _build_agy_worker_argv(exe, prompt, worktree)
        broker_servers = (
            broker_binding.mcp_server_config() if broker_binding is not None else {}
        )
        agy_base_env = (
            broker_binding.apply_environment(env)
            if broker_binding is not None
            else env
        )
        try:
            agy_env = _build_agy_worker_env(agy_base_env, mcp_servers=broker_servers)
        except BrokerMcpConfigurationError as exc:
            logger.error("GoogleCliWorker[%s]: %s", worker_id, exc)
            yield ClaudeSystemInit(
                session_id=session_id,
                model="antigravity/agy",
                tools=[],
                cwd=str(worktree),
                external_capabilities=self.capability_inventory.report_for(
                    "google-cli", binding=None
                ),
            )
            yield ClaudeResult(
                subtype="error_during_execution",
                is_error=True,
                cost_usd=None,
                num_turns=None,
                session_id=session_id,
                duration_ms=0,
                result=f"GoogleCliWorker broker MCP configuration failed: {exc}",
            )
            return

        yield ClaudeSystemInit(
            session_id=session_id,
            model="antigravity/agy",
            tools=[],
            cwd=str(worktree),
            external_capabilities=self.capability_inventory.report_for(
                "google-cli", binding=broker_binding
            ),
        )
        logger.info(
            "GoogleCliWorker[%s] spawn agy over PTY: cwd=%s (Google subscription, OAuth)",
            worker_id,
            worktree,
        )

        loop = asyncio.get_running_loop()

        def _assign(pid: int) -> None:
            self.last_pid = pid
            try:
                job.assign(pid)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "GoogleCliWorker[%s]: job.assign(pid=%s) failed",
                    worker_id,
                    pid,
                    exc_info=True,
                )

        t0 = time.perf_counter()
        result = await loop.run_in_executor(
            None,
            lambda: run_cli_over_pty(
                tuple(argv),
                timeout_s=_WORKER_TIMEOUT_S,
                cwd=str(worktree),
                env=agy_env,
                on_spawn=_assign,
            ),
        )
        wall_ms = int((time.perf_counter() - t0) * 1000)

        with suppress(OSError):
            (log_dir / "stream.jsonl").write_text(result.text, encoding="utf-8")
        with suppress(OSError):
            (log_dir / "stderr.log").write_text(result.raw[:20000], encoding="utf-8")

        is_error = bool(result.error) or result.timed_out or result.exit_status not in (0, None)
        text = result.text or (result.error or "")
        if result.timed_out:
            text = f"{text}\n[timeout after {_WORKER_TIMEOUT_S:.0f}s]".strip()
        logger.info(
            "GoogleCliWorker[%s] agy done: error=%s %dms text=%d chars",
            worker_id,
            is_error,
            wall_ms,
            len(result.text),
        )
        yield ClaudeResult(
            subtype="success" if not is_error else "error_during_execution",
            is_error=is_error,
            cost_usd=None,
            num_turns=None,
            session_id=session_id,
            duration_ms=wall_ms,
            result=text[:4000],
        )
