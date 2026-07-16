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

Isolation note: unlike the CLI workers the provider loop runs IN the app event
loop. Its local ``RunCommand`` tool uses structured argv and an asynchronous
subprocess, assigns that PID to process-tree containment, reduces the child
environment, and reaps the whole tree on completion, timeout, or cancellation.
Fixed POSIX and Windows gate launchers ensure no model-selected target starts
before process-group/Job assignment; argv remains positional data, never shell
source. File tools are dispatched off the event loop. Cancellation is honored
while a command is active as well as at turn boundaries. This containment is
not a filesystem or code-execution sandbox: workspace scripts retain the
Jarvis user's OS rights and intentionally detached POSIX code can escape a
userspace process group.
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

from .api_agent_tools import WORKER_TOOL_SPECS, execute_worker_tool_async
from .capabilities import WorkerCapabilityInventory
from .stream_consumer import (
    ClaudeAssistantMessage,
    ClaudeResult,
    ClaudeSystemInit,
    ClaudeUserMessage,
)

logger = logging.getLogger(__name__)

# Provider errors that mean the FAMILY's key is unusable right now — quota
# depleted / rate-capped ("Your prepayment credits are depleted", 429
# RESOURCE_EXHAUSTED) or auth-dead ("invalid x-api-key", 401). Either way,
# retrying the same family is a guaranteed repeat; the per-family cooldown
# tells the factory's family walk to cross to the next key (missions
# 019f3d01 + 019f3d0f, 2026-07-07 / BUG-042).
_FAMILY_UNUSABLE_MARKERS: tuple[str, ...] = (
    # quota / rate / billing
    "429",
    "quota",
    "rate limit",
    "rate_limit",
    "too many requests",
    "resource_exhausted",
    "depleted",
    "credit",
    "billing",
    "insufficient",
    # auth
    "401",
    "unauthorized",
    "authentication",
    "invalid x-api-key",
    "invalid api key",
    "invalid_api_key",
)


def _error_means_family_unusable(text: str) -> bool:
    """True when a provider error proves the key itself cannot run right now."""
    low = (text or "").lower()
    return any(marker in low for marker in _FAMILY_UNUSABLE_MARKERS)


# Provider slug -> (module, class). Lazy-imported so a worker for one provider
# never imports another's SDK path. Slugs match jarvis provider ids.
_BRAIN_BY_PROVIDER: dict[str, tuple[str, str]] = {
    "openai": ("jarvis.plugins.brain.openai", "OpenAIBrain"),
    "openrouter": ("jarvis.plugins.brain.openrouter", "OpenRouterBrain"),
    # xAI Grok speaks the same OpenAI-compatible streaming/tool-call protocol
    # and needs no vendor SDK or external worker CLI.
    "grok": ("jarvis.plugins.brain.grok", "GrokBrain"),
    # NVIDIA NIM is OpenAI-compatible with no vendor CLI — same in-process
    # tool-loop path as openai/openrouter, running on the user's nvapi- key.
    "nvidia": ("jarvis.plugins.brain.nvidia", "NvidiaBrain"),
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
    "grok": "grok-4.3",
    "claude-api": "claude-opus-4-8",
    "gemini": "gemini-3.1-pro-preview",
    # NVIDIA's own reasoning flagship for heavy subagent work.
    "nvidia": "nvidia/llama-3.1-nemotron-ultra-253b-v1",
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
    except Exception:  # noqa: BLE001, S110 — config read must never crash the worker
        pass
    return _DEFAULT_MODEL.get(prov, "")


_WORKER_TIMEOUT_S: float = 1200.0  # 20 min hard cap, mirrors the other workers
_MAX_TURNS: int = 25
_MAX_TOKENS: int = 8192

_SYSTEM_PROMPT = (
    "You are an autonomous software worker running inside an isolated git "
    "workspace. Your ONLY way to deliver work is to call the provided tools — "
    "Write, Edit, Read, RunCommand, Ls, plus any mission-scoped connected tools — "
    "with file paths relative to the workspace root. Connected tools are "
    "executed by the supervisor and may honestly require human approval. "
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


def _tool_incapable_message(model: str, provider: str, *, detail: str = "") -> str:
    """Shared honest-failure text for a worker model that cannot call tools.

    Missions deliver ALL work through tool calls (Write/Edit/RunCommand/...); a
    text-only reply produces an empty diff and the mission dies 3 critic
    loops later with an inscrutable ``critic_loop_exhausted`` (forensics
    Bug 10). Fail fast and actionably instead.
    """
    msg = (
        f"worker model {model!r} (provider {provider!r}) cannot call tools, "
        "and missions deliver work exclusively through tool calls. Pick a "
        "tool-capable model under Settings -> Jarvis-Agents (brain.worker), "
        "then retry the mission."
    )
    if detail:
        msg = f"{msg} (provider error: {detail})"
    return msg


class ApiAgentWorker:
    """Phase-6 worker that drives an OpenAI-compatible API brain in a tool loop.

    ``cli`` is declared ``"claude"`` so the telemetry schema needs no migration;
    the synthetic init event's ``model`` field carries ``<provider>/<model>`` so
    debugging stays unambiguous.
    """

    cli: Literal["claude"] = "claude"

    def __init__(
        self,
        provider: str,
        *,
        capability_inventory: WorkerCapabilityInventory | None = None,
    ) -> None:
        self.provider = (provider or "").strip().lower()
        self.last_pid: int | None = None
        self.last_session_id: str | None = None
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
        max_turns: int = _MAX_TURNS,
        _broker_binding: Any | None = None,
        **_unused: Any,
    ) -> AsyncIterator[Any]:
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
                max_turns=max_turns,
                broker_binding=broker_binding,
                **_unused,
            ):
                yield event
        finally:
            if issued_here and broker_binding is not None:
                try:
                    broker_binding.close()
                except Exception:  # noqa: BLE001 - cleanup must not mask cancellation
                    logger.exception("ApiAgentWorker: broker binding cleanup failed")

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
        max_turns: int = _MAX_TURNS,
        broker_binding: Any | None,
        **_unused: Any,
    ) -> AsyncIterator[Any]:
        session_id = str(uuid.uuid4())
        self.last_pid = None
        self.last_session_id = session_id
        resolved_model = _resolve_worker_model(self.provider, model)
        log_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 — trivial sync mkdir (mirrors sibling workers)
        stream_path = log_dir / "stream.jsonl"
        written: list[str] = []
        broker_specs = broker_binding.tool_specs if broker_binding is not None else ()
        local_names = {str(spec["name"]) for spec in WORKER_TOOL_SPECS}
        all_tool_specs = WORKER_TOOL_SPECS + tuple(
            spec for spec in broker_specs if str(spec.get("name") or "") not in local_names
        )

        def _emit_line(event: Any) -> None:
            with suppress(OSError):
                with stream_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(event.model_dump(), default=str) + "\n")

        init = ClaudeSystemInit(
            session_id=session_id,
            model=f"{self.provider}/{resolved_model}",
            tools=[t["name"] for t in all_tool_specs],
            cwd=str(worktree),
            external_capabilities=self.capability_inventory.report_for(
                f"api:{self.provider}", binding=broker_binding
            ),
        )
        _emit_line(init)
        yield init

        # Build the brain (provider-agnostic). A missing key / unknown provider
        # → clean error result so the orchestrator's fallback chain takes over.
        try:
            if self.provider not in _BRAIN_BY_PROVIDER:
                raise RuntimeError(f"no API-agent worker for provider {self.provider!r}")
            from jarvis.core.config import (
                get_jarvis_agent_secret,
                override_provider_secrets,
            )

            worker_key = get_jarvis_agent_secret(self.provider)
            with override_provider_secrets({self.provider: worker_key}):
                brain = _build_brain(self.provider, resolved_model)
        except Exception as exc:  # noqa: BLE001
            res = ClaudeResult(
                subtype="error_during_execution",
                is_error=True,
                session_id=session_id,
                duration_ms=0,
                result=f"ApiAgentWorker init failed: {exc}",
            )
            _emit_line(res)
            yield res
            return

        # AP-21: gate on CAPABILITY, and only on an explicit "no" — an absent
        # probe or one that raises is UNKNOWN, and unknown must PROCEED (never
        # brick a mission on a probe glitch). Only can_call_tools() returning
        # False literally gates. Checked BEFORE the turn loop so a tool-less
        # model fails in one line instead of 3 silent critic loops later.
        try:
            can_call_tools = bool(brain.can_call_tools())
        except Exception:  # noqa: BLE001 — capability probe must never brick a mission
            can_call_tools = True
        if not can_call_tools:
            res = ClaudeResult(
                subtype="error_during_execution",
                is_error=True,
                session_id=session_id,
                duration_ms=0,
                result=_tool_incapable_message(resolved_model, self.provider),
            )
            _emit_line(res)
            yield res
            return

        logger.info(
            "ApiAgentWorker[%s] spawn in-process: provider=%s model=%s cwd=%s",
            worker_id,
            self.provider,
            resolved_model,
            worktree,
        )

        messages: list[BrainMessage] = [BrainMessage(role="user", content=prompt)]
        t0 = time.perf_counter()
        final_text = ""
        turns = 0

        try:
            for turn in range(max_turns):
                if time.perf_counter() - t0 > _WORKER_TIMEOUT_S:
                    res = ClaudeResult(
                        subtype="error_during_execution",
                        is_error=True,
                        session_id=session_id,
                        timed_out=True,
                        duration_ms=int((time.perf_counter() - t0) * 1000),
                        result=f"{final_text}\n[timeout after {_WORKER_TIMEOUT_S:.0f}s]".strip(),
                    )
                    _emit_line(res)
                    yield res
                    return

                turns = turn + 1
                req = BrainRequest(
                    messages=tuple(messages),
                    tools=all_tool_specs,
                    system=_SYSTEM_PROMPT,
                    max_tokens=_MAX_TOKENS,
                    stream=True,
                )
                text_parts: list[str] = []
                tool_calls: list[dict[str, Any]] = []
                try:
                    # Override credential lookup only for this awaited provider
                    # call. ContextVar isolation keeps concurrent Brain and
                    # Jarvis-Agent tasks from observing one another's keys.
                    with override_provider_secrets({self.provider: worker_key}):
                        async for delta in brain.complete(req):
                            if delta.content:
                                text_parts.append(delta.content)
                            if delta.tool_call:
                                tool_calls.append(delta.tool_call)
                except Exception as exc:  # noqa: BLE001
                    # The capability pre-gate (above) catches a model the brain
                    # ALREADY knows is tool-incapable. Some providers (OpenRouter
                    # routing to a tool-less upstream) only discover this on the
                    # FIRST live round-trip, surfacing as a 404 "No endpoints
                    # found that support tool use"-class error. Recognize that
                    # shape on turn 0 and convert it to the SAME honest message
                    # instead of letting the raw provider error propagate up as
                    # an inscrutable worker failure. Matched tightly against
                    # OpenRouter's actual copy so an unrelated turn-0 error that
                    # merely mentions tools + support (e.g. "too many tools
                    # defined, exceeds support limit") is NOT mislabeled.
                    low = str(exc).lower()
                    no_tool_endpoints = (
                        ("no endpoints" in low and "tool" in low)
                        or "support tool use" in low
                        or ("404" in low and "tool" in low)
                    )
                    if turn == 0 and no_tool_endpoints:
                        res = ClaudeResult(
                            subtype="error_during_execution",
                            is_error=True,
                            session_id=session_id,
                            duration_ms=int((time.perf_counter() - t0) * 1000),
                            result=_tool_incapable_message(
                                resolved_model, self.provider, detail=str(exc)
                            ),
                        )
                        _emit_line(res)
                        yield res
                        return
                    raise

                assistant_text = "".join(text_parts).strip()
                if assistant_text:
                    final_text = assistant_text

                content_blocks: list[dict[str, Any]] = []
                if assistant_text:
                    content_blocks.append({"type": "text", "text": assistant_text})
                for tc in tool_calls:
                    content_blocks.append(
                        {
                            "type": "tool_use",
                            "id": str(tc.get("id") or uuid.uuid4().hex),
                            "name": str(tc.get("name") or ""),
                            "input": tc.get("input") or {},
                        }
                    )
                assistant_event = ClaudeAssistantMessage(
                    message={"role": "assistant", "content": content_blocks},
                    session_id=session_id,
                )
                _emit_line(assistant_event)
                yield assistant_event

                if not tool_calls:
                    break  # model finished — no more tool calls

                messages.append(BrainMessage(role="assistant", content=content_blocks))

                # Execute each local tool through its async boundary. File I/O
                # is sent to a thread; RunCommand remains cancellable and owns
                # its whole subprocess tree through the mission job.
                result_blocks: list[dict[str, Any]] = []
                for block in content_blocks:
                    if block.get("type") != "tool_use":
                        continue
                    name = block["name"]
                    tool_input = block["input"] if isinstance(block["input"], dict) else {}
                    if name in local_names:
                        result_text, is_error = await execute_worker_tool_async(
                            name,
                            tool_input,
                            worktree=worktree,
                            env=env,
                            job=job,
                            runtime_dir=log_dir / "command-runtime",
                            on_spawn=lambda pid: setattr(self, "last_pid", pid),
                        )
                    elif broker_binding is not None:
                        broker_result = await broker_binding.execute(name, tool_input)
                        result_text = json.dumps(
                            broker_result, ensure_ascii=False, default=str
                        )
                        is_error = not bool(broker_result.get("success"))
                    else:
                        result_text = "Tool is not granted to this mission."
                        is_error = True
                    if name in ("Write", "Edit") and not is_error:
                        fp = str(tool_input.get("file_path", ""))
                        if fp:
                            written.append(fp)
                    result_blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block["id"],
                            "content": result_text,
                            "is_error": is_error,
                        }
                    )
                    messages.append(
                        BrainMessage(
                            role="tool",
                            content=result_text,
                            tool_call_id=block["id"],
                            name=name,
                        )
                    )

                user_event = ClaudeUserMessage(
                    message={"role": "user", "content": result_blocks},
                    session_id=session_id,
                )
                _emit_line(user_event)
                yield user_event

            wall_ms = int((time.perf_counter() - t0) * 1000)
            summary = final_text or (
                f"Completed {len(written)} file change(s): {', '.join(written[:10])}"
                if written
                else "Worker finished with no output."
            )
            res = ClaudeResult(
                subtype="success",
                is_error=False,
                session_id=session_id,
                num_turns=turns,
                duration_ms=wall_ms,
                result=summary[:4000],
            )
            # This family's key works — lift any armed cooldown immediately.
            from jarvis.api_family_quota_state import clear_api_family_cooldown

            clear_api_family_cooldown(self.provider)
            _emit_line(res)
            yield res
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            wall_ms = int((time.perf_counter() - t0) * 1000)
            logger.warning("ApiAgentWorker[%s] failed: %s", worker_id, exc, exc_info=True)
            if _error_means_family_unusable(str(exc)):
                # Remember that THIS key cannot run right now, fingerprinted so
                # a freshly saved key lifts the block instantly. The factory's
                # family walk skips the family on the retry and crosses to the
                # user's next healthy key (AP-22, BUG-042).
                from jarvis.api_family_quota_state import mark_api_family_cooldown
                from jarvis.claude_auth_state import credential_fingerprint

                fp: str | None = None
                try:
                    from jarvis.core.config import get_jarvis_agent_secret

                    fp = credential_fingerprint(
                        get_jarvis_agent_secret(self.provider)
                    )
                except Exception:  # noqa: BLE001 — unreadable store => unbound cooldown
                    fp = None
                mark_api_family_cooldown(self.provider, fingerprint=fp)
                logger.warning(
                    "ApiAgentWorker[%s]: provider %r key is unusable (quota/auth) "
                    "— arming the family cooldown so the worker factory crosses "
                    "to the next reachable key family on the retry.",
                    worker_id,
                    self.provider,
                )
            res = ClaudeResult(
                subtype="error_during_execution",
                is_error=True,
                session_id=session_id,
                num_turns=turns,
                duration_ms=wall_ms,
                result=f"{final_text}\n[worker error: {exc}]".strip(),
            )
            _emit_line(res)
            yield res


__all__ = ["ApiAgentWorker", "supports_api_agent_worker"]
