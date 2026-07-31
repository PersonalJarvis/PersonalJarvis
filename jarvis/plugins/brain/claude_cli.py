"""Anthropic subscription brain — drives the ``claude`` CLI in print mode.

Jarvis already spends two subscriptions this way: ChatGPT through ``codex exec``
and Google through the Gemini CLI. Anthropic was the gap. The only Anthropic
brain was ``claude-api``, billed per token, so a user whose sole Anthropic
credential is a Claude plan could not spend it on brain work at all — and the
Agentic IDE's prompt composer, which needs a capable model or nothing, fell back
to its deterministic regex prompt on every single instruction for them.

This is deliberately NOT a voice-tier brain. Measured 2026-07-26 on a warm
machine: 10-12 s for a trivial prompt (essentially all process start-up) and
26.6 s for a real structured brief on the fastest model. Either number would
destroy a spoken turn. It exists for callers that are already waiting seconds
and whose OUTPUT is the product rather than a step towards it — and any caller
wiring this up needs a deadline well clear of 30 s, not the 45 s that sufficed
when only API providers wrote briefs.

Two shapes, one provider, chosen by the caller through ``structured_prompts``:

* **Conversational** (default) — a light "answer in a sentence or three" wrapper
  with the recent turns, mirroring the Codex sibling. The heavy router system
  prompt is dropped on purpose: it is large, tool-heavy, and misleading for a
  read-only CLI call.
* **Structured** — the caller's ``system`` IS the contract, so it rides in
  ``--system-prompt`` and the payload goes to stdin untouched. Claude Code takes
  a real system prompt, which makes this cleaner here than in the siblings that
  have to flatten everything into one blob.

Getting that choice wrong is the failure mode worth guarding: a structured
caller served conversationally gets three fluent sentences that read like a
valid result. Nobody inspects an answer that looks fine.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import sys
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import suppress

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.core.protocols import BrainDelta, BrainRequest

from .cli_prompt_context import (
    extract_reply_language_directive,
    render_cli_standing_instructions,
    render_structured_prompt,
)

log = logging.getLogger(__name__)

# Hard cap for a single turn. Higher than the Codex sibling's 90 s because this
# provider's whole reason to exist is long-form work a caller is waiting on;
# callers with a tighter budget pass their own ``cli_timeout_s``.
_CLI_TIMEOUT_S: float = 120.0

# How often the run loop yields an empty delta while waiting. The caller's
# no-progress watchdog resets on any delta, so a silent 20 s await would look
# like a stalled provider and lose the turn to a fallback (the Codex sibling hit
# exactly that).
_TICK_S: float = 3.0

# Binary name with Windows shim variants. ``shutil.which`` honours PATHEXT, so
# the bare name usually resolves; the variants cover installs where only the
# ``.cmd`` shim is on PATH.
_BINARY_CANDIDATES: tuple[str, ...] = ("claude", "claude.cmd", "claude.exe")

# Tools refused on this path. The brain answers in text; it must not be able to
# touch the filesystem or run commands even if a prompt talks it into trying.
# Read-only tools are deliberately left available: a caller may legitimately ask
# the model to look something up, and forbidding those buys nothing.
_DISALLOWED_TOOLS: tuple[str, ...] = ("Bash", "Edit", "Write", "NotebookEdit")

_CLI_SYSTEM = (
    "You are Jarvis, a concise and friendly voice assistant. Answer the user's "
    "message directly in one to three short sentences. Reply in plain text only "
    "— do not run any commands, do not read or edit files, do not use tools."
)

# How many recent turns ride along on the conversational path. Every token is
# slow here, so older history is dropped rather than paid for.
_CONVO_TURNS = 6


def _resolve_claude_binary() -> str | None:
    """On-PATH ``claude`` binary (Windows shim variants included), or None."""
    for name in _BINARY_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    return None


def _claude_subscription_connected() -> bool:
    """True when a Claude subscription login is present. Never raises.

    Best-effort by design: this runs for every candidate the resolver considers,
    and a probe that raised would kill a turn that should merely have skipped one
    provider. A missing CLI, an unreadable credential store, or a host with no
    keychain all degrade to False.
    """
    try:
        from jarvis.claude_auth import ClaudeAuthService

        status = ClaudeAuthService().status()
        return bool(
            getattr(status, "connected", False)
            and getattr(status, "mode", "") == "subscription"
        )
    except Exception:  # noqa: BLE001 - a probe degrades, never raises
        log.debug("claude-cli: subscription probe failed", exc_info=True)
        return False


class ClaudeCliBrain:
    """Brain backed by the Claude CLI running on a subscription login."""

    name: str = "claude-cli"
    context_window: int = 200_000
    # Static capability flags stay at the honest value for THIS path: the print
    # -mode CLI hands us text, never tool calls, and never sees an image. A
    # hopeful True here would have BrainManager route a tool turn or a
    # Computer-Use screenshot to a brain that cannot serve it (AP-21).
    supports_tools: bool = False
    supports_vision: bool = False

    def __init__(
        self,
        model: str | None = None,
        structured_prompts: bool = False,
        cli_timeout_s: float | None = None,
    ) -> None:
        # No model default: an unset model means "the CLI's own default for this
        # account's plan". Hardcoding an id here would break every user whose
        # plan does not carry it (AP-21).
        self._model = (model or "").strip() or None
        self._structured_prompts = bool(structured_prompts)
        try:
            budget = float(cli_timeout_s) if cli_timeout_s is not None else 0.0
        except (TypeError, ValueError):
            budget = 0.0
        self.cli_timeout_s = budget if budget > 0 else _CLI_TIMEOUT_S

    # ---- capabilities -------------------------------------------------

    def can_call_tools(self) -> bool:
        """Runtime tool-calling capability — always False on this path.

        The caller (``BrainManager``) uses this to delegate a tool turn to a
        genuinely tool-capable provider instead of letting a text-only brain
        confabulate a refusal that reads like "the model chose no tool".
        """
        return False

    @staticmethod
    def subscription_connected() -> bool:
        """Whether this provider's subscription login is usable right now.

        The resolver asks the provider class rather than importing an auth
        service per family, so a subscription brain added later answers for
        itself with no resolver edit.
        """
        return _claude_subscription_connected()

    # ---- invocation ----------------------------------------------------

    def build_invocation(self, req: BrainRequest) -> tuple[list[str], str]:
        """The argv and the stdin payload for one turn.

        Pure and public because this pair — not the subprocess plumbing — is
        what decides whether the answer comes back in the right shape, and that
        is the property worth testing directly.
        """
        binary = _resolve_claude_binary() or "claude"
        argv: list[str] = [binary, "-p", "--output-format", "text"]
        if self._model:
            argv += ["--model", self._model]
        argv += ["--disallowed-tools", *_DISALLOWED_TOOLS]

        if self._structured_prompts:
            system = str(getattr(req, "system", "") or "").strip()
            if system:
                argv += ["--system-prompt", system]
                # The contract already rides in argv; sending it on stdin too
                # would duplicate it and dilute the payload it governs.
                payload = "\n\n".join(
                    str(message.content).strip()
                    for message in (req.messages or ())
                    if getattr(message, "role", "") in ("system", "user")
                    and isinstance(getattr(message, "content", None), str)
                    and str(message.content).strip()
                )
                return argv, payload or render_structured_prompt(req)
            return argv, render_structured_prompt(req)

        lines: list[str] = [_CLI_SYSTEM, ""]
        prefs = render_cli_standing_instructions(req.system)
        convo = [
            message
            for message in req.messages
            if getattr(message, "role", None) in ("user", "assistant")
            and isinstance(getattr(message, "content", None), str)
        ][-_CONVO_TURNS:]
        for message in convo:
            speaker = "User" if message.role == "user" else "Assistant"
            lines.append(f"{speaker}: {message.content}")
        if prefs:
            lines.extend(["", prefs])
        # The reply-language directive rides LAST (highest recency) so the CLI
        # model answers in the turn's resolved language instead of anchoring to
        # the persona's own language — the sibling bug of 2026-06-21.
        directive = extract_reply_language_directive(req.system)
        if directive:
            lines.extend(["", directive])
        lines.append("Assistant:")
        return argv, "\n".join(lines)

    # ---- run loop ------------------------------------------------------

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        """Run one CLI turn on the subscription and yield its answer."""
        if _resolve_claude_binary() is None:
            raise RuntimeError(
                "Claude CLI not found — install it from https://claude.ai/download "
                "and run 'claude' once to sign in."
            )

        argv, prompt = self.build_invocation(req)
        # A throwaway working directory: this brain answers questions and has no
        # business seeing, or being trusted in, the user's repository.
        workdir = tempfile.mkdtemp(prefix="jarvis-claude-brain-")
        creationflags = NO_WINDOW_CREATIONFLAGS if sys.platform == "win32" else 0

        log.info(
            "claude-cli: spawning print-mode turn (model=%s, structured=%s, "
            "prompt=%d chars)",
            self._model or "<cli default>",
            self._structured_prompts,
            len(prompt),
        )

        t0 = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=workdir,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creationflags,
            )
        except (FileNotFoundError, OSError) as exc:
            shutil.rmtree(workdir, ignore_errors=True)
            log.warning("claude-cli: spawn failed: %s", exc)
            raise RuntimeError(f"Claude CLI could not be launched: {exc}") from exc

        try:
            assert proc.stdin is not None  # noqa: S101 - PIPE is always present
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            # The CLI died before reading; the empty-answer path below reports it
            # with whatever stderr explains, which beats a bare pipe error.
            pass

        comm_task = asyncio.create_task(proc.communicate())
        deadline = t0 + self.cli_timeout_s
        stdout_bytes = b""
        stderr_bytes = b""

        async def _kill() -> None:
            if not comm_task.done():
                comm_task.cancel()
            pid = getattr(proc, "pid", None)
            if sys.platform == "win32" and isinstance(pid, int) and pid > 0:
                # The Node shim spawns children; killing only the shim leaves
                # them running and holding the pipes open.
                with suppress(Exception):  # noqa: BLE001
                    killer = await asyncio.create_subprocess_exec(
                        "taskkill",
                        "/PID",
                        str(pid),
                        "/T",
                        "/F",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                        creationflags=creationflags,
                    )
                    await asyncio.wait_for(killer.wait(), timeout=3.0)
            with suppress(OSError):
                proc.kill()
            with suppress(Exception):  # noqa: BLE001
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            with suppress(asyncio.CancelledError, Exception):
                await comm_task

        try:
            while True:
                slice_timeout = min(_TICK_S, deadline - time.monotonic())
                if slice_timeout <= 0:
                    raise TimeoutError
                done, _pending = await asyncio.wait({comm_task}, timeout=slice_timeout)
                if done:
                    stdout_bytes, stderr_bytes = comm_task.result()
                    break
                # Empty progress tick: nothing visible, but it keeps the caller's
                # no-progress deadline alive through the cold start.
                yield BrainDelta(content="")
        except asyncio.CancelledError:
            await _kill()
            log.info("claude-cli: cancelled (killed)")
            raise
        except TimeoutError as exc:
            await _kill()
            log.warning(
                "claude-cli: no answer within %.0fs (killed)", self.cli_timeout_s
            )
            raise RuntimeError(
                f"Claude CLI did not answer within {self.cli_timeout_s:.0f}s."
            ) from exc
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        answer = stdout_bytes.decode("utf-8", errors="replace").strip()
        elapsed = time.monotonic() - t0
        if not answer:
            detail = stderr_bytes.decode("utf-8", errors="replace").strip()[:300]
            log.warning(
                "claude-cli: empty answer after %.1fs rc=%s detail=%s",
                elapsed,
                proc.returncode,
                detail[:200],
            )
            raise RuntimeError(
                "Claude CLI returned no answer" + (f": {detail}" if detail else ".")
            )

        log.info(
            "claude-cli turn ok: %d chars in %.1fs on the subscription",
            len(answer),
            elapsed,
        )
        yield BrainDelta(content=answer)
        yield BrainDelta(finish_reason="stop")

    def estimate_cost(self, req: BrainRequest) -> float:
        """Zero: this path bills against a subscription, not per token.

        Reporting a per-token estimate here would make cost-aware callers avoid
        the one provider that costs them nothing extra.
        """
        return 0.0


__all__ = ["ClaudeCliBrain"]
