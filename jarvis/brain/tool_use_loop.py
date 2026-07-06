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
from jarvis.core.turn_language import resolve_output_language, resolve_turn_language
from jarvis.safety.tool_executor import VOICE_CONFIRM_SENTINEL, ToolExecutor

from .iteration_budget import IterationBudget
from .streaming import StreamingAggregate, aggregate, aggregate_with_consumer

# Central backstop for tool-output bloat: no tool result may exceed this many
# chars in the tool-role message fed back to the brain. Individual tools should
# self-slim (a raw Gmail ``format=full`` message is ~23k chars of headers +
# base64), but this cap guarantees a bound for EVERY tool — present and future —
# so one verbose provider can never flood the context, slow the turn and crowd
# out the answer (live bug 2026-07-01). The event-bus/DB preview cap
# (``safe_preview``) is a separate path and never touched what the model saw.
_MAX_TOOL_RESULT_CHARS = 8000


def _cap_tool_result_json(serialized: str) -> str:
    """Truncate an over-long serialized tool result, leaving an honest marker so
    the model knows the payload was clipped (rather than silently ending)."""
    if len(serialized) <= _MAX_TOOL_RESULT_CHARS:
        return serialized
    kept = serialized[:_MAX_TOOL_RESULT_CHARS]
    return (
        f"{kept}… [truncated: tool output was {len(serialized)} chars, "
        f"capped at {_MAX_TOOL_RESULT_CHARS}]"
    )


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
    r"recherchier\w*|analysier\w*|erklaer\w*|erklär\w*|"  # i18n-allow: German input-matching data (research-intent classifier)
    r"untersuch\w*|vergleich\w*|zusammenfass\w*|"
    r"research|analy[sz]e|explain|compare|summari[sz]e"
    r")\b",
    re.IGNORECASE,
)

_META_DEBUG_KEYWORDS = re.compile(
    r"\b("
    r"api\s*key|provider|brain|text[-\s]*to[-\s]*speech|tts|"
    r"transkript|transcript|log|bug|debug|fehler|fallback|"  # i18n-allow: German input-matching data (meta/debug-intent classifier)
    r"standardantwort|standardphrase|phrase|jarvis\s+sagt|"
    r"verstehst\s+du\s+was\s+ich\s+meine"
    r")\b",
    re.IGNORECASE,
)

_INSTRUCTIONAL_QUESTION_RE = re.compile(
    r"^\s*(?:"
    r"wie\s+(?:kann|koennte|könnte|muss|soll|mach|mache|macht|geht|funktioniert)\s+"  # i18n-allow: German input-matching data (instructional-question classifier)
    r"|was\s+(?:ist|bedeutet|heisst|heißt)\s+"  # i18n-allow: same German input-matching data
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
# interpreted "Ich heiße Ruben Lütke" as a task and spawned a Phase-6  # i18n-allow: forensic quote of the actual German utterance that triggered this bug
# worker for a manual USER.md edit (failed with exit_code=1) —
# a clear tool-choice misfire in weaker models.
_SELF_IDENTIFICATION_RE = re.compile(
    r"^\s*(?:"
    r"ich\s+(?:heisse|heiße)\s+\w+"  # i18n-allow: same German input-matching data (self-identification classifier)
    r"|mein\s+name\s+(?:ist|lautet)\s+\w+"
    r"|nenn(?:e|en\s+sie)?\s+mich\s+\w+"
    r"|du\s+(?:kannst|darfst|sollst)\s+mich\s+\w+\s+nennen"
    r"|sie\s+(?:koennen|können|duerfen|dürfen|sollen)\s+mich\s+\w+\s+nennen"  # i18n-allow: German input-matching data (self-identification classifier)
    r"|meine\s+anrede\s+(?:ist|lautet)\s+\w+"  # i18n-allow: same German input-matching data
    r"|meine\s+pronomen\s+(?:ist|sind|lauten)\s+\w+"  # i18n-allow: same German input-matching data
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


# Spoken fallback phrases, localized. Live bug 2026-06-10 23:13
# (data/jarvis_desktop.log): the anti-silence fallback was a hardcoded German
# string, so the English turn "Hey, what's the weather like today?" was
# answered in German. A pinned reply language (brain.reply_language) wins;
# in "auto" mode the phrase mirrors the language detected from the user's
# text; ambiguous text keeps the historical German default.
_ANTI_SILENCE_PHRASES: dict[str, str] = {
    "de": (
        "Das kann ich gerade nicht ausfuehren — "  # i18n-allow: spoken German TTS
        "mir fehlt dafuer das passende Werkzeug."  # i18n-allow: spoken German TTS
    ),
    "en": "I can't do that right now — I'm missing the right tool for it.",
    "es": "Ahora mismo no puedo hacerlo — me falta la herramienta adecuada.",
}

# Meta-/debug feedback acknowledgement. Must NOT narrate background bookkeeping
# (the maintainer finds "ich notiere das Feedback" / "I'm noting that" / "tomo
# nota" annoying — that work happens silently, BACKGROUND_ACTION_RE strips it
# anyway). A brief, neutral acknowledgement that invites the actual correction.
_META_DEBUG_ACK_PHRASES: dict[str, str] = {
    "de": "Verstanden. Was genau hätte anders sein sollen?",  # i18n-allow: spoken German TTS
    "en": "Understood. What exactly should have been different?",
    "es": "Entendido. ¿Qué debería haber sido diferente?",
}


def _localized_phrase(
    phrases: dict[str, str], user_utterance: str, reply_language: str
) -> str:
    """Pick the phrase variant matching the pin or the user's turn language.

    A pinned ``reply_language`` (de/en/es) wins outright. In ``auto`` mode the
    language is detected from the utterance TEXT — the tool-use loop never
    receives the STT language tag (the pipeline resolves the turn language from
    that tag separately, and the loop is only handed ``user_utterance``), so we
    pass ``"unknown"`` as the tag and let the text decide. Ambiguous text keeps
    the historical German default. (Under the common ``[stt].language="de"`` pin
    the tag would resolve to ``de`` anyway, so the text-only path is equivalent
    there and strictly better when the text is clearly English/Spanish.)
    """
    lang = reply_language if reply_language in phrases else resolve_turn_language(
        "unknown", user_utterance, default="de"
    )
    return phrases.get(lang, phrases["de"])


def _anti_silence_phrase(user_utterance: str, reply_language: str = "auto") -> str:
    return _localized_phrase(_ANTI_SILENCE_PHRASES, user_utterance, reply_language)


def _meta_debug_ack_phrase(user_utterance: str, reply_language: str = "auto") -> str:
    return _localized_phrase(_META_DEBUG_ACK_PHRASES, user_utterance, reply_language)


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


def _should_block_action_as_research(
    tool: Any,
    tool_name: str,
    user_utterance: str,
    intent_level: str | None,
    evidence_required_tool: str = "",
) -> bool:
    """Research-intent sanity guard: block an action tool (CLI/MCP) when the
    utterance is research-shaped — UNLESS the evidence gate already mandated
    THIS exact tool for the turn.

    The evidence mandate is the *more specific* rule (it named a concrete tool
    for a concrete data lookup, e.g. ``cli_gcloud`` for "...meine Google-Cloud-
    Kosten..."), so it wins over the *generic* research keyword guard. Without
    this exception the two deterministic rules collide — the evidence gate
    forces the tool while this guard forbids it — and the only reachable
    outcome is the unverified-answer fallback (trace 5edf0245). The override is
    scoped to the mandated tool only, so other action tools stay blocked under
    a research intent.
    """
    if tool is None or not _is_action_tool(tool):
        return False
    if tool_name and tool_name == evidence_required_tool:
        return False
    return _is_research_intent(user_utterance, intent_level)


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
    r"untertitel\s+(von|der|im\s+auftrag)|"  # i18n-allow: German STT-hallucination matching data
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


def _canonical_tool_name(name: str) -> str:
    """Hyphen/underscore- and case-insensitive canonical form of a tool name.

    The registered tool surface mixes naming conventions (``wiki-recall`` vs
    ``run_shell``), so models cross-normalize and invent the OTHER spelling of a
    real tool (live incident 2026-07-05: gemini called ``run-shell``). Both
    spellings collapse to one canonical key so an unambiguous variant still
    resolves to the registered tool instead of the missing-tool refusal.
    """
    return (name or "").strip().lower().replace("-", "_")


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
        # Canonical form → registered name, for hyphen/underscore-tolerant
        # lookup. A canonical collision (two registered tools differing only in
        # separator/case) maps to None: an inexact name must never guess
        # between twins — only the exact spelling reaches either of them.
        self._alias_map: dict[str, str | None] = {}
        for registered in tools:
            canon = _canonical_tool_name(registered)
            self._alias_map[canon] = (
                None if canon in self._alias_map else registered
            )
        self._system_prompt = system_prompt
        self._budget = budget or IterationBudget()
        # Per-response output ceiling forwarded onto every BrainRequest this
        # loop issues. Safety ceiling, not a target (see BrainConfig.max_tokens).
        self._max_tokens = max_tokens

    def _resolve_tool(self, requested: str) -> tuple[Tool | None, str]:
        """Look up a model-requested tool name, tolerating separator/case drift.

        Exact match wins. Otherwise the canonical (hyphen/underscore/case-
        insensitive) form resolves — but only when it maps to exactly ONE
        registered tool. Returns ``(tool, registered_name)``; unknown names
        return ``(None, requested)`` so the anti-silence fallback still fires.
        """
        tool = self._tools.get(requested)
        if tool is not None or not requested:
            return tool, requested
        alias = self._alias_map.get(_canonical_tool_name(requested))
        if alias is None:
            return None, requested
        tool = self._tools.get(alias)
        if tool is None:
            return None, requested
        log.info(
            "tool_use_loop: model called tool %r — resolved to registered "
            "tool %r via separator-insensitive alias", requested, alias,
        )
        return tool, alias

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
        evidence_required_tool: str = "",
        text_consumer: Callable[[str], None] | None = None,
        ack_emitter: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        on_progress: Callable[[], None] | None = None,
        reply_language: str = "auto",
        conversation_language: str = "",
        voice_confirm: bool = False,
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

        ``reply_language`` (live bug 2026-06-10): the ``brain.reply_language``
        pin (``auto``/``de``/``en``/``es``) — selects the language of the
        spoken fallback phrases; ``auto`` mirrors the user's utterance.
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

        def _delta_progress(_delta: object) -> None:
            _progress()

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
                agg = await aggregate_with_consumer(
                    stream,
                    text_consumer,
                    delta_consumer=_delta_progress,
                )
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
                log.warning("IterationBudget exhausted: %s", self._budget.snapshot())
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
                # Normalize to the registered name so the caller's skip-list /
                # template selection (keyed on registered names) still matches
                # when the model used the other separator spelling.
                _, first_name = self._resolve_tool(first_name)
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

                # Separator-tolerant resolution: from here on ``tool_name`` is
                # the REGISTERED name (guards, telemetry, executed_tool_names
                # and the tool-result message all key on it); the raw model
                # spelling stays in ``final_agg.tool_calls`` above.
                tool, tool_name = self._resolve_tool(tool_name)
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
                        "tool_use_loop: side-effect tool '%s' blocked for a how-to question",
                        tool_name,
                    )
                    tool_result_payload = {
                        "success": False,
                        "output": None,
                        "error": (
                            "Tool not executed: the user is asking a how-to or "
                            "explanation question. Answer directly with the "
                            "appropriate short instructions. Do not run any "
                            "app, shell, harness, or computer-use action."
                        ),
                    }
                elif (
                    tool is not None
                    and _is_self_identification(user_utterance)
                    and _is_side_effect_tool(tool)
                ):
                    log.info(
                        "tool_use_loop: side-effect tool '%s' blocked for self-identification",
                        tool_name,
                    )
                    tool_result_payload = {
                        "success": False,
                        "output": None,
                        "error": (
                            "Tool not executed: the user is introducing "
                            "themselves (name, salutation, or pronouns). Reply "
                            "with a short, friendly acknowledgement (1-2 "
                            "sentences max). The Curator extracts the facts "
                            "automatically in the background and persists them "
                            "to USER.md — you must NOT manually edit USER.md, "
                            "spawn a worker, or invoke a shell."
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
                        "tool_use_loop: tool '%s' not in the router tool set — "
                        "anti-silence fallback instead of an empty response", tool_name,
                    )
                    tool_result_payload = {"error": f"Tool '{tool_name}' not available"}
                    suppress_output = _anti_silence_phrase(
                        user_utterance, reply_language
                    )
                elif tool_name == "spawn_worker" and _is_meta_debug_intent(user_utterance):
                    log.info(
                        "tool_use_loop: spawn_worker blocked for a meta/debug utterance"
                    )
                    # Asking the LLM to "answer directly and concretely" via a
                    # tool_result error message turned out to be unreliable —
                    # Gemini Flash regularly ignores the instruction, the
                    # outer loop never produces a text chunk, and Brain-Stream
                    # times out after 40s with nothing in `final_agg.text`.
                    # The user then hears silence ("listens, thinks, listens
                    # again, never speaks").
                    #
                    # The Meta-Debug verdict is already deterministic and
                    # high-precision (intent classifier matched the utterance
                    # against a curated keyword set). Short-circuit the loop
                    # with a neutral acknowledgement so the user always hears
                    # *something*, and let the LLM weigh in next turn instead
                    # of stalling this one.
                    suppress_output = _meta_debug_ack_phrase(
                        user_utterance, reply_language
                    )
                    tool_result_payload = {
                        "success": False,
                        "output": None,
                        "error": (
                            "spawn_worker was not executed: the user is talking "
                            "about Jarvis/provider/transcript behavior. Answer "
                            "directly and concretely; no delegation, no "
                            "confirmation phrase."
                        ),
                    }
                elif stt_blocked:
                    # Arg sanity guard: the tool args look like a Whisper
                    # hallucination (ad outro, copyright string, overly long app name).
                    # Do NOT execute the tool; the LLM gets a structured error
                    # and should ask the user again.
                    log.info(
                        "tool_use_loop: STT-hallucination guard blocked %s — %s",
                        tool_name, stt_reason,
                    )
                    tool_result_payload = {
                        "success": False,
                        "output": None,
                        "error": (
                            f"Tool '{tool_name}' NOT executed: {stt_reason}. "
                            f"Likely an STT misheard word. Answer the user "
                            f"with a short follow-up question (max 1 sentence) "
                            f"instead of calling the tool again with the same value."
                        ),
                    }
                elif _should_block_action_as_research(
                    tool, tool_name, user_utterance, intent_level,
                    evidence_required_tool,
                ):
                    # Intent sanity guard: the user used a research keyword but
                    # the LLM still wants to fire an action tool (CLI or MCP)
                    # against the connected system. This is almost always a wrong
                    # choice (user wants info *about* Supabase, not to query their DB).
                    # Instead of executing → tool result with redirect hint;
                    # the LLM corrects itself in the next turn.
                    log.info(
                        "tool_use_loop: action tool '%s' blocked on research intent "
                        "(intent_level=%s) — LLM redirected to search_web",
                        tool_name, intent_level,
                    )
                    tool_result_payload = {
                        "success": False,
                        "output": None,
                        "error": (
                            f"Tool '{tool_name}' was not executed: the utterance "
                            f"'{user_utterance[:120]}' sounds like research (info "
                            f"*about* a topic), not an action on the connected "
                            f"system. Use search_web instead for general research. "
                            f"Action tools (cli_* and MCP) are only meant for "
                            f"targeted operations on your own resources, e.g. "
                            f"'list my projects'."
                        ),
                    }
                else:
                    # Stamp the turn's resolved output language so deterministic
                    # tool readbacks (computer_use "On it"/"Done") speak the
                    # conversation's language instead of re-deriving it from the
                    # bare utterance — a lone "Now" must not flip a German turn
                    # to English (forensic 2026-06-18). One value, honoring the
                    # pin AND conversation stickiness (Runtime Output Language).
                    out_lang = resolve_output_language(
                        reply_language, "unknown", user_utterance,
                        conversation_language=conversation_language,
                    )
                    result = await self._executor.execute(
                        tool, tool_args,
                        user_utterance=user_utterance,
                        config_snapshot={
                            "output_language": out_lang,
                            "voice_confirm": voice_confirm,
                        },
                        trace_id=tid,
                        # Session-Decision-Log: the model's natural-language text
                        # emitted alongside this tool call IS the "why". Captured
                        # for free (no extra call); the executor redacts + caps it.
                        rationale=agg.text or "",
                    )
                    # Two-turn voice/chat confirmation: the executor deferred this
                    # consequential tool instead of blocking. Speak a short
                    # confirmation question and END the turn (no second brain
                    # round) — the user's next "ja" resumes the stashed action via
                    # the BrainManager. The pending descriptor rides out-of-band on
                    # ``final_agg.voice_confirm`` (never serialized into history).
                    if (
                        result is not None
                        and result.error == VOICE_CONFIRM_SENTINEL
                        and isinstance(result.output, dict)
                    ):
                        # Lazy import: ``jarvis.voice`` couples to ``jarvis.core.
                        # self_mod`` via its package __init__, so importing it at
                        # this low-level module's load time creates an order-
                        # dependent circular import. Import on first use instead.
                        from jarvis.voice.tool_confirmation import (
                            format_tool_confirmation,
                        )
                        question = format_tool_confirmation(
                            result.output.get("tool_name", tool_name),
                            language=out_lang,
                        )
                        final_agg.text = question
                        final_agg.finish_reason = "voice_confirm_pending"
                        final_agg.voice_confirm = {
                            "trace_id": result.output.get("trace_id"),
                            "tool_name": result.output.get("tool_name", tool_name),
                        }
                        if text_consumer is not None and question:
                            try:
                                text_consumer(question)
                            except Exception:  # noqa: BLE001
                                pass
                        return final_agg
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

                    # Wave 1.4 — deterministic honest readback for a config
                    # change. In the voice/chat path config applies immediately
                    # (auto_apply="all", no pre-confirm), so the post-change line
                    # must be the REAL pipeline outcome (applied / rolled back /
                    # refused), never a free-form "done" the brain invents. Render
                    # it here and suppress the second brain turn. Language = the
                    # already-resolved output_language (no per-layer re-derivation,
                    # Runtime Output Language doctrine). Lazy import: jarvis.voice
                    # couples to jarvis.core.self_mod via its package __init__.
                    if tool_name == "set_config_value":
                        from jarvis.voice.config_readback import config_readback

                        readback = config_readback(
                            success=result.success,
                            output=result.output,
                            language=out_lang,
                        )
                        if readback is not None:
                            suppress_output = readback

                # Append tool result as a new message. Cap it centrally so no
                # tool can flood the brain's context with an unbounded raw payload
                # (the actual image artifacts still ride separately as a user-role
                # message below, so clipping this text never blinds vision).
                current_messages.append(BrainMessage(
                    role="tool",
                    content=[{
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "content": _cap_tool_result_json(
                            json.dumps(tool_result_payload, ensure_ascii=False, default=str)
                        ),
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
