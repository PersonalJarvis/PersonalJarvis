"""OpenAI Codex Brain — two backends, one provider.

Codex can back the conversational *brain* two ways:

* **API key** (``codex_openai_api_key`` / ``openai_api_key``): the fast, cheap
  path — a normal OpenAI chat-completions stream via ``_openai_base``.
* **ChatGPT login (OAuth)**: the explicit subscription voice profile uses the
  persistent ``codex app-server`` stable text protocol and streams assistant
  deltas. The ordinary Codex OAuth fallback retains ``codex exec`` for
  compatibility with the user's non-isolated CLI profile.

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
import re
import shutil
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import suppress
from typing import Any

from jarvis.core import config as cfg
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.core.protocols import BrainDelta, BrainRequest

from ._openai_base import stream_complete
from .cli_prompt_context import (
    extract_reply_language_directive,
    render_cli_standing_instructions,
    render_structured_prompt,
)

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

_CLI_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


def _app_server_id(value: object, key: str) -> str:
    if not isinstance(value, Mapping):
        return ""
    direct = value.get(key)
    if isinstance(direct, str):
        return direct
    nested_name = "turn" if key == "turnId" else "thread"
    nested = value.get(nested_name)
    if isinstance(nested, Mapping) and isinstance(nested.get("id"), str):
        return str(nested["id"])
    return ""


def _agent_message_text(params: Mapping[str, Any]) -> str:
    item = params.get("item")
    if not isinstance(item, Mapping):
        return ""
    item_type = str(item.get("type") or "").replace("_", "").lower()
    if item_type != "agentmessage":
        return ""
    text = item.get("text")
    if isinstance(text, str):
        return text
    content = item.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, Mapping) and isinstance(block.get("text"), str):
            parts.append(str(block["text"]))
    return "".join(parts)


def _completed_turn_status(params: Mapping[str, Any]) -> str:
    turn = params.get("turn")
    status: object = turn.get("status") if isinstance(turn, Mapping) else None
    if isinstance(status, Mapping):
        status = status.get("type")
    if not isinstance(status, str):
        status = params.get("status")
    return str(status or "").strip().lower()


def _resolve_codex_binary() -> str | None:
    """On-PATH ``codex`` binary (Windows shim variants included), or None.

    Delegates to the same resolver the Providers card uses. Doing its own
    ``shutil.which`` meant the card could report the subscription as connected
    while every action failed with "Codex CLI not found": a GUI-launched app on
    macOS (and a Windows app started from Explorer) inherits a login PATH that
    never saw the user's shell profile, and only ``CodexAuthService`` repairs
    that via ``ensure_cli_paths()``. Two resolvers, two answers, one confusing
    bug — so there is now one.
    """
    try:
        from jarvis.codex_auth import CodexAuthService  # noqa: PLC0415

        resolved = CodexAuthService()._resolve_binary()
        if resolved:
            return resolved
    except Exception:  # noqa: BLE001 — fall back to the plain PATH probe
        log.debug("Shared Codex binary resolution failed", exc_info=True)
    for name in ("codex", "codex.cmd", "codex.exe"):
        path = shutil.which(name)
        if path:
            return path
    return None


async def _terminate_posix_process_group(pid: int, proc: Any) -> None:
    """Reap a POSIX process group: ``SIGTERM``, a short grace, then ``SIGKILL``.

    Best-effort by nature — a tree that already exited is the good outcome, and
    a group we may not signal is not worth failing a teardown over.
    """
    import signal  # noqa: PLC0415 — POSIX-only, kept off the import path

    killpg = getattr(os, "killpg", None)
    getpgid = getattr(os, "getpgid", None)
    if not callable(killpg):
        return
    try:
        pgid = getpgid(pid) if callable(getpgid) else pid
    except (ProcessLookupError, OSError):
        # Silence is right: the child is already gone, or has no readable
        # group. It was started with `start_new_session`, so its pid IS the
        # group in every case that still matters — falling back is exact.
        pgid = pid

    def _signal(sig: int) -> bool:
        try:
            killpg(pgid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            # Silence is right: "already exited" is the outcome we wanted, and
            # a group that is not ours to signal cannot be reaped by retrying.
            # The bool tells the caller not to bother escalating.
            return False
        return True

    if not _signal(signal.SIGTERM):
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=2.0)
        return
    except Exception as exc:  # noqa: BLE001 — SIGTERM ignored, escalate
        log.debug(
            "Codex CLI process group %s survived SIGTERM (%s); escalating",
            pgid,
            type(exc).__name__,
        )
    _signal(getattr(signal, "SIGKILL", signal.SIGTERM))


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


#: Launcher extensions that are not programs at all but scripts whose FIRST act
#: is to look up a bare ``node``. Resolving the shim itself therefore proves
#: nothing about whether it can run.
_NODE_DEPENDENT_SHIMS = (".cmd", ".bat", ".ps1")


def _ensure_node_reachable(env: dict[str, str], binary: str) -> None:
    """Give the npm shim a PATH its own bare ``node`` lookup can satisfy.

    Live 2026-08-06 17:42: the shim resolved fine and the spawn returned rc=1
    with "node is not recognized" on stderr — the app's PATH carried the npm
    global dir but not the Node.js install dir. Upstairs that surfaced as
    "returned no answer", which reads like a model that stayed silent rather
    than a launcher that never started (AP-30).

    The repair is the same one the mission workers already use: resolve Node
    out-of-PATH and put its directory on the child's PATH. Only when that
    fails too, and only for a launcher that cannot run without it, is this a
    hard error — a native binary needs no interpreter, and guessing wrong
    would break a working install.
    """
    from jarvis.core.path_augment import resolve_node_executable  # noqa: PLC0415

    node = resolve_node_executable()
    if node:
        node_dir = os.path.dirname(node)
        current = env.get("PATH", "")
        known = {
            os.path.normcase(os.path.normpath(part)) for part in current.split(os.pathsep) if part
        }
        if os.path.normcase(os.path.normpath(node_dir)) not in known:
            env["PATH"] = f"{current}{os.pathsep}{node_dir}" if current else node_dir
            log.info(
                "CodexBrain CLI: added the Node.js directory to the child PATH "
                "so the npm launcher can find its interpreter (%s)",
                node_dir,
            )
        return
    if binary.lower().endswith(_NODE_DEPENDENT_SHIMS):
        raise RuntimeError(
            "Node.js was not found, and the Codex CLI on this host is an npm "
            "launcher that cannot start without it — install Node.js, or "
            "reinstall the CLI with 'npm i -g @openai/codex'."
        )
    log.warning(
        "CodexBrain CLI: Node.js is not resolvable on this host; %s must be a "
        "native build or the spawn will fail",
        binary,
    )


def _build_cli_command(binary: str, model: str | None) -> list[str]:
    """Build the read-only subscription CLI command with an optional model."""
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
    selected = str(model or "").strip()
    if selected:
        if not _CLI_MODEL_RE.fullmatch(selected):
            raise RuntimeError("Codex subscription model id contains unsupported characters.")
        cmd.extend(["--model", selected])
    return cmd


# Light system instruction for the CLI path. Keeps codex answering as a
# conversational assistant instead of an autonomous coding agent — paired with
# ``--sandbox read-only`` so it physically cannot write files or run risky
# commands even if it tries.
_CLI_SYSTEM = (
    "You are the user's concise and friendly voice assistant. Answer the user's "
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
    prefs = render_cli_standing_instructions(req.system)
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
    if prefs:
        lines.extend(["", prefs])
    # The reply-language directive rides LAST (highest recency) so the CLI model
    # answers in the turn's resolved language instead of anchoring to the German
    # persona — without this the directive is dropped (live bug 2026-06-21, the
    # antigravity sibling: an English request was answered in German).
    lang_directive = extract_reply_language_directive(req.system)
    if lang_directive:
        lines.extend(["", lang_directive])
    lines.append("Assistant:")
    return "\n".join(lines)


class CodexBrain:
    name: str = "codex"
    context_window: int = 128_000
    supports_tools: bool = True
    # supports_vision is RUNTIME, not a fixed True (mirrors can_call_tools): only
    # the API-key path passes images to the model (complete() → stream_complete,
    # which defaults supports_vision=True). The ChatGPT-subscription CLI
    # (codex exec) flattens the turn to text via _build_cli_prompt and DROPS
    # every image, so on the CLI path codex is BLIND. The Computer-Use screenshot
    # loop skips supports_vision=False providers (screenshot_only_loop._call_brain),
    # so a static True made CU hand a screenshot to a brain that cannot see it and
    # plan blind. The class default is the safe blind value; __init__ flips it to
    # True only when an API key (→ the vision-capable API path) is configured.
    supports_vision: bool = False
    # The subscription CLI path has no dedicated system channel: structured
    # mode prepends the caller's contract to the flattened prompt
    # (render_structured_prompt). The subscription resolver prefers a sibling
    # that forwards the contract on a real system channel when one is signed
    # in — see the antigravity note for the live failure this prevents.
    native_system_prompt: bool = False

    def __init__(
        self,
        model: str | None = None,
        structured_prompts: bool = False,
        cli_timeout_s: float | None = None,
        prefer_subscription: bool = False,
        persistent_subscription_transport: bool = False,
    ) -> None:
        self._model = model or DEFAULT_MODEL
        # Empty means "use the subscription CLI default". Keep this distinct
        # from the API fallback model so an unpinned subscription never gets an
        # unrelated API default forced onto it.
        self._cli_model = str(model or "").strip()
        self._client: Any = None
        # Background/structured callers (the wiki curator tier) set this so the
        # CLI path forwards their JSON contract verbatim instead of the
        # conversational "answer in 1-3 plain-text sentences" wrapper — which
        # made structured output impossible by instruction.
        self._structured_prompts = bool(structured_prompts)
        # Capability hint for surfaces that explicitly present the ChatGPT
        # subscription as a separate choice from the OpenAI API card. Without
        # it, a stored API key silently wins and the user's selected
        # subscription card is billed through the API instead.
        self._prefer_subscription = bool(prefer_subscription)
        # Voice follow-ups reuse one warm App Server on the SpeechPipeline's
        # event loop. One-shot callers (provider previews and setup checks)
        # must close their transport after the turn; otherwise they retain the
        # dedicated subscription-profile lease and the real voice loop cannot
        # answer even though STT completed successfully.
        self._persistent_subscription_transport = bool(persistent_subscription_transport)
        # Slow background callers (the wiki Stage-2 judge sends ~16k-char
        # body-aware prompts) pass their own per-call budget; the voice-tier
        # default stays the tight cap. Live 2026-07-21: the judge died on
        # every run because the fixed 90 s cap killed codex before the wiki
        # tier's 180 s budget was half used.
        try:
            budget = float(cli_timeout_s) if cli_timeout_s is not None else 0.0
        except (TypeError, ValueError):
            budget = 0.0
        self._cli_timeout_s = budget if budget > 0 else _CLI_TIMEOUT_S
        # Only the API-key path can see images (see the supports_vision note).
        self.supports_vision = bool(self._api_key()) and not self._prefer_subscription

    @staticmethod
    def subscription_connected() -> bool:
        """Whether this provider's ChatGPT subscription login is usable now.

        The subscription resolver asks the provider class rather than importing
        an auth service per family, so each brain answers for itself. Never
        raises — a failed probe means "not connected", never a broken turn.
        """
        return _codex_oauth_connected()

    def can_call_tools(self) -> bool:
        """Runtime tool-calling capability (NOT the static ``supports_tools``).

        Only the API-key path can emit ``tool_calls``. The ChatGPT-subscription
        CLI (``codex exec``) drives an autonomous agent over a flattened prompt
        and drops every tool — so when no API key is configured this brain cannot
        run a tool/Computer-Use turn itself. The caller (``BrainManager``) uses
        this to delegate tool turns to a tool-capable provider instead of letting
        the CLI confabulate a refusal."""
        return bool(self._api_key()) and not self._prefer_subscription

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

    def _render_prompt(self, req: BrainRequest) -> str:
        """Conversational flattening for voice turns; verbatim for structured."""
        if self._structured_prompts:
            return render_structured_prompt(req)
        return _build_cli_prompt(req)

    async def _complete_via_app_server(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        """Stream a ChatGPT-subscription turn over the stable App Server API."""
        from jarvis.codex_app_server import (  # noqa: PLC0415
            CodexAppServerClient,
            CodexAppServerDisconnected,
            CodexAppServerTimeout,
            get_shared_codex_app_server,
        )

        binary_path = (
            str(getattr(getattr(cfg.load_config(), "codex", None), "binary_path", "")).strip()
            or None
        )
        if self._persistent_subscription_transport:
            client = get_shared_codex_app_server(binary_path, purpose="text")
        else:
            client = CodexAppServerClient(binary_path=binary_path, purpose="text")
        try:
            prompt = self._render_prompt(req)
            started = await client.text_thread_start()
            thread_id = _app_server_id(started, "threadId")
            if not thread_id:
                raise RuntimeError("Codex App Server returned no text thread id.")

            subscription = client.subscribe(thread_id)
            turn_id = ""
            emitted = ""
            authoritative = ""
            finished = False
            started_at = time.monotonic()
            try:
                turn_result = await client.turn_start(thread_id, prompt)
                turn_id = _app_server_id(turn_result, "turnId")
                deadline = started_at + self._cli_timeout_s
                while not finished:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError
                    notification = await subscription.get(timeout_s=remaining)
                    params = notification.params
                    event_turn_id = _app_server_id(params, "turnId")
                    if turn_id and event_turn_id and event_turn_id != turn_id:
                        continue
                    if notification.method == "turn/started":
                        turn_id = turn_id or event_turn_id
                        continue
                    if notification.method == "item/agentMessage/delta":
                        delta = params.get("delta")
                        if isinstance(delta, str) and delta:
                            emitted += delta
                            yield BrainDelta(content=delta)
                        continue
                    if notification.method == "item/completed":
                        final_text = _agent_message_text(params)
                        if not final_text:
                            continue
                        authoritative += final_text
                        if final_text.startswith(emitted):
                            suffix = final_text[len(emitted) :]
                            if suffix:
                                emitted += suffix
                                yield BrainDelta(content=suffix)
                        elif not emitted:
                            emitted = final_text
                            yield BrainDelta(content=final_text)
                        else:
                            log.warning(
                                "Codex App Server final text diverged from streamed "
                                "deltas; keeping the already delivered stream to avoid "
                                "duplicate speech"
                            )
                        continue
                    if notification.method == "turn/completed":
                        status = _completed_turn_status(params)
                        if status and status not in {"completed", "interrupted"}:
                            raise RuntimeError(
                                f"Codex App Server text turn ended with status {status}."
                            )
                        if status == "interrupted":
                            raise RuntimeError("Codex App Server text turn was interrupted.")
                        finished = True
                    elif notification.method in {"error", "turn/failed"}:
                        raise RuntimeError("Codex App Server text turn failed.")
            except asyncio.CancelledError:
                if turn_id:
                    with suppress(Exception):
                        await asyncio.shield(client.turn_interrupt(thread_id, turn_id))
                raise
            except (TimeoutError, CodexAppServerTimeout) as exc:
                if turn_id:
                    with suppress(Exception):
                        await asyncio.shield(client.turn_interrupt(thread_id, turn_id))
                raise RuntimeError(
                    "Codex App Server did not finish the subscription turn within "
                    f"{self._cli_timeout_s:.0f}s."
                ) from exc
            finally:
                subscription.close()
                try:
                    await asyncio.shield(
                        asyncio.wait_for(client.thread_unsubscribe(thread_id), timeout=3.0)
                    )
                except (TimeoutError, CodexAppServerDisconnected):
                    log.warning("Codex App Server text thread cleanup did not complete")
                except Exception:
                    log.debug("Codex App Server text thread cleanup failed", exc_info=True)

            answer = (authoritative or emitted).strip()
            if not answer:
                raise RuntimeError("Codex App Server returned no subscription answer.")
            log.info(
                "Codex App Server text turn ok: %d chars in %.1fs via ChatGPT login",
                len(answer),
                time.monotonic() - started_at,
            )
            yield BrainDelta(finish_reason="stop")
        finally:
            if not self._persistent_subscription_transport:
                try:
                    await asyncio.shield(client.close())
                except Exception:
                    log.warning(
                        "Codex App Server one-shot transport cleanup failed",
                        exc_info=True,
                    )

    async def _complete_via_cli(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        """Drive ``codex exec`` over the ChatGPT login and stream its answer.

        The codex agent emits JSON event frames; the answer arrives in a terminal
        ``agent_message`` frame (~15-20 s in). ``proc.communicate()`` drains both
        pipes safely (no stderr-buffer deadlock); we await it as a task and emit a
        no-text progress tick every few seconds so the dispatcher's *no-progress*
        stall deadline keeps resetting — otherwise a single ~20 s silent await is
        cancelled and the turn falls back to another provider (the live "Gemini
        answered while Codex was the active brain" bug). INFO logging makes the
        desktop log show whether the turn ran, how long it took, or why it failed.
        """
        binary = _resolve_codex_binary()
        if binary is None:
            raise RuntimeError(
                "Codex CLI not found — run 'npm i -g @openai/codex' and 'codex login'."
            )

        prompt = self._render_prompt(req)
        workdir = tempfile.mkdtemp(prefix="jarvis-codex-brain-")
        # OAuth env: drop OPENAI_API_KEY (so the subscription token wins) and
        # CODEX_HOME (a custom home breaks the global ~/.codex auth lookup).
        env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "CODEX_HOME")}
        _ensure_node_reachable(env, binary)
        cmd = _build_cli_command(binary, self._cli_model)
        creationflags = NO_WINDOW_CREATIONFLAGS if sys.platform == "win32" else 0
        log.info(
            "CodexBrain CLI: spawning '%s exec' for the ChatGPT-login brain (prompt=%d chars)",
            binary,
            len(prompt),
        )

        t0 = time.monotonic()
        # POSIX: give the CLI its own process group so a timeout can reap the
        # WHOLE tree. The npm launcher execs a real `codex` child; killing only
        # the launcher left that child running, so every capped or cancelled
        # subscription turn leaked a process on macOS and Linux while Windows
        # was already clean via `taskkill /T`. Windows rejects the kwarg.
        session_kwargs: dict[str, Any] = (
            {} if sys.platform == "win32" else {"start_new_session": True}
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=workdir,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creationflags,
                **session_kwargs,
            )
        except (FileNotFoundError, OSError) as exc:
            with suppress(OSError):
                shutil.rmtree(workdir, ignore_errors=True)
            log.warning("CodexBrain CLI: spawn failed: %s", exc)
            raise RuntimeError(f"Codex CLI could not be launched: {exc}") from exc

        try:
            assert proc.stdin is not None  # noqa: S101 — PIPE always present
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass

        comm_task = asyncio.create_task(proc.communicate())
        deadline = t0 + self._cli_timeout_s
        stdout_bytes = b""
        stderr_bytes = b""

        async def _kill_cli_process() -> None:
            if not comm_task.done():
                comm_task.cancel()
            pid = getattr(proc, "pid", None)
            if sys.platform == "win32" and isinstance(pid, int) and pid > 0:
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
            elif sys.platform != "win32" and isinstance(pid, int) and pid > 0:
                # The POSIX sibling of `taskkill /T`: the CLI leads its own
                # group (start_new_session above), so one signal reaps the
                # launcher AND the real codex child it exec'd.
                await _terminate_posix_process_group(pid, proc)
            with suppress(OSError):
                proc.kill()
            with suppress(Exception):  # noqa: BLE001
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
                # alive through the ~20 s codex spin-up (yields nothing visible).
                yield BrainDelta(content="")
        except asyncio.CancelledError:
            await _kill_cli_process()
            log.info("CodexBrain CLI: cancelled (killed)")
            raise
        except TimeoutError as exc:
            await _kill_cli_process()
            log.warning(
                "CodexBrain CLI: no answer within %.0fs (killed)",
                self._cli_timeout_s,
            )
            raise RuntimeError(
                f"Codex (ChatGPT login) did not answer within {self._cli_timeout_s:.0f}s."
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
        elapsed = time.monotonic() - t0
        if not answer:
            detail = error_text or (
                stderr_bytes.decode("utf-8", errors="replace").strip()[:300] if stderr_bytes else ""
            )
            log.warning(
                "CodexBrain CLI: empty answer after %.1fs rc=%s detail=%s",
                elapsed,
                proc.returncode,
                detail[:200],
            )
            raise RuntimeError(
                "Codex (ChatGPT login) returned no answer" + (f": {detail}" if detail else ".")
            )

        log.info(
            "CodexBrain CLI turn ok: %d chars in %.1fs via ChatGPT login",
            len(answer),
            elapsed,
        )
        yield BrainDelta(content=answer)
        yield BrainDelta(finish_reason="stop")

    # ---- public API ---------------------------------------------------

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        if self._prefer_subscription:
            if req.tools:
                raise RuntimeError("Codex ChatGPT subscription mode cannot execute brain tools.")
            log.info(
                "CodexBrain.complete: explicit ChatGPT-subscription path (model=%s)",
                self._model,
            )
            async for delta in self._complete_via_app_server(req):
                yield delta
            return

        api_key = self._api_key()
        if api_key:
            log.info("CodexBrain.complete: API-key path (model=%s)", self._model)
            client = self._ensure_client(api_key)
            emitted = False
            try:
                async for delta in stream_complete(client, self._model, req):
                    if delta.content or delta.tool_call:
                        emitted = True
                    yield delta
                return
            except Exception as exc:  # noqa: BLE001 — classified below, re-raised when not recoverable
                status = getattr(exc, "status_code", None)
                if status is None:
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                # A throttled/dead API KEY must not brick the provider while
                # the user's paid ChatGPT subscription sits idle next to it
                # (live 2026-07-18: every wiki call died on a 429ing key).
                # Only account-level failures cross over, and only when the
                # stream produced nothing a listener could have consumed.
                if emitted or status not in (401, 402, 403, 429):
                    raise
                if req.tools:
                    # The CLI path is tool-blind; answering a tool turn with
                    # plausible prose would LOOK like "model chose no tool"
                    # and defeat BrainManager's delegation to a genuinely
                    # tool-capable provider. Let the chain handle it instead.
                    log.warning(
                        "CodexBrain: API-key path failed (HTTP %s) with %d "
                        "tool(s) requested — not crossing to the tool-blind "
                        "CLI; surfacing the error for provider fallback",
                        status,
                        len(req.tools),
                    )
                    raise
                if not await asyncio.to_thread(_codex_oauth_connected):
                    raise
                log.warning(
                    "CodexBrain: API-key path failed (HTTP %s) — falling back "
                    "to the ChatGPT-subscription CLI",
                    status,
                )
            async for delta in self._complete_via_cli(req):
                yield delta
            return
        # to_thread: the probe shells out to `codex --version` on its first
        # call per process (up to ~4s) — never block the event loop for it.
        oauth = await asyncio.to_thread(_codex_oauth_connected)
        log.info(
            "CodexBrain.complete: no API key — oauth=%s, %d tool(s) requested "
            "(ignored on the CLI path)",
            oauth,
            len(req.tools or ()),
        )
        if oauth:
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
