"""``computer_use`` — router-tier tool for live-desktop control (Wave 1).

This is the first-class, clearly-described entry point the router brain uses to
drive the user's *actual* machine: open apps, click, type, scroll, drag — every
mouse-and-keyboard action. It exists because the router previously had no honest
path for desktop actions:

* ``spawn_worker`` runs a worker in an isolated git worktree — it can edit code
  and research, but it can **never** touch the user's live desktop.
* ``dispatch_to_harness`` could reach the computer-use harness, but only through
  a two-level indirection (``harness="computer-use"``) whose schema description
  talks about "OpenClaw, Codex, code-editing, research" — so the model never
  picked it for desktop actions and instead refused or hallucinated a tool.

A dedicated tool with an unambiguous name + description is the strongest signal
for LLM tool selection. Execution is delegated to the canonical in-process
``computer-use`` harness (``jarvis/plugins/harness/computer_use.py`` →
``jarvis/harness/screenshot_only_loop.py``), which gates every individual action
through the ToolExecutor risk tiers (ADR-0008). The tool itself is a direct,
safe-gated action — never a spawn — so it carries no D9 recursion risk and never
belongs in a worker tool set (AP-5/AP-14).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from jarvis.brain.local_action_gate import HARNESS_NAME
from jarvis.core.bus import EventBus
from jarvis.core.events import AnnouncementRequested
from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.harness.manager import HarnessManager
from jarvis.plugins.tool.dispatch_to_harness import DispatchToHarnessTool
from jarvis.voice.action_phrases import (
    action_phrase,
    cu_failure_readback,
    resolve_phrase_language,
)

log = logging.getLogger(__name__)

#: Default ceiling for a single computer-use run, in seconds. A multi-step GUI
#: loop (open app → screenshot → click/type → verify) needs a generous budget;
#: the per-step timeout inside the loop bounds individual actions.
_DEFAULT_TIMEOUT_S = 120.0


def _cu_failure_detail(output: Any) -> tuple[int | None, str | None]:
    """Pull ``(exit_code, human_detail)`` out of a CU harness failure result.

    ``DispatchToHarnessTool`` returns ``output`` as a dict carrying
    ``exit_code`` plus ``stderr``/``stdout``; the screenshot loop writes the
    model's real ``fail`` reason into ``stderr`` (``"[cu] fail at <tag>:
    <reason>"``). Surfacing it lets the readback forward the human reason
    instead of the opaque ``error="exit N"``. Best-effort: a non-dict / missing
    field yields ``(None, None)`` and the readback degrades to the exit-code
    phrase.
    """
    if not isinstance(output, dict):
        return None, None
    raw_code = output.get("exit_code")
    exit_code: int | None
    try:
        exit_code = int(raw_code) if raw_code is not None else None
    except (TypeError, ValueError):
        exit_code = None
    stderr = str(output.get("stderr") or "").strip()
    stdout = str(output.get("stdout") or "").strip()
    detail = stderr or stdout or None
    return exit_code, detail


class ComputerUseTool:
    """Drive the live desktop via the in-process computer-use harness."""

    name: str = "computer_use"
    risk_tier: str = "monitor"
    description: str = (
        "Control THIS computer's live desktop with mouse and keyboard: open "
        "apps, click buttons, type into fields, scroll, navigate the screen, "
        "operate any GUI application. Use this for ANY on-screen / app / "
        "desktop action the user asks for (e.g. 'open a terminal', 'open Chrome "
        "and go to gmail', 'click the blue button', 'type X into Notepad', "
        "'scroll down'). Pass the user's request verbatim as 'goal'. This drives "
        "the real machine — unlike spawn_worker, which only works in an "
        "isolated code workspace and cannot touch the desktop."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": (
                    "The desktop action to perform, in the user's own words "
                    "(e.g. 'open a terminal and start the cloud config')."
                ),
            },
        },
        "required": ["goal"],
    }

    def __init__(
        self,
        *,
        bus: EventBus | None = None,
        manager: HarnessManager | None = None,
        max_output_chars: int = 4000,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        # Reuse the harness-dispatch plumbing (streaming, trimming, timeout)
        # rather than re-implementing it; we only fix the harness identity.
        self._dispatch = DispatchToHarnessTool(
            bus=bus,
            manager=manager,
            max_output_chars=max_output_chars,
        )
        self._bus = bus
        self._timeout_s = float(timeout_s)
        # Strong refs so background missions are never garbage-collected
        # mid-flight (same pattern as BrainManager._cu_background_tasks).
        self._background_tasks: set[asyncio.Task[None]] = set()

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        goal = (args.get("goal") or "").strip()
        if not goal:
            return ToolResult(success=False, output=None, error="goal missing")
        # Wave 0 (frontier-speed, 2026-06-09): with a bus wired (production),
        # the mission runs as a BACKGROUND task and the brain turn gets an
        # immediate ACK. Run inline, the mission would live inside the brain
        # turn's task — the speech stall guard's task.cancel() (or any turn
        # unwind, e.g. a TTS abort) would behead a healthy desktop mission.
        # The outcome is ALWAYS announced (AD-OE1/OE5/OE6: zero silent drops).
        # Without a bus there is nowhere to announce, so the old synchronous
        # contract stays (tests / minimal wiring).
        if self._bus is None:
            return await self._dispatch.execute(
                {
                    "harness": HARNESS_NAME,
                    "prompt": goal,
                    "timeout_s": self._timeout_s,
                },
                ctx,
            )
        task = asyncio.create_task(
            self._run_background(goal, ctx), name="computer-use-tool-background",
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return ToolResult(
            success=True,
            output=(
                "Desktop mission started in the background; the outcome will "
                "be announced to the user when it finishes. Reply with a brief "
                "acknowledgement only — do NOT claim the task is already done."
            ),
        )

    async def _run_background(self, goal: str, ctx: ExecutionContext) -> None:
        """Run the mission off the brain turn and announce the outcome.

        Never raises — a background crash must not leak into the event loop.
        """
        # The outcome readback is spoken VERBATIM (no LLM re-render), so localize
        # it to the turn's language. The tool has no reply_language pin — detect
        # from the user's own words (live bug 2026-06-15: an English CU turn ended
        # with the German "Erledigt.").
        lang = resolve_phrase_language(None, ctx.user_utterance)
        text: str
        try:
            result = await self._dispatch.execute(
                {
                    "harness": HARNESS_NAME,
                    "prompt": goal,
                    "timeout_s": self._timeout_s,
                },
                ctx,
            )
            if result.success:
                text = action_phrase("cu_done", lang)
            else:
                err = str(getattr(result, "error", "") or "")
                exit_code, detail = _cu_failure_detail(getattr(result, "output", None))
                text = cu_failure_readback(
                    lang, error=err, exit_code=exit_code, detail=detail,
                )
        except Exception as exc:  # noqa: BLE001
            log.error("computer_use background mission failed: %r", exc, exc_info=True)
            text = action_phrase("cu_crashed", lang)
        try:
            await self._bus.publish(AnnouncementRequested(
                text=text,
                priority="normal",
                language=lang,
                kind="completion",
            ))
        except Exception:  # noqa: BLE001
            log.debug("computer_use completion announce failed", exc_info=True)
