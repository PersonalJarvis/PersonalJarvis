"""ApiAgentWorker — an in-process agentic Phase-6 worker for API brains.

The CLI workers (claude / codex / agy) wrap a vendor binary that ships its own
agent loop + file tools. The pure-API providers — **openai**, **openrouter** —
have no such CLI, so picking them as the subagent provider used to fall through
to the Claude worker (they silently ran on Claude). This worker
closes that gap: it drives the selected provider's own chat API
(`jarvis.plugins.brain.*`) in a tool-use loop, writing real files into the git
worktree via :mod:`api_agent_tools`, and emits the same claude-shaped stream the
Kontrollierer + Critic already read (so NO orchestrator change is needed).

Provider-agnostic by design (multi-provider mandate / AP-21): the provider slug
selects the Brain class via a capability-style map; nothing branches on a model
id. A missing API key degrades to a clean error result so the orchestrator's
fallback chain takes over instead of crashing.

Isolation note: unlike the subprocess workers this runs IN the app event loop —
there is no child PID to hand the Job Object, so ``job`` is accepted but unused;
the only spawned processes are short-lived ``Bash`` tool subprocesses (run off
the loop via ``run_in_executor`` so the voice pipeline never blocks). Cancel is
honored cooperatively at each turn boundary.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal

from jarvis.core.protocols import BrainMessage, BrainRequest

from .api_agent_tools import WORKER_TOOL_SPECS, execute_worker_tool
from .stream_consumer import (
    ClaudeAssistantMessage,
    ClaudeResult,
    ClaudeSystemInit,
    ClaudeUserMessage,
)

logger = logging.getLogger(__name__)

# Provider slug -> (module, class). Lazy-imported so a worker for one provider
# never imports another's SDK path. Slugs match jarvis provider ids.
_BRAIN_BY_PROVIDER: dict[str, tuple[str, str]] = {
    "openai": ("jarvis.plugins.brain.openai", "OpenAIBrain"),
    "openrouter": ("jarvis.plugins.brain.openrouter", "OpenRouterBrain"),
    # B3/B4 (open-source AP-22): claude-api + gemini run the SAME in-process tool
    # loop, so an Anthropic- or Gemini-API-key-only user can run heavy missions
    # without the npm `claude`/`gemini` CLI binary. The CLI worker stays preferred
    # (subscription-first) when its binary is present; this is the API fallback.
    "claude-api": ("jarvis.plugins.brain.claude_api", "ClaudeAPIBrain"),
    "gemini": ("jarvis.plugins.brain.gemini", "GeminiBrain"),
}

# A free OpenRouter model id — the LAST-RESORT default for the gateway provider
# when the user configured no model at all. Never a paid Anthropic id: a gateway
# default that silently bills the most expensive model is the exact open-source
# §3 / AP-22 trap (live forensic 2026-06-29 drained ~5€ on Opus 4.8). A wrong free
# id degrades with a clean 404; a wrong-but-valid paid id bills the user silently.
_OPENROUTER_FREE_DEFAULT = "nvidia/nemotron-3-ultra-550b-a55b:free"

# Per-provider default deep model when the step carries none AND the user pinned
# nothing. Documented defaults only — never a gate (AP-21).
_DEFAULT_MODEL: dict[str, str] = {
    "openai": "gpt-5.5",
    "openrouter": _OPENROUTER_FREE_DEFAULT,
    "claude-api": "claude-opus-4-8",
    "gemini": "gemini-3.1-pro-preview",
}


def _resolve_worker_model(provider: str, explicit: str) -> str:
    """Model for the in-process API worker, honoring the user's PICK.

    Precedence:
      1. the step's explicit model (the decomposer pinned one) — always wins;
      2. ``[brain.sub_jarvis].model`` but ONLY when its provider matches this
         worker (a pin set for antigravity must never run on the openrouter key);
      3. ``[brain.providers[provider]].model`` — the user's own pick for this
         provider (e.g. the free OpenRouter model);
      4. the documented per-provider ``_DEFAULT_MODEL`` (non-paid for openrouter).

    Never a hardcoded paid foreign-family id while the user has picked something
    (AP-21/AP-22, open-source single-key §3).
    """
    if explicit and explicit.strip():
        return explicit.strip()
    prov = (provider or "").strip().lower()
    try:
        from jarvis.core import config as _cfg

        root = _cfg.load_config()
        sub = getattr(root.brain, "worker", None)
        if (
            sub is not None
            and (getattr(sub, "provider", "") or "").strip().lower() == prov
            and (getattr(sub, "model", "") or "").strip()
        ):
            return sub.model.strip()
        pc = (root.brain.providers or {}).get(prov)
        picked = (getattr(pc, "model", "") or "").strip()
        if picked:
            return picked
    except Exception:  # noqa: BLE001 — config read must never crash the worker
        pass
    return _DEFAULT_MODEL.get(prov, "")

_WORKER_TIMEOUT_S: float = 1200.0  # 20 min hard cap, mirrors the other workers
_MAX_TURNS: int = 25
_MAX_TOKENS: int = 8192

_SYSTEM_PROMPT = (
    "You are an autonomous software worker running inside an isolated git "
    "workspace. Your ONLY way to deliver work is to call the provided tools — "
    "Write, Edit, Read, Bash, Ls — with paths relative to the workspace root. "
    "Actually CREATE and EDIT the files the task needs; never just describe what "
    "you would do (a text description is not a deliverable and will be rejected). "
    "Work autonomously without asking questions: if a detail is unspecified, pick "
    "a sensible default and build the finished artefact. When the task is fully "
    "done, reply with a one-line summary and stop calling tools."
)


def supports_api_agent_worker(provider: str | None) -> bool:
    """True if ``provider`` has an in-process API-agent worker."""
    return (provider or "").strip().lower() in _BRAIN_BY_PROVIDER


def _build_brain(provider: str, model: str) -> Any:
    mod_name, cls_name = _BRAIN_BY_PROVIDER[provider]
    mod = __import__(mod_name, fromlist=[cls_name])
    return getattr(mod, cls_name)(model=model)


class ApiAgentWorker:
    """Phase-6 worker that drives an OpenAI-compatible API brain in a tool loop.

    ``cli`` is declared ``"claude"`` so the telemetry schema needs no migration;
    the synthetic init event's ``model`` field carries ``<provider>/<model>`` so
    debugging stays unambiguous.
    """

    cli: Literal["claude"] = "claude"

    def __init__(self, provider: str) -> None:
        self.provider = (provider or "").strip().lower()
        self.last_pid: int | None = None
        self.last_session_id: str | None = None

    async def spawn(
        self,
        prompt: str,
        *,
        worktree: Path,
        env: dict[str, str],  # noqa: ARG002 — in-process brain reads creds via cfg
        job: Any,  # noqa: ARG002 — no child PID to assign (in-process)
        worker_id: str,
        log_dir: Path,
        model: str = "",
        max_turns: int = _MAX_TURNS,
        **_unused: Any,
    ) -> AsyncIterator[Any]:
        session_id = str(uuid.uuid4())
        self.last_session_id = session_id
        resolved_model = _resolve_worker_model(self.provider, model)
        log_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 — trivial sync mkdir (mirrors sibling workers)
        stream_path = log_dir / "stream.jsonl"
        written: list[str] = []

        def _emit_line(event: Any) -> None:
            with suppress(OSError):
                with stream_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(event.model_dump(), default=str) + "\n")

        init = ClaudeSystemInit(
            session_id=session_id,
            model=f"{self.provider}/{resolved_model}",
            tools=[t["name"] for t in WORKER_TOOL_SPECS],
            cwd=str(worktree),
        )
        _emit_line(init)
        yield init

        # Build the brain (provider-agnostic). A missing key / unknown provider
        # → clean error result so the orchestrator's fallback chain takes over.
        try:
            if self.provider not in _BRAIN_BY_PROVIDER:
                raise RuntimeError(f"no API-agent worker for provider {self.provider!r}")
            brain = _build_brain(self.provider, resolved_model)
        except Exception as exc:  # noqa: BLE001
            res = ClaudeResult(
                subtype="error_during_execution", is_error=True, session_id=session_id,
                duration_ms=0, result=f"ApiAgentWorker init failed: {exc}",
            )
            _emit_line(res)
            yield res
            return

        logger.info(
            "ApiAgentWorker[%s] spawn in-process: provider=%s model=%s cwd=%s",
            worker_id, self.provider, resolved_model, worktree,
        )

        messages: list[BrainMessage] = [BrainMessage(role="user", content=prompt)]
        loop = asyncio.get_running_loop()
        t0 = time.perf_counter()
        final_text = ""
        turns = 0

        try:
            for turn in range(max_turns):
                if time.perf_counter() - t0 > _WORKER_TIMEOUT_S:
                    res = ClaudeResult(
                        subtype="error_during_execution", is_error=True,
                        session_id=session_id, timed_out=True,
                        duration_ms=int((time.perf_counter() - t0) * 1000),
                        result=f"{final_text}\n[timeout after {_WORKER_TIMEOUT_S:.0f}s]".strip(),
                    )
                    _emit_line(res)
                    yield res
                    return

                turns = turn + 1
                req = BrainRequest(
                    messages=tuple(messages),
                    tools=WORKER_TOOL_SPECS,
                    system=_SYSTEM_PROMPT,
                    max_tokens=_MAX_TOKENS,
                    stream=True,
                )
                text_parts: list[str] = []
                tool_calls: list[dict[str, Any]] = []
                async for delta in brain.complete(req):
                    if delta.content:
                        text_parts.append(delta.content)
                    if delta.tool_call:
                        tool_calls.append(delta.tool_call)

                assistant_text = "".join(text_parts).strip()
                if assistant_text:
                    final_text = assistant_text

                content_blocks: list[dict[str, Any]] = []
                if assistant_text:
                    content_blocks.append({"type": "text", "text": assistant_text})
                for tc in tool_calls:
                    content_blocks.append({
                        "type": "tool_use",
                        "id": str(tc.get("id") or uuid.uuid4().hex),
                        "name": str(tc.get("name") or ""),
                        "input": tc.get("input") or {},
                    })
                assistant_event = ClaudeAssistantMessage(
                    message={"role": "assistant", "content": content_blocks},
                    session_id=session_id,
                )
                _emit_line(assistant_event)
                yield assistant_event

                if not tool_calls:
                    break  # model finished — no more tool calls

                messages.append(BrainMessage(role="assistant", content=content_blocks))

                # Execute each tool call off the event loop, emit tool_result.
                result_blocks: list[dict[str, Any]] = []
                for block in content_blocks:
                    if block.get("type") != "tool_use":
                        continue
                    name = block["name"]
                    tool_input = block["input"] if isinstance(block["input"], dict) else {}
                    result_text, is_error = await loop.run_in_executor(
                        None,
                        lambda n=name, i=tool_input: execute_worker_tool(
                            n, i, worktree=worktree
                        ),
                    )
                    if name in ("Write", "Edit") and not is_error:
                        fp = str(tool_input.get("file_path", ""))
                        if fp:
                            written.append(fp)
                    result_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": result_text,
                        "is_error": is_error,
                    })
                    messages.append(BrainMessage(
                        role="tool", content=result_text, tool_call_id=block["id"], name=name,
                    ))

                user_event = ClaudeUserMessage(
                    message={"role": "user", "content": result_blocks},
                    session_id=session_id,
                )
                _emit_line(user_event)
                yield user_event

            wall_ms = int((time.perf_counter() - t0) * 1000)
            summary = final_text or (
                f"Completed {len(written)} file change(s): {', '.join(written[:10])}"
                if written else "Worker finished with no output."
            )
            res = ClaudeResult(
                subtype="success", is_error=False, session_id=session_id,
                num_turns=turns, duration_ms=wall_ms, result=summary[:4000],
            )
            _emit_line(res)
            yield res
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            wall_ms = int((time.perf_counter() - t0) * 1000)
            logger.warning("ApiAgentWorker[%s] failed: %s", worker_id, exc, exc_info=True)
            res = ClaudeResult(
                subtype="error_during_execution", is_error=True, session_id=session_id,
                num_turns=turns, duration_ms=wall_ms,
                result=f"{final_text}\n[worker error: {exc}]".strip(),
            )
            _emit_line(res)
            yield res


__all__ = ["ApiAgentWorker", "supports_api_agent_worker"]
