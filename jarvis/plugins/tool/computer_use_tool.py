"""``computer_use`` — router-tier tool for live-desktop control (Wave 1).

This is the first-class, clearly-described entry point the router brain uses to
drive the user's *actual* machine: open apps, click, type, scroll, drag — every
mouse-and-keyboard action. It exists because the router previously had no honest
path for desktop actions:

* ``spawn_worker`` runs a worker in an isolated git worktree — it can edit code
  and research, but it can **never** touch the user's live desktop.
* ``dispatch_to_harness`` could reach the computer-use harness, but only through
  a two-level indirection (``harness="computer-use"``) whose schema description
  talks about "generic sub-agent harnesses, code-editing, research" — so the model never
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
from jarvis.harness.computer_use_context import peek_computer_use_context
from jarvis.harness.manager import HarnessManager
from jarvis.plugins.tool.dispatch_to_harness import DispatchToHarnessTool
from jarvis.voice.action_phrases import (
    CU_TOOL_OUTCOME_LAYER,
    OUTPUT_LANGUAGE_ENV_KEY,
    action_phrase,
    cu_failure_readback,
    cu_success_readback,
    resolve_phrase_language,
)

log = logging.getLogger(__name__)


def _ctx_output_language(ctx: ExecutionContext) -> str:
    """Language for a deterministic CU readback (de/en/es).

    Prefers the turn's resolved output language stamped into ``ctx.config`` by
    the tool-use loop — that single value already honors the ``brain.reply_language``
    pin AND conversation stickiness, so a one-word English "Now" in a German
    conversation reads back German (forensic 2026-06-18). Falls back to detecting
    the user's own words only when no stamp is present (tests / minimal wiring),
    which is the historical behavior.
    """
    config = getattr(ctx, "config", None)
    stamped = config.get("output_language") if isinstance(config, dict) else None
    if stamped:
        return str(stamped)
    return resolve_phrase_language(None, ctx.user_utterance)


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
    # The mission runs in the BACKGROUND and is announced on completion. Take
    # THIS output verbatim as the final answer and skip the second brain
    # iteration, exactly like spawn_worker — otherwise the model sees the
    # internal English steering instruction below and echoes it as its own
    # assistant text (live bug 2026-06-18, session 71f2d2de). tool_use_loop
    # honours this flag at jarvis/brain/tool_use_loop.py:662-666 / 709-728.
    suppress_response: bool = True
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
        # Double-dispatch gate (CU v2): the SAME voice request occasionally
        # dispatches twice (documented in computer_use_context.py), so two
        # overlapping missions typed the same URL twice. While a mission for
        # a goal is still running, a second dispatch of the SAME goal is
        # absorbed into the running one (the user still gets ONE ack and ONE
        # completion announcement). A DIFFERENT goal still runs concurrently.
        self._active_goals: dict[str, asyncio.Task[None]] = {}

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        goal = (args.get("goal") or "").strip()
        if not goal:
            return ToolResult(success=False, output=None, error="goal missing")
        # Fresh-machine honesty (2026-07-06): computer_use stays in ROUTER_TOOLS
        # unconditionally (ADR-0011), but the CU context is only wired when
        # [computer_use].enabled AND a vision engine exist (factory.py). On an
        # unwired machine (enabled=false, or no vision engine — e.g. headless)
        # every dispatch used to die deep inside the harness with
        # "RuntimeError: ComputerUseHarness context not set". Peek the
        # singleton BEFORE dispatching so an unwired machine gets an honest,
        # actionable ToolResult instead of a crash.
        if peek_computer_use_context() is None:
            return ToolResult(
                success=False,
                output=None,
                error=(
                    "computer-use is not active on this machine: [computer_use].enabled "
                    "is false or no vision engine could be built. "
                    "Tell the user desktop control is currently OFF. There is no "
                    "Settings toggle for it (yet); it is enabled via the config value "
                    "computer_use.enabled=true — you may offer to set it through the "
                    "config self-mod path (ask-tier, requires the user's confirmation "
                    "and an app restart), or the user can run: "
                    "jarvis config set computer_use.enabled true. "
                    "Do not retry this tool in this turn."
                ),
            )
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
                    # Thread the turn's language to the verifier (proof readback).
                    "env": {OUTPUT_LANGUAGE_ENV_KEY: _ctx_output_language(ctx)},
                },
                ctx,
            )
        goal_key = " ".join(goal.split()).casefold()
        running = self._active_goals.get(goal_key)
        if running is not None and not running.done():
            log.info(
                "computer_use: duplicate dispatch of an already-running goal "
                "absorbed (%.60s)", goal,
            )
            return ToolResult(
                success=True,
                output=action_phrase("cu_dispatch_ack", _ctx_output_language(ctx)),
            )
        task = asyncio.create_task(
            self._run_background(goal, ctx), name="computer-use-tool-background",
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        self._active_goals[goal_key] = task
        task.add_done_callback(
            lambda t, key=goal_key: self._active_goals.pop(key, None),
        )
        # suppress_response=True (class attr) → tool_use_loop takes this output
        # VERBATIM and stops, so there is no second iteration to echo an
        # internal instruction. Speak the pre-existing, localized dispatch ACK
        # (AP-11: pure dict lookup, no LLM). Language resolved the same way as
        # _run_background below.
        ack_lang = _ctx_output_language(ctx)
        return ToolResult(
            success=True,
            output=action_phrase("cu_dispatch_ack", ack_lang),
        )

    async def _run_background(self, goal: str, ctx: ExecutionContext) -> None:
        """Run the mission off the brain turn and announce the outcome.

        Never raises — a background crash must not leak into the event loop.
        """
        # The outcome readback is spoken VERBATIM (no LLM re-render), so localize
        # it to the turn's language: prefer the loop-stamped turn output language
        # (honors the reply_language pin AND conversation stickiness), else detect
        # from the user's own words (live bug 2026-06-15: an English CU turn ended
        # with the German "Erledigt."; forensic 2026-06-18: a lone "Now" flipped a
        # German turn to English).
        lang = _ctx_output_language(ctx)
        text: str
        detail: str | None = None
        try:
            result = await self._dispatch.execute(
                {
                    "harness": HARNESS_NAME,
                    "prompt": goal,
                    "timeout_s": self._timeout_s,
                    # Thread the turn's resolved language to the in-harness
                    # verifier so its `proof` is spoken back in the user's
                    # language, matching the frame (live bug 2026-06-27).
                    "env": {OUTPUT_LANGUAGE_ENV_KEY: lang},
                },
                ctx,
            )
            if result.success:
                # Forward the verifier's on-screen observation (sitting in the
                # harness stdout) as the readback, so an informational request
                # ("...and check which tabs I have open") is actually answered
                # instead of a content-free "Done." (live bug 2026-06-18,
                # session 241a1984). Falls back to the plain done phrase when the
                # mission left no usable observation. Pure parse, no LLM (AP-11).
                output = getattr(result, "output", None)
                stdout = output.get("stdout") if isinstance(output, dict) else None
                text = cu_success_readback(lang, stdout=stdout)
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
                # A background Computer-Use mission reports the user's requested
                # desktop action as the turn completion. The exit-code / harness
                # reason rides ``detail`` (shown in the transcript, never spoken)
                # so a failure is debuggable.
                kind="completion",
                detail=detail,
                # Tag this so the BrainManager mirrors the outcome into the live
                # conversation history. This tool runs in its own module with no
                # _history access; without the mirror, a router/text-chat desktop
                # action would vanish from the model's next-turn context (the same
                # subsystem-confusion bug the voice fast-path fixes inline).
                source_layer=CU_TOOL_OUTCOME_LAYER,
            ))
        except Exception:  # noqa: BLE001
            log.debug("computer_use completion announce failed", exc_info=True)
