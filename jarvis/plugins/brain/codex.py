"""OpenAI Codex Brain — two backends, one provider.

Codex can back the conversational *brain* two ways:

* **API key** (``codex_openai_api_key`` / ``openai_api_key``): the fast, cheap
  path — a normal OpenAI chat-completions stream via ``_openai_base``.
* **ChatGPT login (OAuth)**: when no API key is configured but ``codex login``
  has stored a ChatGPT subscription token, we drive the ``codex`` CLI
  (``codex exec``) directly — no per-call billing. This is SLOW (the codex agent
  spins up per turn, ~15-20 s, and burns many tokens) and tool-limited, so it is
  a deliberate fallback the user opts into, not the default. A chat-completions
  endpoint genuinely cannot run on the subscription; the CLI is the only bridge.

The CLI path runs ``codex exec`` in a throwaway temp dir with ``--sandbox
read-only`` (no writes, no dangerous commands), a light "answer conversationally"
prompt, and the OAuth env (drops ``OPENAI_API_KEY``/``CODEX_HOME`` so the global
``~/.codex/auth.json`` subscription token wins). It parses the ``agent_message``
JSON frame and yields it as the brain response.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

from jarvis.core import config as cfg
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.core.protocols import BrainDelta, BrainRequest

from ._openai_base import stream_complete

log = logging.getLogger(__name__)

# Fallback only — the active model comes from [brain.providers.codex].model in
# jarvis.toml. We mirror the proven OpenAIBrain default (a known-good OpenAI
# chat model) rather than a codex-specific id that could 404 out of the box;
# set a codex model in jarvis.toml to use one. Overridable, no code change.
DEFAULT_MODEL = "gpt-5.5"

# Hard cap for a single ``codex exec`` brain turn. The CLI is slow (~15-20 s);
# 90 s leaves headroom for a cold start without hanging the brain coroutine
# forever if the subscription is unreachable.
_CLI_TIMEOUT_S: float = 90.0


def _resolve_codex_binary() -> str | None:
    """On-PATH ``codex`` binary (Windows shim variants included), or None."""
    for name in ("codex", "codex.cmd", "codex.exe"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _codex_oauth_connected() -> bool:
    """True when ``codex login`` has stored a ChatGPT (OAuth) subscription token.

    Best-effort: a missing CLI / unreadable auth file degrades to False (the
    brain then has neither a key nor OAuth and raises a clear error).
    """
    try:
        from jarvis.codex_auth import CodexAuthService

        status = CodexAuthService().status()
        return bool(status.connected and status.mode == "chatgpt")
    except Exception:  # noqa: BLE001
        return False


# Light system instruction for the CLI path. Keeps codex answering as a
# conversational assistant instead of an autonomous coding agent — paired with
# ``--sandbox read-only`` so it physically cannot write files or run risky
# commands even if it tries.
_CLI_SYSTEM = (
    "You are Jarvis, a concise and friendly voice assistant. Answer the user's "
    "message directly in one to three short sentences. Reply in plain text only "
    "— do not run any commands, do not read or edit files, do not use tools."
)


def _build_cli_prompt(req: BrainRequest) -> str:
    """Flatten the recent conversation into a single prompt for ``codex exec``.

    The heavy router system prompt (``req.system`` + role=system messages, full
    of tool definitions) is intentionally dropped — feeding it to the codex agent
    would make it slow, expensive and confused. We send a light conversational
    instruction plus the last few user/assistant turns for context.
    """
    lines: list[str] = [_CLI_SYSTEM, ""]
    # Last ~6 non-system, non-tool turns for context (older history is dropped to
    # keep the codex turn small — every token is slow + billed on the CLI path).
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


class CodexBrain:
    name: str = "codex"
    context_window: int = 128_000
    supports_tools: bool = True
    supports_vision: bool = True

    def __init__(self, model: str | None = None) -> None:
        self._model = model or DEFAULT_MODEL
        self._client: Any = None

    # ---- API-key path -------------------------------------------------

    def _api_key(self) -> str | None:
        return cfg.get_provider_secret("codex") or cfg.get_secret(
            "codex_openai_api_key", "OPENAI_API_KEY"
        )

    def _ensure_client(self, api_key: str) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=api_key)
        return self._client

    # ---- CLI (ChatGPT-OAuth) path ------------------------------------

    async def _complete_via_cli(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        """Drive ``codex exec`` over the ChatGPT login and yield its answer.

        Single non-streaming chunk: the codex agent emits its text in a terminal
        ``agent_message`` frame, so we collect it and yield once. Slow (~15-20 s)
        by nature of the agent spin-up — the caller's stall guard tolerates a
        single turn under its no-progress window.
        """
        binary = _resolve_codex_binary()
        if binary is None:
            raise RuntimeError(
                "Codex CLI not found — run 'npm i -g @openai/codex' and 'codex login'."
            )

        prompt = _build_cli_prompt(req)
        workdir = tempfile.mkdtemp(prefix="jarvis-codex-brain-")
        # OAuth env: drop OPENAI_API_KEY (so the subscription token wins) and
        # CODEX_HOME (a custom home breaks the global ~/.codex auth lookup).
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("OPENAI_API_KEY", "CODEX_HOME")
        }
        cmd = [
            binary,
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "-c",
            "approval_policy=never",
        ]
        creationflags = NO_WINDOW_CREATIONFLAGS if sys.platform == "win32" else 0

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=workdir,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creationflags,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"Codex CLI could not be launched: {exc}") from exc

        try:
            assert proc.stdin is not None  # noqa: S101 — PIPE always present
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=_CLI_TIMEOUT_S
            )
        except TimeoutError as exc:
            with suppress(ProcessLookupError):
                proc.kill()
            raise RuntimeError(
                f"Codex (ChatGPT login) did not answer within {_CLI_TIMEOUT_S:.0f}s."
            ) from exc
        finally:
            with suppress(OSError):
                shutil.rmtree(workdir, ignore_errors=True)

        text_parts: list[str] = []
        error_text: str | None = None
        for raw in stdout_bytes.decode("utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = obj.get("type")
            if t == "item.completed":
                item = obj.get("item", {}) or {}
                if item.get("type") == "agent_message":
                    txt = item.get("text", "")
                    if txt:
                        text_parts.append(txt)
            elif t in ("error", "turn.failed"):
                msg = obj.get("message") or obj.get("error")
                if isinstance(msg, dict):
                    msg = msg.get("message") or json.dumps(msg, ensure_ascii=False)
                error_text = str(msg) if msg else "codex turn failed"

        answer = "\n".join(text_parts).strip()
        if not answer:
            detail = error_text or (
                stderr_bytes.decode("utf-8", errors="replace").strip()[:300]
                if stderr_bytes
                else ""
            )
            raise RuntimeError(
                "Codex (ChatGPT login) returned no answer"
                + (f": {detail}" if detail else ".")
            )

        log.info("CodexBrain CLI turn ok: %d chars via ChatGPT login", len(answer))
        yield BrainDelta(content=answer)
        yield BrainDelta(finish_reason="stop")

    # ---- public API ---------------------------------------------------

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        api_key = self._api_key()
        if api_key:
            client = self._ensure_client(api_key)
            async for delta in stream_complete(client, self._model, req):
                yield delta
            return
        if _codex_oauth_connected():
            async for delta in self._complete_via_cli(req):
                yield delta
            return
        raise RuntimeError(
            "No Codex auth found: save an OpenAI API key (fast) or run "
            "'codex login' (ChatGPT subscription, slow CLI path)."
        )

    def estimate_cost(self, req: BrainRequest) -> float:
        in_tokens = sum(len(str(m.content)) for m in req.messages) // 4
        return (in_tokens * 5 + req.max_tokens * 15) / 1_000_000
