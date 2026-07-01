"""TaskRunner — dispatches a persisted task spec to its action.

Lifecycle of a task:

    scheduled → running → (completed | failed | cancelled)

The runner loads the spec from the store, sets the state to ``running``,
and then branches on the action kind:

- ``HarnessDispatchAction`` → ``HarnessManager.dispatch(...)`` and streams
  progress results as ``task_steps`` rows.
- ``SpeakAction`` → ``TTSProvider.synthesize(text)``; the audio chunks are
  forwarded to the output device (audio-out routing is outside our scope —
  we consume the stream and log step lines).
- ``ToolCallAction`` → ``ToolExecutor.execute(tool, args)`` via the tool
  registry. Risk-tier/approval work as usual.

Retry policy: after a failure we increment ``attempts`` and check
``max_attempts``. On retry: the state stays ``scheduled`` (so the scheduler
re-enqueues the task — that happens in a separate reschedule call by the
orchestrator, see ADR-0005). Simplification in Phase 5: no automatic backoff
rescheduling for on_event tasks; only time-based ones get ``finished_at_ns``
on failure and stay ``failed``. The task-curator job (later) can retry.

**Cancel handling:** the runner checks ``cancel_token.is_
cancelled()`` before every step. If set, it aborts and sets the state to
``cancelled``.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID, uuid4

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    AnnouncementRequested,
    TaskCompleted,
    TaskFailed,
    TaskStarted,
    TaskStepRecorded,
)

if TYPE_CHECKING:
    from jarvis.control.cancel import CancelToken
    from jarvis.tasks.store import TaskStore


log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Protocol stubs for dependency injection
# ----------------------------------------------------------------------

class _HarnessManagerLike(Protocol):
    async def dispatch(self, name: str, task: Any) -> Any: ...


class _TTSLike(Protocol):
    async def synthesize(self, text: str, voice: str | None = None) -> Any: ...


class _ToolRegistryLike(Protocol):
    def __contains__(self, name: str) -> bool: ...
    def __getitem__(self, name: str) -> Any: ...


class _ToolExecutorLike(Protocol):
    async def execute(self, tool: Any, args: dict[str, Any], **kwargs: Any) -> Any: ...


class _AgentBrainLike(Protocol):
    """Runs a single agentic brain turn for an ``agent`` task.

    ``allowed_tools`` is the per-task tool allowlist (the toggled plugins);
    the implementation is responsible for restricting the turn to those and
    for honouring each grant's scope when pre-authorizing ask-tier actions.
    """
    async def run_task(
        self,
        *,
        prompt: str,
        allowed_tools: tuple[str, ...],
        model_tier: str,
        trace_id: UUID | None = None,
    ) -> Any: ...


class _AutoApproverLike(Protocol):
    """Pre-authorizes ask-tier tools for a task's granted plugins (Option B)."""
    def arm(self, trace_id: UUID, plugin_ids: Iterable[str], *, approved_by: str) -> None: ...
    def disarm(self, trace_id: UUID) -> None: ...


# ----------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------

class TaskRunner:
    """Executes a task spec (one invocation per ``run()`` call).

    Dependencies are optional — e.g. if no ``tts`` is passed, a
    ``SpeakAction`` task fails with a clean error, which is logged as a
    ``task_steps`` row. This lets the runner be used in tests without the
    full infrastructure.
    """

    def __init__(
        self,
        store: TaskStore,
        bus: EventBus,
        *,
        harness_manager: _HarnessManagerLike | None = None,
        tts: _TTSLike | None = None,
        tool_executor: _ToolExecutorLike | None = None,
        tool_registry: _ToolRegistryLike | Any = None,
        agent_brain: _AgentBrainLike | None = None,
        auto_approver: _AutoApproverLike | None = None,
    ) -> None:
        self._store = store
        self._bus = bus
        self._harness = harness_manager
        self._tts = tts
        self._executor = tool_executor
        self._tools = tool_registry
        self._brain = agent_brain
        self._approver = auto_approver

    # ------------------------------------------------------------------

    async def run(
        self,
        task_id: str,
        cancel_token: CancelToken | None = None,
        *,
        trigger_event: dict[str, Any] | None = None,
    ) -> None:
        """Runs a task through to completion. The terminal state is persisted in the store.

        ``trigger_event`` carries the flat fields of the bus event that fired an
        ``on_event`` task (e.g. a ``MissionCompleted`` with ``result_uri``). Its
        values are available as ``{field}`` placeholders in the action prompt/text
        and in the ``announce_on_*`` strings. ``None`` for time-based tasks.
        """
        spec = await self._store.get_spec(task_id)
        if spec is None:
            log.warning("TaskRunner: task_id %s not found in store", task_id)
            return

        # Early cancel probe (before the state change)
        if cancel_token is not None and cancel_token.is_cancelled():
            await self._store.update_state(task_id, "cancelled",
                                           error=cancel_token.reason or "cancelled")
            return

        ctx = _event_context(trigger_event)
        await self._store.update_state(task_id, "running", increment_attempts=True)
        await self._bus.publish(
            TaskStarted(task_id=task_id, source_layer="tasks.runner")
        )

        start = time.perf_counter()
        try:
            await self._execute_action(task_id, spec, cancel_token, ctx)
        except _Cancelled as exc:
            await self._store.update_state(task_id, "cancelled", error=str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            duration_ms = int((time.perf_counter() - start) * 1000)
            error_msg = f"{type(exc).__name__}: {exc}"
            await self._store.update_state(task_id, "failed", error=error_msg)
            await self._store.append_step(task_id, "log",
                                          {"event": "error", "message": error_msg})
            await self._bus.publish(
                TaskFailed(
                    task_id=task_id,
                    error=error_msg,
                    will_retry=False,
                    source_layer="tasks.runner",
                )
            )
            log.exception("Task %s failed after %dms", task_id, duration_ms)
            await self._announce(getattr(spec, "announce_on_failure", None), ctx)
            return

        duration_ms = int((time.perf_counter() - start) * 1000)
        # Recurring (`every`) tasks return to `scheduled` so they survive a
        # restart and keep firing; the scheduler re-arms the next due time.
        # One-shot triggers terminate as `completed`.
        is_recurring = getattr(spec.trigger, "type", None) == "every"
        final_state = "scheduled" if is_recurring else "completed"
        await self._store.update_state(
            task_id, final_state,
            result={"duration_ms": duration_ms},
        )
        await self._bus.publish(
            TaskCompleted(
                task_id=task_id,
                duration_ms=duration_ms,
                source_layer="tasks.runner",
            )
        )
        await self._announce(getattr(spec, "announce_on_success", None), ctx)

    async def _announce(self, template: str | None, ctx: dict[str, Any]) -> None:
        """Emit a When-Then completion announcement (``announce_on_*``).

        Interpolates the triggering event's fields into ``template`` and publishes
        it as ``AnnouncementRequested(kind="subagent")`` — the readback kind that
        survives the voice hangup gate and is mirrored to browser tabs, so it
        reaches the user after "hang up" and on a headless runtime. No-op when the
        rule set no announcement.
        """
        if not template:
            return
        text = _safe_format(template, ctx).strip()
        if not text:
            return
        await self._bus.publish(
            AnnouncementRequested(
                text=text,
                kind="subagent",
                source_layer="tasks.runner",
            )
        )

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def _execute_action(
        self,
        task_id: str,
        spec: Any,
        cancel_token: CancelToken | None,
        ctx: dict[str, Any],
    ) -> None:
        action = spec.action
        # Cancel check
        self._check_cancel(cancel_token)

        if action.kind == "harness_dispatch":
            await self._run_harness_dispatch(task_id, action, cancel_token, ctx)
        elif action.kind == "speak":
            await self._run_speak(task_id, action, cancel_token, ctx)
        elif action.kind == "tool_call":
            await self._run_tool_call(task_id, action, cancel_token)
        elif action.kind == "agent":
            await self._run_agent(task_id, action, cancel_token, ctx)
        else:  # pragma: no cover — the schema does not allow anything else
            raise RuntimeError(f"Unknown action kind: {action.kind}")

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    async def _run_harness_dispatch(
        self,
        task_id: str,
        action: Any,
        cancel_token: CancelToken | None,
        ctx: dict[str, Any],
    ) -> None:
        if self._harness is None:
            raise RuntimeError("HarnessManager not configured — harness_dispatch cannot run")
        # Local import so we don't create cycles on core/protocols in
        # test environments
        from jarvis.core.protocols import HarnessTask

        # Interpolate {field} placeholders from the triggering event so a CU goal
        # like "open {result_uri} in the browser" resolves to the finished
        # mission's artifact. No-op for time-based tasks (empty ctx).
        prompt = _safe_format(action.prompt, ctx)
        task = HarnessTask(
            prompt=prompt,
            allow_computer_use=action.allow_computer_use,
        )
        seq = await self._store.append_step(
            task_id, "action",
            {"kind": "harness_dispatch", "harness": action.harness, "prompt": prompt},
        )
        await self._bus.publish(
            TaskStepRecorded(task_id=task_id, seq=seq, kind="action",
                             source_layer="tasks.runner")
        )

        async for result in await _aiter_safe(self._harness.dispatch(action.harness, task)):
            self._check_cancel(cancel_token)
            payload: dict[str, Any] = {
                "stdout": getattr(result, "stdout", ""),
                "stderr": getattr(result, "stderr", ""),
                "exit_code": getattr(result, "exit_code", 0),
                "is_final": getattr(result, "is_final", False),
            }
            seq = await self._store.append_step(task_id, "log", payload)
            await self._bus.publish(
                TaskStepRecorded(task_id=task_id, seq=seq, kind="log",
                                 source_layer="tasks.runner")
            )
            if payload["is_final"] and int(payload["exit_code"]) != 0:
                raise RuntimeError(
                    f"Harness '{action.harness}' exit_code={payload['exit_code']}: "
                    f"{payload['stderr']!s}"
                )

    async def _run_speak(
        self,
        task_id: str,
        action: Any,
        cancel_token: CancelToken | None,
        ctx: dict[str, Any],
    ) -> None:
        text = _safe_format(action.text, ctx)
        seq = await self._store.append_step(
            task_id, "action",
            {"kind": "speak", "text": text},
        )
        await self._bus.publish(
            TaskStepRecorded(task_id=task_id, seq=seq, kind="action",
                             source_layer="tasks.runner")
        )
        if self._tts is None:
            raise RuntimeError("TTSProvider not configured — speak cannot run")

        # Audit F-AUDIT-3 (2026-04-29): filter action.text through
        # scrub_for_voice before TTS synthesizes it. Defense-in-depth: workflow
        # definitions could have brain-generated text as a speak action without
        # the skill author explicitly calling the filter.
        # Language: action has an optional .language; otherwise default "de".
        from jarvis.brain.output_filter import scrub_for_voice
        speak_lang = getattr(action, "language", None) or "de"
        scrubbed = scrub_for_voice(text, language=speak_lang)
        if scrubbed.actions:
            log.info(
                "tasks.runner.speak filter [%s]: %s (fallback=%s)",
                speak_lang, scrubbed.actions, scrubbed.fallback_used,
            )
        speak_text = scrubbed.cleaned
        if not speak_text.strip():
            log.info("tasks.runner.speak: text empty after filter — skipping TTS")
            seq = await self._store.append_step(
                task_id, "log",
                {"event": "tts_skipped", "reason": "scrub_empty"},
            )
            await self._bus.publish(
                TaskStepRecorded(task_id=task_id, seq=seq, kind="log",
                                 source_layer="tasks.runner")
            )
            return

        # TTS returns an AsyncIterator of AudioChunks. We consume the stream
        # so the provider fully drains its pipeline — audio routing is
        # outside the runner's scope.
        chunk_count = 0
        async for _chunk in await _aiter_safe(self._tts.synthesize(speak_text)):
            self._check_cancel(cancel_token)
            chunk_count += 1

        seq = await self._store.append_step(
            task_id, "log",
            {"event": "tts_done", "chunks": chunk_count},
        )
        await self._bus.publish(
            TaskStepRecorded(task_id=task_id, seq=seq, kind="log",
                             source_layer="tasks.runner")
        )

    async def _run_agent(
        self,
        task_id: str,
        action: Any,
        cancel_token: CancelToken | None,
        ctx: dict[str, Any],
    ) -> None:
        """Run an agentic brain turn: the prompt is executed with the toggled
        plugins as the tool allowlist. Each grant's scope is forwarded so the
        brain can pre-authorize unattended ask-tier actions.
        """
        if self._brain is None:
            raise RuntimeError(
                "Agent brain not configured — agent action cannot run"
            )
        prompt = _safe_format(action.prompt, ctx)
        allowed_tools = tuple(g.plugin_id for g in action.plugin_grants)
        # Plugins the user granted write/full are pre-authorized for this
        # unattended run (ask-tier actions auto-approve); read stays gated.
        auto_plugins = tuple(
            g.plugin_id for g in action.plugin_grants if g.scope in ("write", "full")
        )
        trace_id = uuid4()
        seq = await self._store.append_step(
            task_id, "action",
            {
                "kind": "agent",
                "prompt": prompt[:200],
                "tools": list(allowed_tools),
                "grants": [{"plugin_id": g.plugin_id, "scope": g.scope}
                           for g in action.plugin_grants],
                "preauthorized": list(auto_plugins),
                "model_tier": action.model_tier,
            },
        )
        await self._bus.publish(
            TaskStepRecorded(task_id=task_id, seq=seq, kind="action",
                             source_layer="tasks.runner")
        )
        self._check_cancel(cancel_token)

        if self._approver is not None:
            self._approver.arm(
                trace_id, auto_plugins, approved_by=f"scheduled-task:{task_id}"
            )
        try:
            result = await self._brain.run_task(
                prompt=prompt,
                allowed_tools=allowed_tools,
                model_tier=action.model_tier,
                trace_id=trace_id,
            )
        finally:
            if self._approver is not None:
                self._approver.disarm(trace_id)
        text = str(result).strip()
        seq = await self._store.append_step(
            task_id, "log",
            {"event": "agent_result", "text": text[:2000]},
        )
        await self._bus.publish(
            TaskStepRecorded(task_id=task_id, seq=seq, kind="log",
                             source_layer="tasks.runner")
        )
        # Delivery: speak the result at the next VAD turn-boundary. The TTS
        # pipeline scrubs it; on a muted/headless runtime this is a logged
        # no-op (cloud-first). The result also stays visible as the step above
        # in the task's detail timeline.
        if text:
            await self._bus.publish(
                AnnouncementRequested(
                    text=text,
                    # A scheduled/background task result is sub-agent output.
                    kind="subagent",
                    source_layer="tasks.runner",
                )
            )

    async def _run_tool_call(
        self,
        task_id: str,
        action: Any,
        cancel_token: CancelToken | None,
    ) -> None:
        if self._executor is None or self._tools is None:
            raise RuntimeError("ToolExecutor or tool registry not configured")
        tool = _lookup_tool(self._tools, action.tool_name)
        if tool is None:
            raise KeyError(f"Tool '{action.tool_name}' not found in registry")

        seq = await self._store.append_step(
            task_id, "action",
            {"kind": "tool_call", "tool_name": action.tool_name, "args": action.args},
        )
        await self._bus.publish(
            TaskStepRecorded(task_id=task_id, seq=seq, kind="action",
                             source_layer="tasks.runner")
        )
        self._check_cancel(cancel_token)

        result = await self._executor.execute(
            tool,
            dict(action.args),
            user_utterance=f"<task:{task_id}>",
        )
        success = bool(getattr(result, "success", False))
        seq = await self._store.append_step(
            task_id, "log",
            {
                "event": "tool_result",
                "success": success,
                "error": getattr(result, "error", None),
            },
        )
        await self._bus.publish(
            TaskStepRecorded(task_id=task_id, seq=seq, kind="log",
                             source_layer="tasks.runner")
        )
        if not success:
            raise RuntimeError(
                f"Tool '{action.tool_name}' failed: "
                f"{getattr(result, 'error', 'unknown')}"
            )

    # ------------------------------------------------------------------
    # Cancel check
    # ------------------------------------------------------------------

    @staticmethod
    def _check_cancel(cancel_token: CancelToken | None) -> None:
        if cancel_token is not None and cancel_token.is_cancelled():
            raise _Cancelled(cancel_token.reason or "cancelled")


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

class _Cancelled(RuntimeError):
    """Internal sentinel for cancel paths."""


class _SafeDict(dict):
    """``str.format_map`` backing dict that leaves unknown ``{key}`` untouched."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _event_context(trigger_event: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize the triggering event into a flat string-keyed template context.

    Drops the bus-bookkeeping fields (``trace_id``/``timestamp_ns``/
    ``source_layer``) so only meaningful event data is exposed as placeholders.
    """
    if not trigger_event:
        return {}
    drop = {"trace_id", "timestamp_ns", "source_layer"}
    return {k: v for k, v in trigger_event.items() if k not in drop}


def _safe_format(template: str, ctx: dict[str, Any]) -> str:
    """Interpolate ``{field}`` placeholders from ``ctx``; never raise.

    Unknown placeholders pass through verbatim (``_SafeDict``). A malformed
    template (stray brace, attribute/format-spec access) returns unchanged rather
    than crashing the task — the template is user-authored, not trusted input.
    """
    if not template or "{" not in template:
        return template
    try:
        return template.format_map(_SafeDict(ctx))
    except (ValueError, IndexError, KeyError, AttributeError, TypeError):
        return template


def _lookup_tool(registry: Any, name: str) -> Any:
    """Accepts either a dict-like (``__contains__``/``__getitem__``) or a
    has-get registry.
    """
    try:
        if name in registry:
            return registry[name]
    except TypeError:
        pass
    getter = getattr(registry, "get", None)
    if callable(getter):
        return getter(name)
    return None


async def _aiter_safe(maybe_coro: Any) -> Any:
    """Accepts either an ``AsyncIterator`` or a coroutine that returns one,
    and returns the iterator.

    This pattern is needed because some harness/TTS implementations declare
    ``async def dispatch(...) -> AsyncIterator`` (a coroutine that yields an
    iterator), while others declare ``def dispatch(...) -> AsyncIterator``
    directly.
    """
    import inspect
    if inspect.isawaitable(maybe_coro):
        return await maybe_coro
    return maybe_coro
