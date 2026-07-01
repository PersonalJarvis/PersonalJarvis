"""WorkflowRunner — executes WorkflowDefs step by step.

Design principles:

1. **Sequential**, not parallel. Most user workflows are
   "do this, then that, then say the result" — DAG parallelism only
   comes in v2, once someone actually needs it.
2. **All dependencies are optional.** If the BrainManager is missing,
   the ``brain_prompt`` step raises a clean error, but the runner
   doesn't crash. This lets tests run without the full infrastructure.
3. **Template variables** are expanded via substring replace before
   execution. Scope: ``{{prev.output}}``, ``{{step_N.output}}``,
   ``{{input.<key>}}`` — everything else stays literal.
4. **Events** go to the bus (``WorkflowStarted``, ``WorkflowStepStarted``,
   ``WorkflowStepCompleted``, ``WorkflowCompleted``). The UI listens for
   these and renders live updates.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any, Protocol

from jarvis.core.bus import EventBus
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.core.events import (
    AnnouncementRequested,
    WorkflowCompleted,
    WorkflowStarted,
    WorkflowStepCompleted,
    WorkflowStepStarted,
)

from .schema import (
    WorkflowDef,
    step_display_label,
)

if TYPE_CHECKING:
    from .store import WorkflowStore


log = logging.getLogger(__name__)

_PREVIEW_MAX = 240


# ----------------------------------------------------------------------
# Protocol stubs for dependency injection
# ----------------------------------------------------------------------

class _BrainLike(Protocol):
    async def __call__(self, prompt: str) -> str: ...


class _HarnessManagerLike(Protocol):
    async def dispatch(self, name: str, task: Any) -> Any: ...


class _ToolExecutorLike(Protocol):
    async def execute(self, tool: Any, args: dict[str, Any], **kwargs: Any) -> Any: ...


# ----------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------

class WorkflowRunner:
    """Executor for WorkflowDefs — one instance per app, many parallel runs."""

    def __init__(
        self,
        store: WorkflowStore,
        bus: EventBus,
        *,
        brain: _BrainLike | None = None,
        harness_manager: _HarnessManagerLike | None = None,
        tool_registry: Any = None,
        tool_executor: _ToolExecutorLike | None = None,
    ) -> None:
        self._store = store
        self._bus = bus
        self._brain = brain
        self._harness = harness_manager
        self._tools = tool_registry
        self._executor = tool_executor

    # ------------------------------------------------------------------
    # Runtime dependency swap (the BrainManager is only built after the
    # workflow store; we hot-swap it in afterward).
    # ------------------------------------------------------------------

    def attach_brain(self, brain: _BrainLike) -> None:
        self._brain = brain

    def attach_harness_manager(self, hm: _HarnessManagerLike) -> None:
        self._harness = hm

    def attach_tools(self, registry: Any, executor: _ToolExecutorLike) -> None:
        self._tools = registry
        self._executor = executor

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def trigger(
        self,
        workflow_id: str,
        *,
        trigger_reason: str = "manual",
        input_data: dict[str, Any] | None = None,
    ) -> str:
        """Starts a workflow run fire-and-forget. Returns the run ID.

        The actual run runs as an ``asyncio.create_task`` — the caller
        (REST route, cron scheduler) can continue immediately and track
        the run via ``run_id`` polling/live events.
        """
        wf = await self._store.get_def(workflow_id)
        if wf is None:
            raise KeyError(f"Workflow {workflow_id} not found")
        run_id = await self._store.create_run(
            workflow_id,
            trigger=trigger_reason,
            input_data=input_data,
        )
        asyncio.create_task(
            self._run_workflow(wf, run_id, trigger_reason, input_data or {}),
            name=f"workflow-{wf.name}-{run_id[:8]}",
        )
        return run_id

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _run_workflow(
        self,
        wf: WorkflowDef,
        run_id: str,
        trigger_reason: str,
        input_data: dict[str, Any],
    ) -> None:
        start = time.perf_counter()
        await self._store.update_run_state(run_id, "running")
        await self._bus.publish(
            WorkflowStarted(
                workflow_id=str(wf.id),
                run_id=run_id,
                trigger=trigger_reason,
                title=wf.name,
                source_layer="workflows.runner",
            )
        )

        step_outputs: dict[str, str] = {}
        success = True
        error_msg: str | None = None

        for idx, step in enumerate(wf.steps, start=1):
            label = step_display_label(step)
            await self._store.start_step(run_id, idx, step.kind, label)
            await self._bus.publish(
                WorkflowStepStarted(
                    run_id=run_id,
                    step_index=idx,
                    kind=step.kind,
                    label=label,
                    source_layer="workflows.runner",
                )
            )

            step_start = time.perf_counter()
            try:
                output = await self._execute_step(step, step_outputs, input_data)
            except Exception as exc:  # noqa: BLE001
                duration_ms = int((time.perf_counter() - step_start) * 1000)
                error_text = f"{type(exc).__name__}: {exc}"
                await self._store.finish_step(
                    run_id, idx, success=False, error=error_text,
                )
                await self._bus.publish(
                    WorkflowStepCompleted(
                        run_id=run_id,
                        step_index=idx,
                        success=False,
                        duration_ms=duration_ms,
                        error=error_text,
                        source_layer="workflows.runner",
                    )
                )
                success = False
                error_msg = error_text
                log.warning("Workflow %s step %d failed: %s",
                            wf.name, idx, error_text)
                break

            duration_ms = int((time.perf_counter() - step_start) * 1000)
            step_outputs[f"step_{idx}"] = output
            step_outputs["prev"] = output
            await self._store.finish_step(
                run_id, idx, success=True, output=output,
            )
            await self._bus.publish(
                WorkflowStepCompleted(
                    run_id=run_id,
                    step_index=idx,
                    success=True,
                    duration_ms=duration_ms,
                    output_preview=output[:_PREVIEW_MAX],
                    source_layer="workflows.runner",
                )
            )

        duration_ms = int((time.perf_counter() - start) * 1000)
        final_state = "completed" if success else "failed"
        await self._store.update_run_state(run_id, final_state, error=error_msg)
        await self._store.set_last_run(str(wf.id), time.time_ns(), final_state)
        await self._bus.publish(
            WorkflowCompleted(
                workflow_id=str(wf.id),
                run_id=run_id,
                success=success,
                duration_ms=duration_ms,
                error=error_msg,
                source_layer="workflows.runner",
            )
        )

    # ------------------------------------------------------------------
    # Step dispatch
    # ------------------------------------------------------------------

    async def _execute_step(
        self,
        step: Any,
        step_outputs: dict[str, str],
        input_data: dict[str, Any],
    ) -> str:
        if step.kind == "brain_prompt":
            return await self._run_brain_prompt(step, step_outputs, input_data)
        if step.kind == "harness_dispatch":
            return await self._run_harness(step, step_outputs, input_data)
        if step.kind == "speak":
            return await self._run_speak(step, step_outputs, input_data)
        if step.kind == "tool_call":
            return await self._run_tool_call(step, step_outputs, input_data)
        if step.kind == "shell_cmd":
            return await self._run_shell_cmd(step, step_outputs, input_data)
        if step.kind == "telegram_send":
            return await self._run_telegram_send(step, step_outputs, input_data)
        raise RuntimeError(f"Unknown step kind: {step.kind}")

    async def _run_brain_prompt(
        self, step: Any, outputs: dict[str, str], input_data: dict[str, Any],
    ) -> str:
        if self._brain is None:
            raise RuntimeError(
                "No brain available — workflow requires a BrainManager"
            )
        prompt = _expand_template(step.prompt, outputs, input_data)
        reply = await _maybe_await_brain(self._brain, prompt)
        # Cap at the user-defined max_output_chars
        cap = getattr(step, "max_output_chars", 2000)
        if len(reply) > cap:
            reply = reply[:cap] + "…"
        return reply

    async def _run_harness(
        self, step: Any, outputs: dict[str, str], input_data: dict[str, Any],
    ) -> str:
        if self._harness is None:
            raise RuntimeError("No HarnessManager available")
        from jarvis.core.protocols import HarnessTask

        prompt = _expand_template(step.prompt, outputs, input_data)
        task = HarnessTask(
            prompt=prompt,
            allow_computer_use=step.allow_computer_use,
        )
        stdout_chunks: list[str] = []
        final_exit = 0
        gen = self._harness.dispatch(step.harness, task)
        if asyncio.iscoroutine(gen):
            gen = await gen
        async for result in gen:
            out = getattr(result, "stdout", "") or ""
            if out:
                stdout_chunks.append(out)
            if getattr(result, "is_final", False):
                final_exit = int(getattr(result, "exit_code", 0))
                break
        full = "".join(stdout_chunks).strip()
        if final_exit != 0:
            raise RuntimeError(
                f"Harness '{step.harness}' exit_code={final_exit}: {full[-400:]}"
            )
        return full

    async def _run_speak(
        self, step: Any, outputs: dict[str, str], input_data: dict[str, Any],
    ) -> str:
        text = _expand_template(step.text, outputs, input_data)
        await self._bus.publish(
            AnnouncementRequested(
                text=text,
                priority=step.priority,
                language=step.language,
                source_layer="workflows.runner",
            )
        )
        return text

    async def _run_tool_call(
        self, step: Any, outputs: dict[str, str], input_data: dict[str, Any],
    ) -> str:
        if self._tools is None or self._executor is None:
            raise RuntimeError("Tool registry/executor not available")
        tool = None
        try:
            if step.tool_name in self._tools:
                tool = self._tools[step.tool_name]
        except TypeError:
            pass
        if tool is None:
            getter = getattr(self._tools, "get", None)
            if callable(getter):
                tool = getter(step.tool_name)
        if tool is None:
            raise KeyError(f"Tool '{step.tool_name}' not in registry")

        expanded_args: dict[str, Any] = {}
        for k, v in (step.args or {}).items():
            if isinstance(v, str):
                expanded_args[k] = _expand_template(v, outputs, input_data)
            else:
                expanded_args[k] = v

        result = await self._executor.execute(
            tool, expanded_args, user_utterance=f"<workflow:{step.tool_name}>",
        )
        success = bool(getattr(result, "success", False))
        if not success:
            err = getattr(result, "error", None) or "tool call failed"
            raise RuntimeError(err)
        payload = getattr(result, "output", None)
        if payload is None:
            return "ok"
        if isinstance(payload, (dict, list)):
            return json.dumps(payload, ensure_ascii=False)
        return str(payload)

    async def _run_shell_cmd(
        self, step: Any, outputs: dict[str, str], input_data: dict[str, Any],
    ) -> str:
        """Starts a subprocess with a timeout + output cap.

        Design note: we shell-split with ``shlex.split`` and use
        ``create_subprocess_exec`` (``shell=False``). That means pipes/
        redirects (``|``, ``>``) don't work out of the box — anyone who
        needs them uses ``cmd=powershell -c "..."`` or splits into
        two shell_cmd steps with a template variable.
        """
        import shlex

        cmd_expanded = _expand_template(step.command, outputs, input_data)
        try:
            # posix=False so Windows paths with backslashes aren't
            # interpreted as escape sequences. Downside: quotes stay
            # part of the token — we strip them by hand, so
            # '"C:\\Program Files\\app.exe"' becomes 'C:\\Program Files\\app.exe'.
            argv = shlex.split(cmd_expanded, posix=False)
        except ValueError as exc:
            raise RuntimeError(f"Shell command parsing failed: {exc}") from exc
        argv = [a[1:-1] if len(a) >= 2 and a[0] == a[-1] and a[0] in ('"', "'")
                else a for a in argv]
        if not argv:
            raise RuntimeError("Empty shell command")

        cwd = step.cwd or None

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=step.timeout_s,
            )
        except TimeoutError as exc:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            raise RuntimeError(
                f"Shell command timed out after {step.timeout_s}s: {cmd_expanded[:80]}"
            ) from exc

        stdout = (stdout_b or b"").decode("utf-8", errors="replace")
        stderr = (stderr_b or b"").decode("utf-8", errors="replace")
        if proc.returncode != 0:
            raise RuntimeError(
                f"Shell command exit_code={proc.returncode}: "
                f"{stderr[-400:] or stdout[-400:]}"
            )
        cap = step.max_output_chars
        output = stdout.strip()
        if len(output) > cap:
            output = output[:cap] + "…"
        return output

    async def _run_telegram_send(
        self, step: Any, outputs: dict[str, str], input_data: dict[str, Any],
    ) -> str:
        """POST to the Telegram Bot API. Token + default chat ID come from config."""
        from jarvis.core.config import get_secret, load_config

        token = get_secret("telegram_bot_token", "TELEGRAM_BOT_TOKEN")
        if not token:
            raise RuntimeError(
                "Telegram bot token not set — "
                "configure ENV TELEGRAM_BOT_TOKEN or the credential manager "
                "key 'telegram_bot_token'. "
                "Create a bot via @BotFather."
            )

        # Chat ID: step > config > error
        chat_id = step.chat_id.strip() if step.chat_id else ""
        parse_mode = "Markdown"
        if not chat_id:
            try:
                cfg = load_config()
                chat_id = cfg.integrations.telegram.chat_id.strip()
                parse_mode = cfg.integrations.telegram.parse_mode or "Markdown"
            except Exception:  # noqa: BLE001
                pass
        if not chat_id:
            raise RuntimeError(
                "Telegram chat ID not set — either specify 'chat_id' in "
                "the step or set '[integrations.telegram].chat_id' "
                "in jarvis.toml."
            )

        text = _expand_template(step.text, outputs, input_data)
        # Telegram max is 4096 characters — truncate instead of erroring.
        if len(text) > 4096:
            text = text[:4090] + "\n…"

        import httpx

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                r = await client.post(url, json=payload)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Telegram request failed: {exc}") from exc

        if r.status_code >= 400:
            # Telegram responds with ``{"ok": false, "description": "..."}``
            try:
                detail = r.json().get("description") or r.text[:200]
            except Exception:  # noqa: BLE001
                detail = r.text[:200]
            raise RuntimeError(
                f"Telegram HTTP {r.status_code}: {detail}"
            )
        return f"sent to chat_id={chat_id} ({len(text)} characters)"


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

_TEMPLATE_RE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


def _expand_template(
    s: str, outputs: dict[str, str], input_data: dict[str, Any],
) -> str:
    """Expands ``{{prev.output}}`` / ``{{step_N.output}}`` / ``{{input.X}}``.

    Unknown placeholders stay literal — so the user quickly notices
    during debugging if they made a typo.
    """
    def repl(m: re.Match[str]) -> str:
        token = m.group(1).strip()
        if token.startswith("input."):
            key = token[len("input."):]
            v = input_data.get(key, "")
            return str(v)
        if "." in token:
            ref, field = token.split(".", 1)
            if field == "output":
                return outputs.get(ref, m.group(0))
        return m.group(0)

    return _TEMPLATE_RE.sub(repl, s)


async def _maybe_await_brain(brain: Any, prompt: str) -> str:
    """The brain can be: callable(str) -> Awaitable[str], or an object with
    ``__call__``, or respond(thread_id, text, store)-like. We try the
    callable pattern (BrainManager) and fail gracefully.
    """
    if callable(brain):
        try:
            maybe = brain(prompt)
            if asyncio.iscoroutine(maybe):
                result = await maybe
            else:
                result = maybe
            return str(result or "")
        except TypeError:
            pass
    raise RuntimeError(
        "Brain is not directly callable — only BrainManager-compatible "
        "providers are supported"
    )
