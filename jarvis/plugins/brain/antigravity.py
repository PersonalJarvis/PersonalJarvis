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
from jarvis.google_cli.isolated_home import (
    ensure_isolated_home as _ensure_isolated_home,
)
from jarvis.google_cli.isolated_home import (
    iso_home_root as _iso_home_root,
)
from jarvis.google_cli.isolated_home import (
    real_gemini_dir as _real_gemini_dir,
)
from jarvis.google_cli.isolated_home import redirect_home_env
from jarvis.google_cli.pty_runner import repair_agy_path
from jarvis.google_cli.resolver import GoogleCli, resolve_google_cli

from .cli_prompt_context import (
    extract_reply_language_directive,
    render_cli_standing_instructions,
    render_structured_prompt,
)

log = logging.getLogger(__name__)

# Fallback only — the active model comes from [brain.providers.antigravity].model.
# Flash is the fast default. NOTE (verified 2026-06-21, agy 1.0.10): agy ignores
# both --model and settings.json model.name and runs its own IDE-configured
# default, so this value only steers the gemini-CLI fallback path; for agy it is
# informational. Kept in sync with the curated catalog (model_catalog.py).
DEFAULT_MODEL = "gemini-3.5-flash"

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
    prefs = render_cli_standing_instructions(req.system)
    convo = [
        m
        for m in req.messages
        if getattr(m, "role", None) in ("user", "assistant")
        and isinstance(getattr(m, "content", None), str)
    ][-6:]
    for m in convo:
        speaker = "User" if m.role == "user" else "Assistant"
        lines.append(f"{speaker}: {m.content}")
    if prefs:
        lines.extend(["", prefs])
    # The reply-language directive rides LAST (highest recency) so the CLI model
    # answers in the turn's resolved language instead of anchoring to the German
    # persona — without this the directive is dropped and an English request is
    # answered in German (live bug 2026-06-21).
    lang_directive = extract_reply_language_directive(req.system)
    if lang_directive:
        lines.extend(["", lang_directive])
    lines.append("Assistant:")
    return "\n".join(lines)


# Reasoning effort sent alongside an explicit ``--model`` on the agy CLI. The
# binary REQUIRES the pair: passing a model without an effort aborts the run.
_DEFAULT_AGY_EFFORT = "medium"
# The levels agy accepts (``agy --help``: low|medium|high). A request asking to
# disable thinking maps to the lowest level agy offers, since it has no "off".
_AGY_EFFORTS = frozenset({"low", "medium", "high"})


def _agy_effort(req: BrainRequest | None) -> str:
    """Effort flag for an agy run, derived from the caller's own hint."""
    requested = str(getattr(req, "reasoning_effort", "") or "").strip().lower()
    if requested == "none":
        return "low"
    return requested if requested in _AGY_EFFORTS else _DEFAULT_AGY_EFFORT


def _build_argv(
    cli: GoogleCli, prompt: str, model: str, effort: str = _DEFAULT_AGY_EFFORT
) -> list[str]:
    """Build read-only JSON-output commands; the complete prompt travels on stdin.

    Antigravity print mode activates on piped input without ``--print``.
    Keeping input out of argv avoids both Windows and POSIX argument limits.
    """
    if cli.kind == "agy":
        return [
            *cli.argv_prefix,
            "--output-format",
            "json",
            "--mode",
            "plan",
            "--model",
            model,
            "--effort",
            effort,
        ]
    return [
        *cli.argv_prefix,
        "-p",
        "Follow the instructions supplied on stdin.",
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
    # Neither agy nor the Gemini CLI takes a real system prompt in headless
    # mode: structured mode PREPENDS the caller's contract to the user prompt
    # (render_structured_prompt), where the agent may read it as text to react
    # to rather than orders to follow. Live 2026-08-11: every Agentic IDE pane
    # title became a chat acknowledgement ("Understood! I see …") the moment
    # this provider wrote the recaps. The subscription resolver uses this flag
    # to prefer a sibling with a dedicated system channel when one is signed in.
    native_system_prompt: bool = False

    def __init__(
        self,
        model: str | None = None,
        structured_prompts: bool = False,
        cli_timeout_s: float | None = None,
    ) -> None:
        self._model = model or DEFAULT_MODEL
        # Background/structured callers (the wiki curator tier) set this so
        # their JSON contract reaches the CLI model verbatim instead of the
        # conversational "answer in 1-3 plain-text sentences" wrapper — which
        # made structured output impossible by instruction (live 2026-07-18:
        # every wiki extraction died with "no JSON array found in response").
        self._structured_prompts = bool(structured_prompts)
        # Slow background callers (wiki Stage-2 judge) pass their own per-call
        # budget so the internal cap cannot kill a turn the caller is still
        # willing to wait for; the voice-tier default stays the tight cap.
        try:
            budget = float(cli_timeout_s) if cli_timeout_s is not None else 0.0
        except (TypeError, ValueError):
            budget = 0.0
        self._cli_timeout_s = budget if budget > 0 else _CLI_TIMEOUT_S

    @staticmethod
    def subscription_connected() -> bool:
        """Whether this provider's Google subscription login is usable now.

        The subscription resolver asks the provider class rather than importing
        an auth service per family, so each brain answers for itself. Only a
        personal OAuth login counts: an API key configured for this family bills
        per token, which is precisely what a subscription caller is avoiding.
        Never raises — a failed probe means "not connected", never a broken turn.
        """
        try:
            from jarvis.google_cli.auth_service import GoogleCliAuthService

            status = GoogleCliAuthService().status()
            return bool(
                getattr(status, "connected", False)
                and getattr(status, "mode", "") == "oauth-personal"
            )
        except Exception:  # noqa: BLE001 - a probe degrades, never raises
            return False

    def can_call_tools(self) -> bool:
        """Runtime tool-calling capability (NOT the static ``supports_tools``).

        Antigravity drives the Google-subscription CLI (``agy`` or Gemini) over
        pipes with a flattened prompt — it has no
        chat-completions/function-calling path and drops every tool. The caller
        (``BrainManager``) uses this to delegate tool/Computer-Use turns to a
        tool-capable provider instead of letting the CLI silently no-op."""
        return False

    def _render_prompt(self, req: BrainRequest) -> str:
        """Conversational flattening for voice turns; verbatim for structured."""
        if self._structured_prompts:
            return render_structured_prompt(req)
        return _build_cli_prompt(req)

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        cli = resolve_google_cli()
        if cli is None:
            raise RuntimeError(
                "No Google CLI found — install Antigravity (agy) or the Gemini "
                "CLI and sign in with Google."
            )

        prompt = self._render_prompt(req)
        argv = _build_argv(cli, prompt, self._model, _agy_effort(req))
        async for delta in self._complete_via_pipe(argv, prompt):
            yield delta

    def _build_child_env(self, *, harden_path: bool) -> dict[str, str]:
        """Child env: drop API keys (subscription login wins), redirect HOME to an
        isolated hook/mcp-free CLI home (the lag fix), and on Windows repair PATH
        so agy's internal ``cmd.exe``/``npm`` spawns
        resolve even from a degraded launch env."""
        env = {k: v for k, v in os.environ.items() if k not in _DROP_ENV}
        if harden_path and sys.platform == "win32":
            node = shutil.which("node") or shutil.which("node.exe")
            node_dir = os.path.dirname(node) if node else None
            env["PATH"] = repair_agy_path(env.get("PATH", ""), node_dir=node_dir)
        # Redirect HOME so the CLI reads our minimal settings.json (no per-turn
        # PowerShell hook storm, no npm MCP boot) instead of the user's ~/.gemini.
        iso = _ensure_isolated_home(
            real_dir=_real_gemini_dir(), dest_root=_iso_home_root(), model=self._model
        )
        if iso:
            redirect_home_env(env, iso)
        return env

    async def _complete_via_pipe(self, argv: list[str], prompt: str) -> AsyncIterator[BrainDelta]:
        """Drive the official Google CLI with UTF-8 stdin and JSON stdout."""
        env = await asyncio.to_thread(self._build_child_env, harden_path=True)
        workdir = tempfile.mkdtemp(prefix="jarvis-antigravity-brain-")
        creationflags = NO_WINDOW_CREATIONFLAGS if sys.platform == "win32" else 0
        log.info(
            "AntigravityBrain(pipe): spawning %s (model=%s, prompt=%d chars)",
            argv[0],
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

        # communicate drains both output pipes while sending the full prompt;
        # the same deadline covers a child that never consumes its input.
        comm_task = asyncio.create_task(proc.communicate(input=prompt.encode("utf-8")))
        deadline = t0 + self._cli_timeout_s
        stdout_bytes = b""
        stderr_bytes = b""

        async def _kill() -> None:
            if not comm_task.done():
                comm_task.cancel()
            pid = getattr(proc, "pid", None)
            if sys.platform == "win32" and isinstance(pid, int) and pid > 0:
                with suppress(Exception):
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
            log.warning(
                "AntigravityBrain: no answer within %.0fs (killed)",
                self._cli_timeout_s,
            )
            raise RuntimeError(
                f"Antigravity (Google login) did not answer within {self._cli_timeout_s:.0f}s."
            ) from exc
        finally:
            with suppress(OSError):
                shutil.rmtree(workdir, ignore_errors=True)

        if proc.returncode:
            log.warning("AntigravityBrain: CLI exited with status %s", proc.returncode)
            raise RuntimeError(f"Google CLI failed with exit status {proc.returncode}.")
        decoded = stdout_bytes.decode("utf-8", errors="replace")
        try:
            envelope = json.loads(decoded)
        except ValueError:
            envelope = None  # The parser below supports older plain-text CLI output.
        if isinstance(envelope, dict) and (
            envelope.get("error") or envelope.get("status") in {"ERROR", "CANCELED", "CANCELLED"}
        ):
            log.warning("AntigravityBrain: CLI reported a failed result")
            raise RuntimeError("Google CLI reported a failed result.")
        answer = _parse_cli_answer(decoded)
        elapsed = time.monotonic() - t0
        if not answer:
            detail = stderr_bytes.decode("utf-8", errors="replace").strip()[:300]
            log.warning(
                "AntigravityBrain: empty answer after %.1fs rc=%s detail=%s",
                elapsed,
                proc.returncode,
                detail[:200],
            )
            raise RuntimeError(
                "Antigravity (Google login) returned no answer" + (f": {detail}" if detail else ".")
            )

        log.info("AntigravityBrain turn ok: %d chars in %.1fs", len(answer), elapsed)
        yield BrainDelta(content=answer)
        yield BrainDelta(finish_reason="stop")

    def estimate_cost(self, req: BrainRequest) -> float:
        # Billed against the Google subscription, not per-call — report ~0.
        return 0.0
