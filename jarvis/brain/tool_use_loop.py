"""Tool-use loop: coordinates brain calls + tool execution across multiple turns.

Flow:
  1. Send request to brain (with tools spec)
  2. Consume stream → aggregate text + tool-calls
  3. If `finish_reason == "tool_use"` or tool-calls present:
       a. Per tool-call: lookup + intent sanity check + ToolExecutor.execute()
       b. Append tool result as new `BrainMessage(role="tool", ...)`
       c. Budget check
       d. Back to step 1 with extended messages
  4. Otherwise: done, return text
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

from jarvis.core.protocols import Brain, BrainMessage, BrainRequest, ImageBlock, Tool
from jarvis.safety.tool_executor import ToolExecutor

from .iteration_budget import IterationBudget
from .streaming import StreamingAggregate, aggregate, aggregate_with_consumer


def _images_from_artifacts(artifacts: object) -> list[ImageBlock]:
    """Extract ImageBlocks from a tool's artifacts (Wave 2 on-demand vision).

    A vision-capable tool (e.g. the screenshot tool) returns
    ``artifacts=({"type": "image", "mime": ..., "data": <base64>},)``. Each image
    artifact becomes an ImageBlock so it can ride on a user-role message back into
    the conversation. Non-image / malformed artifacts are skipped.
    """
    blocks: list[ImageBlock] = []
    for art in artifacts or ():
        if isinstance(art, dict) and art.get("type") == "image" and art.get("data"):
            blocks.append(ImageBlock(
                mime=str(art.get("mime") or "image/jpeg"),
                data_b64=str(art["data"]),
            ))
    return blocks

log = logging.getLogger(__name__)

# Research keywords — if any of these appear in the user utterance, an
# action tool-call (CLI or MCP) is almost always wrong. The sanity check below
# blocks it without executing the tool, and gives the LLM a redirect hint.
_RESEARCH_KEYWORDS = re.compile(
    r"\b("
    r"recherchier\w*|analysier\w*|erklaer\w*|erklär\w*|"
    r"untersuch\w*|vergleich\w*|zusammenfass\w*|"
    r"research|analy[sz]e|explain|compare|summari[sz]e"
    r")\b",
    re.IGNORECASE,
)

_META_DEBUG_KEYWORDS = re.compile(
    r"\b("
    r"api\s*key|provider|brain|text[-\s]*to[-\s]*speech|tts|"
    r"transkript|transcript|log|bug|debug|fehler|fallback|"
    r"standardantwort|standardphrase|phrase|jarvis\s+sagt|"
    r"verstehst\s+du\s+was\s+ich\s+meine"
    r")\b",
    re.IGNORECASE,
)

_INSTRUCTIONAL_QUESTION_RE = re.compile(
    r"^\s*(?:"
    r"wie\s+(?:kann|koennte|könnte|muss|soll|mach|mache|macht|geht|funktioniert)\s+"
    r"|was\s+(?:ist|bedeutet|heisst|heißt)\s+"
    r"|woran\s+erkenne\s+"
    r"|warum\s+"
    r"|how\s+(?:do|can|could|should|would)\s+"
    r"|what\s+(?:is|does|are)\s+"
    r"|why\s+"
    r")",
    re.IGNORECASE,
)

# Self-identification patterns: user introduces themselves (name, salutation, pronouns).
# These utterances must NEVER trigger side-effect tools — the Curator
# (jarvis/memory/curator/) extracts the facts automatically in the background
# and merges them into USER.md. Observation 2026-05-05: Gemini-3-Flash-Preview
# interpreted "Ich heiße Personal Jarvis Maintainer" as a task and spawned a Phase-6
# worker for a manual USER.md edit (failed with exit_code=1) —
# a clear tool-choice misfire in weaker models.
_SELF_IDENTIFICATION_RE = re.compile(
    r"^\s*(?:"
    r"ich\s+(?:heisse|heiße)\s+\w+"
    r"|mein\s+name\s+(?:ist|lautet)\s+\w+"
    r"|nenn(?:e|en\s+sie)?\s+mich\s+\w+"
    r"|du\s+(?:kannst|darfst|sollst)\s+mich\s+\w+\s+nennen"
    r"|sie\s+(?:koennen|können|duerfen|dürfen|sollen)\s+mich\s+\w+\s+nennen"
    r"|meine\s+anrede\s+(?:ist|lautet)\s+\w+"
    r"|meine\s+pronomen\s+(?:ist|sind|lauten)\s+\w+"
    r"|my\s+name(?:\s+is|'s)\s+\w+"
    r"|call\s+me\s+\w+"
    r"|you\s+(?:can|may|should)\s+call\s+me\s+\w+"
    r"|i'?m\s+called\s+\w+"
    r"|i\s+am\s+called\s+\w+"
    r")",
    re.IGNORECASE,
)

_SIDE_EFFECT_TOOL_NAMES = {
    "click",
    "dispatch_to_harness",
    "dispatch_with_review",
    "hotkey",
    "move_mouse",
    "multi_spawn",
    "open_app",
    "remember",
    "run_shell",
    "spawn_worker",
    "type_text",
}


def _is_research_intent(utterance: str, intent_level: str | None = None) -> bool:
    """Heuristic: does the utterance text indicate a pure research intent?

    Primarily via regex on research verbs. ``intent_level`` from the router can
    be passed as an additional signal (currently only logged — the regex alone
    is precise enough, because intent-level 'deep' also occurs in coding/
    reasoning and is not a reliable research signal on its own).
    """
    if intent_level:
        log.debug("research-check: intent_level=%s utterance=%r", intent_level, utterance[:80])
    return bool(_RESEARCH_KEYWORDS.search(utterance or ""))


def _is_meta_debug_intent(utterance: str) -> bool:
    """User is talking about Jarvis/provider behaviour, not about a task."""
    return bool(_META_DEBUG_KEYWORDS.search(utterance or ""))


def _is_instructional_question(utterance: str) -> bool:
    """User is asking for an explanation or how-to, not for execution."""
    return bool(_INSTRUCTIONAL_QUESTION_RE.search(utterance or ""))


def _is_self_identification(utterance: str) -> bool:
    """User is introducing themselves (name, salutation, pronouns) — NOT an action request.

    Such utterances are picked up by the Curator background job and
    persisted in USER.md; a manual tool-call (run_shell, dispatch_*,
    spawn_sub_jarvis, ...) is always a wrong choice by the LLM here.
    """
    return bool(_SELF_IDENTIFICATION_RE.search(utterance or ""))


def _is_action_tool(tool: Any) -> bool:
    """True if the tool operates on a connected system (CLI or MCP).

    Recognises two patterns:
    - Name prefix ``cli_`` (CliTool instances)
    - Flag ``is_action_tool=True`` (set by MCPToolAdapter)
    Used by the sanity guard to block research intents against action tools —
    regardless of whether it is a CLI or MCP server.
    """
    name = getattr(tool, "name", "")
    if isinstance(name, str) and name.startswith("cli_"):
        return True
    return bool(getattr(tool, "is_action_tool", False))


def _is_side_effect_tool(tool: Any) -> bool:
    """True for tools that execute or mutate something locally or externally."""
    name = getattr(tool, "name", "")
    if isinstance(name, str):
        normalized = name.replace("-", "_")
        if normalized in _SIDE_EFFECT_TOOL_NAMES:
            return True
        if normalized.startswith("cli_"):
            return True
    return _is_action_tool(tool)


# STT hallucination markers: typical YouTube end-cards, ad outros,
# copyright strings that Whisper sometimes recognises as an utterance. If these
# end up in a tool argument, the brain has interpreted a hallucination as a
# command — do NOT execute the tool call.
_ARG_HALLUCINATION_RE = re.compile(
    r"\b("
    r"im\s+auftrag\s+des|mediagroup|"
    r"untertitel\s+(von|der|im\s+auftrag)|"
    r"abonnier(e|t|en)?\s+(den|meinen)\s+kanal|"
    r"thanks\s+for\s+watching|please\s+subscribe|"
    r"copyright\s+\d{4}|all\s+rights\s+reserved"
    r")\b",
    re.IGNORECASE,
)

# Per-tool maximum arg length. Blocks hallucination args in fields that are
# expected to be short. ``type_text`` and ``run_shell`` are intentionally
# uncapped — legitimately long text/commands can appear there.
_ARG_MAXLEN: dict[str, tuple[str, int]] = {
    "open_app":    ("app_name", 80),
    "open-app":    ("app_name", 80),
    "search_web":  ("query", 200),
    "search-web":  ("query", 200),
}


def _is_stt_hallucinated(tool_name: str, args: Any) -> tuple[bool, str]:
    """Checks whether the tool args look like an STT hallucination.

    Returns ``(blocked, reason)``. ``blocked=True`` means the tool call
    should NOT be executed; ``reason`` is fed back to the LLM as an error
    so it switches to asking the user for clarification.
    """
    if not isinstance(args, dict):
        return False, ""

    # Per-tool max-length check on the primary field
    field_spec = _ARG_MAXLEN.get(tool_name)
    if field_spec:
        fname, maxlen = field_spec
        val = str(args.get(fname, ""))
        if len(val) > maxlen:
            return True, f"Arg '{fname}' ist {len(val)} chars (erlaubt: {maxlen})"

    # Generic ad/outro marker check across all string args
    for k, v in args.items():
        if isinstance(v, str) and _ARG_HALLUCINATION_RE.search(v):
            return True, f"Arg '{k}' enthaelt Werbe-/Outro-Marker"

    return False, ""


class ToolUseLoop:
    """Loop until no more tool calls are pending or the budget is exhausted."""

    def __init__(
        self,
        brain: Brain,
        tools: dict[str, Tool],
        executor: ToolExecutor,
        *,
        system_prompt: str | None = None,
        budget: IterationBudget | None = None,
        max_tokens: int = 8192,
    ) -> None:
        self._brain = brain
        self._tools = tools
        self._executor = executor
        self._system_prompt = system_prompt
        self._budget = budget or IterationBudget()
        # Per-response output ceiling forwarded onto every BrainRequest this
        # loop issues. Safety ceiling, not a target (see BrainConfig.max_tokens).
        self._max_tokens = max_tokens

    def _tool_schemas(self) -> list[dict[str, Any]]:
        """Schemas in Anthropic-compatible format (providers normalise)."""
        return [
            {
                "name": tool.name,
                "description": getattr(tool, "description", ""),
                "input_schema": tool.schema,
            }
            for tool in self._tools.values()
        ]

    async def run(
        self,
        messages: list[BrainMessage],
        *,
        trace_id: UUID | None = None,
        user_utterance: str = "",
        intent_level: str | None = None,
        text_consumer: Callable[[str], None] | None = None,
        ack_emitter: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        on_progress: Callable[[], None] | None = None,
    ) -> StreamingAggregate:
        """Executes the complete loop and returns the final aggregate.

        ``intent_level`` comes from the router (``fast``/``deep``/``code``) and is
        currently forwarded to the research heuristic; in the future it can be used
        for further tier-aware logic (e.g. tool-visibility filtering).

        ``text_consumer`` (latency-sprint-1): called synchronously per brain text chunk —
        enables sentence streaming in the speech pipeline. When ``None`` (default),
        the loop behaves identically to the previous implementation.
        Pre-tool-use texts are also delivered; the persona prompt prohibits
        filler openers anyway, so this is non-critical.

        ``on_progress`` (stall-timeout signal, live bug 2026-06-01): called
        synchronously at every "the brain is actively working" boundary — once
        per completed model round and once per executed tool. A vision/tool turn
        streams little or no text, so the speech pipeline cannot tell "still
        working" from "stalled" by watching text chunks alone; these pings let
        its *no-progress* deadline reset across the long silent gaps (model
        thinking + tool execution) instead of guillotining a working turn at a
        hard wall-clock cap. ``None`` (default) is a no-op. Callback exceptions
        are swallowed so a buggy consumer can never break tool execution.

        ``ack_emitter`` (perceived-latency pattern): if provided, awaited
        exactly once on the first iteration that has tool calls scheduled,
        with the first tool's name + input. The caller decides whether to
        publish an ``AnnouncementRequested`` (skip-list / template selection
        live in the caller). Subsequent iterations of the same turn never
        re-emit — multi-step tool plans get a single ack at the start, not
        chatter at every step. Emitter exceptions are logged but do not
        block tool execution.
        """
        tid = trace_id or uuid4()
        current_messages = list(messages)
        tools_payload = self._tool_schemas()
        final_agg = StreamingAggregate()
        ack_attempted = False

        def _progress() -> None:
            # Stall-timeout heartbeat (see ``on_progress`` in the docstring).
            # Swallow everything: a progress consumer must never break the loop.
            if on_progress is not None:
                try:
                    on_progress()
                except Exception:  # noqa: BLE001
                    log.debug("on_progress callback raised (ignored)", exc_info=True)

        while True:
            req = BrainRequest(
                messages=tuple(current_messages),
                tools=tuple(tools_payload),
                system=self._system_prompt,
                max_tokens=self._max_tokens,
                stream=True,
            )
            stream = self._brain.complete(req)
            if text_consumer is not None:
                agg = await aggregate_with_consumer(stream, text_consumer)
            else:
                agg = await aggregate(stream)
            # A model round finished — the brain is alive and working. Reset the
            # pipeline's no-progress deadline before the (possibly long) tool
            # execution + next round so a slow-but-working turn is not cut off.
            _progress()

            # Accumulate final text
            if agg.text:
                final_agg.text += agg.text
            final_agg.finish_reason = agg.finish_reason
            for k, v in agg.usage.items():
                final_agg.usage[k] = final_agg.usage.get(k, 0) + int(v)

            # Budget tracking
            self._budget.record_turn(
                tokens_in=agg.usage.get("input_tokens", 0),
                tokens_out=agg.usage.get("output_tokens", 0),
            )

            # No tool calls → done
            if not agg.tool_calls:
                break

            # Budget exhausted → abort
            if self._budget.exceeded():
                log.warning("IterationBudget erschöpft: %s", self._budget.snapshot())
                final_agg.finish_reason = "budget_exceeded"
                break

            # Pre-execution acknowledgment (perceived-latency pattern). Fires
            # exactly once per turn, on the first iteration that has tool
            # calls. Done after both early-exit checks so we never ack for a
            # tool that won't actually run. The emitter is responsible for
            # skip-list filtering and template selection.
            if ack_emitter is not None and not ack_attempted:
                ack_attempted = True
                first_call = agg.tool_calls[0]
                first_name = first_call.get("name", "") or ""
                first_input = first_call.get("input", {}) or {}
                if not isinstance(first_input, dict):
                    first_input = {}
                try:
                    await ack_emitter(first_name, first_input)
                except Exception as exc:  # noqa: BLE001 — emitter must never block tool execution
                    log.warning("ack_emitter failed: %s", exc)

            # Add assistant turn with tool-calls to the message history
            # (for providers that expect role=assistant with tool_calls)
            assistant_content: list[dict[str, Any]] = []
            if agg.text:
                assistant_content.append({"type": "text", "text": agg.text})
            for tc in agg.tool_calls:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc.get("id", f"call_{uuid4().hex[:8]}"),
                    "name": tc.get("name", ""),
                    "input": tc.get("input", {}),
                })
            current_messages.append(BrainMessage(
                role="assistant",
                content=assistant_content if assistant_content else agg.text,
            ))

            # Execute tools
            suppress_output: str | None = None
            for tc in agg.tool_calls:
                tool_name = tc.get("name", "")
                tool_args = tc.get("input", {})
                call_id = tc.get("id", "")
                final_agg.tool_calls.append(dict(tc))
                # Wave 2: reset per tool-call so a guard/refusal branch on a
                # later iteration cannot reuse a stale executor result when we
                # check for image artifacts below.
                result = None

                tool = self._tools.get(tool_name)
                stt_blocked, stt_reason = (
                    _is_stt_hallucinated(tool_name, tool_args)
                    if tool is not None else (False, "")
                )
                if (
                    tool is not None
                    and _is_instructional_question(user_utterance)
                    and _is_side_effect_tool(tool)
                ):
                    log.info(
                        "tool_use_loop: Side-Effect-Tool '%s' fuer How-to-Frage blockiert",
                        tool_name,
                    )
                    tool_result_payload = {
                        "success": False,
                        "output": None,
                        "error": (
                            "Tool nicht ausgefuehrt: der User stellt eine How-to- "
                            "oder Erklaerfrage. Antworte direkt mit der passenden "
                            "kurzen Anleitung. Fuehre keine App-, Shell-, Harness- "
                            "oder Computer-Use-Aktion aus."
                        ),
                    }
                elif (
                    tool is not None
                    and _is_self_identification(user_utterance)
                    and _is_side_effect_tool(tool)
                ):
                    log.info(
                        "tool_use_loop: Side-Effect-Tool '%s' fuer Self-Identification blockiert",
                        tool_name,
                    )
                    tool_result_payload = {
                        "success": False,
                        "output": None,
                        "error": (
                            "Tool nicht ausgefuehrt: der User stellt sich vor "
                            "(Name, Anrede oder Pronomen). Antworte mit einer "
                            "kurzen, freundlichen Begruessung (1-2 Saetze, max). "
                            "Der Curator extrahiert die Facts automatisch im "
                            "Hintergrund und persistiert sie in USER.md — Du "
                            "musst USER.md NICHT manuell editieren, keinen "
                            "Worker spawnen, keine Shell aufrufen."
                        ),
                    }
                elif tool is None:
                    # AD-OE6 anti-silence: the model named a tool that is not in
                    # the router tool set (e.g. the prompt advertised a tool that
                    # was missing from ROUTER_TOOLS). Feed the error back AND set
                    # a spoken fallback so the turn never ends in silence — the
                    # historical "action command -> empty -> user hears nothing"
                    # failure (BUG-007/016/020/028 class). With the ROUTER_TOOLS
                    # fix this should be rare, but never silent again.
                    log.warning(
                        "tool_use_loop: Tool '%s' nicht im Router-Tool-Set — "
                        "Anti-Stille-Fallback statt leerer Antwort", tool_name,
                    )
                    tool_result_payload = {"error": f"Tool '{tool_name}' nicht verfügbar"}
                    suppress_output = (
                        "Das kann ich gerade nicht ausfuehren — mir fehlt dafuer "
                        "das passende Werkzeug."
                    )
                elif tool_name == "spawn_worker" and _is_meta_debug_intent(user_utterance):
                    log.info(
                        "tool_use_loop: spawn_worker fuer Meta-/Debug-Utterance blockiert"
                    )
                    # Asking the LLM to "antworte direkt und konkret" via a
                    # tool_result error message turned out to be unreliable —
                    # Gemini Flash regularly ignores the instruction, the
                    # outer loop never produces a text chunk, and Brain-Stream
                    # times out after 40s with nothing in `final_agg.text`.
                    # The user then hears silence ("hört zu, denkt, hört
                    # wieder zu, spricht gar nicht").
                    #
                    # The Meta-Debug verdict is already deterministic and
                    # high-precision (intent classifier matched the utterance
                    # against a curated keyword set). Short-circuit the loop
                    # with a neutral acknowledgement so the user always hears
                    # *something*, and let the LLM weigh in next turn instead
                    # of stalling this one.
                    suppress_output = (
                        "Verstanden, ich notiere das Feedback. "
                        "Soll ich es genauer untersuchen?"
                    )
                    tool_result_payload = {
                        "success": False,
                        "output": None,
                        "error": (
                            "spawn_worker wurde nicht ausgefuehrt: der User "
                            "spricht ueber Jarvis-/Provider-/Transcript-Verhalten. "
                            "Antworte direkt und konkret; keine Delegation, keine "
                            "Bestaetigungsphrase."
                        ),
                    }
                elif stt_blocked:
                    # Arg sanity guard: the tool args look like a Whisper
                    # hallucination (ad outro, copyright string, overly long app name).
                    # Do NOT execute the tool; the LLM gets a structured error
                    # and should ask the user again.
                    log.info(
                        "tool_use_loop: STT-Halluzination-Guard blockiert %s — %s",
                        tool_name, stt_reason,
                    )
                    tool_result_payload = {
                        "success": False,
                        "output": None,
                        "error": (
                            f"Tool '{tool_name}' NICHT ausgefuehrt: {stt_reason}. "
                            f"Wahrscheinlich STT-Misshearing. Antworte dem User "
                            f"mit einer kurzen Rueckfrage (max 1 Satz) statt das "
                            f"Tool erneut mit dem selben Wert zu rufen."
                        ),
                    }
                elif (
                    tool is not None
                    and _is_action_tool(tool)
                    and _is_research_intent(user_utterance, intent_level)
                ):
                    # Intent sanity guard: the user used a research keyword but
                    # the LLM still wants to fire an action tool (CLI or MCP)
                    # against the connected system. This is almost always a wrong
                    # choice (user wants info *about* Supabase, not to query their DB).
                    # Instead of executing → tool result with redirect hint;
                    # the LLM corrects itself in the next turn.
                    log.info(
                        "tool_use_loop: Action-Tool '%s' bei Research-Intent blockiert "
                        "(intent_level=%s) — LLM wird auf search_web umgeleitet",
                        tool_name, intent_level,
                    )
                    tool_result_payload = {
                        "success": False,
                        "output": None,
                        "error": (
                            f"Tool '{tool_name}' wurde nicht ausgefuehrt: der Utterance "
                            f"'{user_utterance[:120]}' klingt nach Recherche (Info *ueber* "
                            f"ein Thema), nicht nach Aktion auf dem verbundenen System. "
                            f"Nutze stattdessen search_web fuer allgemeine Recherche. "
                            f"Action-Tools (cli_* und MCP) sind nur fuer gezielte "
                            f"Operationen auf Deinen Ressourcen gedacht, z.B. "
                            f"'liste meine Projekte'."
                        ),
                    }
                else:
                    result = await self._executor.execute(
                        tool, tool_args,
                        user_utterance=user_utterance,
                        trace_id=tid,
                    )
                    tool_result_payload = {
                        "success": result.success,
                        "output": result.output,
                        "error": result.error,
                    }
                    # Record a tool that ACTUALLY ran (success only) so consumers
                    # can tell a real side effect from a merely-requested or
                    # guard-blocked call. The guard branches above never reach
                    # here, so a blocked computer_use / open_app is correctly
                    # absent — this is what keeps the voice pipeline from speaking
                    # "Erledigt." for a desktop action that did not happen
                    # (2026-06-09).
                    if result.success:
                        final_agg.executed_tool_names.add(tool_name)
                    # suppress_response=True: tool provides its own final response,
                    # the second brain iteration is skipped (fix for 1-3 s stall
                    # after fire-and-forget tool calls like spawn_worker).
                    if (
                        getattr(tool, "suppress_response", False)
                        and result.success
                    ):
                        suppress_output = result.output

                # Append tool result as a new message
                current_messages.append(BrainMessage(
                    role="tool",
                    content=[{
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "content": json.dumps(tool_result_payload, ensure_ascii=False, default=str),
                    }],
                    tool_call_id=call_id,
                    name=tool_name,
                ))
                # A tool just finished — another active-work boundary. Reset the
                # no-progress deadline so a slow tool (a vision capture, an MCP
                # round-trip) does not count as a stall.
                _progress()

                # Wave 2 (vision-on-demand): if the tool returned image
                # artifact(s), feed them back as a user-role message so a
                # vision-capable provider can actually see them on the next
                # iteration. A tool-role message becomes a Gemini functionResponse
                # (no image support), so the image MUST ride on a user message.
                # Gated on a real execution — the refusal/guard branches above
                # leave result=None, so this only fires for tools that ran.
                if result is not None:
                    _img_blocks = _images_from_artifacts(
                        getattr(result, "artifacts", ()) or ()
                    )
                    if _img_blocks:
                        current_messages.append(BrainMessage(
                            role="user",
                            content="(Tool screenshot — describe or use it as needed.)",
                            images=tuple(_img_blocks),
                        ))

            # Budget check after execution
            if self._budget.exceeded():
                final_agg.finish_reason = "budget_exceeded"
                break

            # Fire-and-forget: if any tool has suppress_response=True,
            # skip the second brain iteration and return immediately.
            if suppress_output is not None:
                final_agg.text = suppress_output
                final_agg.finish_reason = "suppress_response"
                # Feed the suppress text into the sentence-stream consumer
                # (when present) so the TTS path actually speaks it. Without
                # this hook, `final_agg.text` is set but the speech-pipeline
                # never received any text-chunk, so the Brain-Stream log
                # line printed `🤖 Jarvis [de] (streamed): ` with nothing
                # after — the user heard silence even though we had an ACK
                # phrase ready. Verified live 2026-05-13: Voice-Spawn-ACK
                # was suppressed for exactly this reason.
                if text_consumer is not None and suppress_output:
                    try:
                        text_consumer(suppress_output)
                    except Exception:  # noqa: BLE001
                        # Consumer errors must not block the final return —
                        # speech-pipeline bugs are surfaced upstream via
                        # AnnouncementRequested events.
                        pass
                break

        return final_agg
