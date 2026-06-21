"""Antigravity Brain — answer over the user's Google subscription, no API key.

This provider drives the **official** Google agent CLI (Antigravity ``agy`` or
the Gemini CLI, resolved by :func:`jarvis.google_cli.resolver.resolve_google_cli`)
as a subprocess over the existing "Sign in with Google" login. It is the Google
sibling of :class:`jarvis.plugins.brain.codex.CodexBrain` (which does the same
over the ChatGPT subscription via ``codex exec``).

OAuth-only: there is no API-key path here — that is what the existing ``gemini``
brain provider is for. The conversational brain runs the CLI in read-only
``--approval-mode plan`` so it cannot write files or run commands.

Google ToS (hard): we only ever invoke the official binary. We never read the
stored OAuth token to make our own HTTP request. The CLI is slow (the agent
spins up per turn), so this is a deliberate, user-opted path — not the default.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import suppress

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.core.protocols import BrainDelta, BrainRequest
from jarvis.google_cli.resolver import GoogleCli, resolve_google_cli

log = logging.getLogger(__name__)

# Fallback only — the active model comes from [brain.providers.antigravity].model.
# Matches the user's ~/.gemini/settings.json default; overridable, no code change.
DEFAULT_MODEL = "gemini-3.1-pro-preview"

# Hard cap for a single CLI brain turn. The agent CLI is slow (cold start +
# 20k-token system prompt); 120 s leaves headroom without hanging the brain
# coroutine forever if the subscription is unreachable.
_CLI_TIMEOUT_S: float = 120.0

# Env keys dropped from the child so the subscription login wins and an
# accidental API key can never bill the wrong account / break the OAuth path.
_DROP_ENV: tuple[str, ...] = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_AISTUDIO_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
)

_CLI_SYSTEM = (
    "You are Jarvis, a concise and friendly voice assistant. Answer the user's "
    "message directly in one to three short sentences. Reply in plain text only "
    "— do not run any commands, do not read or edit files, do not use tools."
)


def _parse_cli_answer(stdout: str) -> str:
    """Extract the answer text from the CLI's stdout.

    The Gemini CLI ``-o json`` emits a JSON object with a ``response`` field;
    ``agy`` variants differ. We try JSON first (``response``/``text``/``output``/
    ``content``) and fall back to the raw, trimmed stdout so a plain-text or
    schema-changed output still yields an answer.
    """
    text = (stdout or "").strip()
    if not text:
        return ""
    try:
        obj = json.loads(text)
    except (ValueError, json.JSONDecodeError):
        return text
    if isinstance(obj, str):
        return obj.strip()
    if isinstance(obj, dict):
        for key in ("response", "text", "output", "content", "result"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return ""
    return text


def _build_cli_prompt(req: BrainRequest) -> str:
    """Flatten the last few conversational turns into one prompt for ``-p``.

    The heavy router system prompt (full of tool definitions) is dropped — it
    would make the agent CLI slow and confused. We send a light conversational
    instruction plus the last ~6 user/assistant turns for context.
    """
    lines: list[str] = [_CLI_SYSTEM, ""]
    convo = [
        m
        for m in req.messages
        if getattr(m, "role", None) in ("user", "assistant")
        and isinstance(getattr(m, "content", None), str)
    ][-6:]
    for m in convo:
        speaker = "User" if m.role == "user" else "Assistant"
        lines.append(f"{speaker}: {m.content}")
    lines.append("Assistant:")
    return "\n".join(lines)


def _build_argv(cli: GoogleCli, prompt: str, model: str) -> list[str]:
    """Build the headless argv for the resolved CLI — flags differ per binary.

    * ``agy`` (Antigravity CLI 1.0.9): ``--print <prompt> --model <id>``. It has
      neither ``--approval-mode`` nor ``-o json`` (live ``agy --help`` 2026-06-20);
      output is plain text, which ``_parse_cli_answer`` handles via its raw
      fallback. A read-only conversational answer needs no tool permissions.
    * ``gemini`` (Gemini CLI): read-only ``--approval-mode plan`` + ``--skip-trust``
      (so the throwaway workdir is trusted and the sandbox policy is not loaded —
      forensic 2026-06-20) + ``-o json``.
    """
    if cli.kind == "agy":
        return [*cli.argv_prefix, "--print", prompt, "--model", model]
    return [
        *cli.argv_prefix,
        "-p",
        prompt,
        "-m",
        model,
        "--approval-mode",
        "plan",
        "--skip-trust",
        "-o",
        "json",
    ]


class AntigravityBrain:
    name: str = "antigravity"
    context_window: int = 1_048_576
    supports_tools: bool = True  # ignored on the CLI path (mirrors CodexBrain)
    supports_vision: bool = False

    def __init__(self, model: str | None = None) -> None:
        self._model = model or DEFAULT_MODEL

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        cli = resolve_google_cli()
        if cli is None:
            raise RuntimeError(
                "No Google CLI found — install Antigravity (agy) or the Gemini "
                "CLI and sign in with Google."
            )

        prompt = _build_cli_prompt(req)
        argv = _build_argv(cli, prompt, self._model)
        workdir = tempfile.mkdtemp(prefix="jarvis-antigravity-brain-")
        env = {k: v for k, v in os.environ.items() if k not in _DROP_ENV}
        creationflags = NO_WINDOW_CREATIONFLAGS if sys.platform == "win32" else 0
        log.info(
            "AntigravityBrain: spawning %s (kind=%s, model=%s, prompt=%d chars)",
            cli.argv_prefix[0],
            cli.kind,
            self._model,
            len(prompt),
        )

        t0 = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=workdir,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creationflags,
            )
        except (FileNotFoundError, OSError) as exc:
            with suppress(OSError):
                shutil.rmtree(workdir, ignore_errors=True)
            log.warning("AntigravityBrain: spawn failed: %s", exc)
            raise RuntimeError(f"Google CLI could not be launched: {exc}") from exc

        # Close stdin empty — the whole prompt rides on ``-p``.
        with suppress(Exception):
            if proc.stdin is not None:
                proc.stdin.close()

        comm_task = asyncio.create_task(proc.communicate())
        deadline = t0 + _CLI_TIMEOUT_S
        stdout_bytes = b""
        stderr_bytes = b""

        async def _kill() -> None:
            if not comm_task.done():
                comm_task.cancel()
            pid = getattr(proc, "pid", None)
            if sys.platform == "win32" and isinstance(pid, int) and pid > 0:
                with suppress(Exception):
                    killer = await asyncio.create_subprocess_exec(
                        "taskkill", "/PID", str(pid), "/T", "/F",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                        creationflags=creationflags,
                    )
                    await asyncio.wait_for(killer.wait(), timeout=3.0)
            with suppress(Exception):
                proc.kill()
            with suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            with suppress(asyncio.CancelledError, Exception):
                await comm_task

        try:
            while True:
                slice_timeout = min(3.0, deadline - time.monotonic())
                if slice_timeout <= 0:
                    raise TimeoutError
                done, _ = await asyncio.wait({comm_task}, timeout=slice_timeout)
                if done:
                    stdout_bytes, stderr_bytes = comm_task.result()
                    break
                # No-text progress tick: keeps the caller's no-progress deadline
                # alive through the slow agent spin-up (yields nothing visible).
                yield BrainDelta(content="")
        except asyncio.CancelledError:
            await _kill()
            log.info("AntigravityBrain: cancelled (killed)")
            raise
        except TimeoutError as exc:
            await _kill()
            log.warning("AntigravityBrain: no answer within %.0fs (killed)", _CLI_TIMEOUT_S)
            raise RuntimeError(
                f"Antigravity (Google login) did not answer within {_CLI_TIMEOUT_S:.0f}s."
            ) from exc
        finally:
            with suppress(OSError):
                shutil.rmtree(workdir, ignore_errors=True)

        answer = _parse_cli_answer(stdout_bytes.decode("utf-8", errors="replace"))
        elapsed = time.monotonic() - t0
        if not answer:
            detail = stderr_bytes.decode("utf-8", errors="replace").strip()[:300]
            log.warning(
                "AntigravityBrain: empty answer after %.1fs rc=%s detail=%s",
                elapsed, proc.returncode, detail[:200],
            )
            raise RuntimeError(
                "Antigravity (Google login) returned no answer"
                + (f": {detail}" if detail else ".")
            )

        log.info("AntigravityBrain turn ok: %d chars in %.1fs", len(answer), elapsed)
        yield BrainDelta(content=answer)
        yield BrainDelta(finish_reason="stop")

    def estimate_cost(self, req: BrainRequest) -> float:
        # Billed against the Google subscription, not per-call — report ~0.
        return 0.0
