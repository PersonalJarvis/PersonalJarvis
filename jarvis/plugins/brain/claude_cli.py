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
import re
import shutil
import subprocess
import sys
import tempfile
import threading
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

# What a STRUCTURED print-mode turn switches off, and why it may. A structured
# caller hands over the complete material in the payload and wants prose back;
# the turn never legitimately uses a tool, an MCP server, a skill, or the saved
# session. Yet a bare ``claude -p`` starts all of it: it connects every
# user-configured MCP server, syncs plugins and skills, and persists the session
# to disk — pure cold-start cost on a call whose whole output is one brief.
# Measured on the maintainer's box 2026-08-12 with the composer's real payload:
# 27.5 s bare against 16.4 s with this set, identical brief quality (the lean
# run also stopped wrapping the brief in a code fence, which the composer
# otherwise has to peel off).
#
# Each entry is gated on the probed flag set below, because a downloader's CLI
# may be older than these flags — an unknown option makes the CLI exit with an
# error instead of an answer, which would turn a speed-up into a broken writer
# (§3: any CLI version keeps working).
#
# The probe below is a SYNTAX check, not a semantics one: ``--tools ""`` also
# assumes the empty value stays the documented "disable all" spelling. If a
# future CLI keeps the flag but stops accepting "", the turn ends as a usage
# error on stdout — which the empty-answer path already reports and the
# composer's rescue chain absorbs, so the worst case is one wasted rung.
_FAST_STRUCTURED_ARGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("--tools", ("--tools", "")),  # no built-in tools at all — writing only
    ("--strict-mcp-config", ("--strict-mcp-config",)),  # no MCP servers
    ("--disable-slash-commands", ("--disable-slash-commands",)),  # no skills
    ("--no-session-persistence", ("--no-session-persistence",)),  # no disk save
)

# ``BrainRequest.reasoning_effort`` values the CLI's ``--effort`` accepts
# verbatim. "none" is deliberately absent: the CLI has no such level, and the
# composer's documented floor is a modest effort, never zero.
_EFFORT_LEVELS: frozenset[str] = frozenset({"low", "medium", "high"})

# The probed set of long options this install's CLI understands, filled once
# per process from ``claude --help``. ``None`` means "not probed yet"; an empty
# set means the probe failed and every gated flag stays off — slower, but every
# turn still works.
_supported_flags: frozenset[str] | None = None
_probe_lock = threading.Lock()

_HELP_PROBE_TIMEOUT_S = 20.0
_FLAG_RE = re.compile(r"--[a-z][a-z0-9-]*")


def _probe_supported_flags() -> frozenset[str]:
    """The long options ``claude --help`` advertises, probed once per process.

    Ground truth over guesswork: gating on a version number means maintaining a
    private map of which release introduced which flag, and getting one entry
    wrong turns every composition into a CLI usage error. The help text is the
    CLI's own statement of what it accepts.

    Never raises. A missing binary, a hung probe, or unparseable output all
    degrade to the empty set — the invocation then simply stays as lean as the
    oldest supported CLI.
    """
    global _supported_flags
    with _probe_lock:
        if _supported_flags is not None:
            return _supported_flags
        flags: frozenset[str] = frozenset()
        binary = _resolve_claude_binary()
        if binary is not None:
            try:
                creationflags = NO_WINDOW_CREATIONFLAGS if sys.platform == "win32" else 0
                result = subprocess.run(  # noqa: S603 - fixed argv, no user input
                    [binary, "--help"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=_HELP_PROBE_TIMEOUT_S,
                    creationflags=creationflags,
                )
                flags = frozenset(_FLAG_RE.findall(result.stdout or ""))
            except Exception:  # noqa: BLE001 - a failed probe costs speed, not the turn
                log.debug("claude-cli: --help probe failed", exc_info=True)
        _supported_flags = flags
        return flags


def reset_flag_probe_cache() -> None:
    """Forget the probed flag set (tests; a CLI upgrade mid-process)."""
    global _supported_flags
    with _probe_lock:
        _supported_flags = None


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
            getattr(status, "connected", False) and getattr(status, "mode", "") == "subscription"
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
    # Structured mode hands the caller's contract to ``--system-prompt`` — a
    # channel the model treats as its instructions, not as user text to react
    # to. The subscription resolver prefers providers that can say True here:
    # a sibling that can only PREPEND the contract to the prompt is an
    # autonomous agent reading orders inside its input, and its observed
    # failure is an acknowledgement ("Understood! I see …") shipped as the
    # answer.
    native_system_prompt: bool = True

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

    def build_invocation(
        self, req: BrainRequest, cli_flags: frozenset[str] | None = None
    ) -> tuple[list[str], str]:
        """The argv and the stdin payload for one turn.

        Pure and public because this pair — not the subprocess plumbing — is
        what decides whether the answer comes back in the right shape, and that
        is the property worth testing directly.

        ``cli_flags`` is the probed set of long options this install's CLI
        understands (see ``_probe_supported_flags``). Only flags present there
        are added, so an older CLI never sees an option it would die on. ``None``
        reads as "unknown" and keeps the invocation at its lowest common shape.
        """
        binary = _resolve_claude_binary() or "claude"
        argv: list[str] = [binary, "-p", "--output-format", "text"]
        if self._model:
            argv += ["--model", self._model]
        argv += ["--disallowed-tools", *_DISALLOWED_TOOLS]

        if self._structured_prompts:
            known = cli_flags or frozenset()
            for flag, args in _FAST_STRUCTURED_ARGS:
                if flag in known:
                    argv += args
            # The caller's reasoning_effort is a QUALITY decision it already
            # made (the composer documents "medium" as its floor). The CLI's
            # own default may think longer without writing a better brief, so
            # the choice is forwarded rather than dropped.
            effort = str(getattr(req, "reasoning_effort", "") or "").strip()
            if "--effort" in known and effort in _EFFORT_LEVELS:
                argv += ["--effort", effort]
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

        # One help spawn per process lifetime: the first turn pays ~2-3 s for
        # the probe, every later turn reads the cached set.
        cli_flags = (
            _supported_flags
            if _supported_flags is not None
            else await asyncio.to_thread(_probe_supported_flags)
        )
        argv, prompt = self.build_invocation(req, cli_flags=cli_flags)
        # A throwaway working directory: this brain answers questions and has no
        # business seeing, or being trusted in, the user's repository.
        workdir = tempfile.mkdtemp(prefix="jarvis-claude-brain-")
        creationflags = NO_WINDOW_CREATIONFLAGS if sys.platform == "win32" else 0

        log.info(
            "claude-cli: spawning print-mode turn (model=%s, structured=%s, prompt=%d chars)",
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
            log.warning("claude-cli: no answer within %.0fs (killed)", self.cli_timeout_s)
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
            raise RuntimeError("Claude CLI returned no answer" + (f": {detail}" if detail else "."))

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


__all__ = ["ClaudeCliBrain", "reset_flag_probe_cache"]
