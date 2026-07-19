"""CriticRunner - spawns the Jarvis-Agent worker harness as the out-of-process reviewer.

The critic receives the mission prompt, worker diff, worker log summary, and
reflection context, then returns a schema-valid verdict. Provider/model
selection follows the same [brain.sub_jarvis] fallback chain as the heavy
worker path.

2026-05-17 (CRIT-1 from audit-team 10): when the resolved primary provider
is ``claude-api`` we bypass the Jarvis-Agent worker harness entirely and spawn ``claude --print``
directly (analogous to ``ClaudeDirectWorker``). Live forensics on
mission_019e35a4 today showed the external ``openclaw`` CLI (2026.5.7) silently ignores the
``cliBackends["claude-cli"]`` override we inject into ``openclaw.json``
(``provider_chain.py:486-505`` for the worker, ``_ensure_critic_agent_registered``
below for the critic), routes the LLM call through the ``anthropic`` Messages
API backend instead, and dies with HTTP 400 "out of extra usage". That
failure mode put 100 % of voice-driven missions into ``critic_loop_exhausted``
since 13:14 today. Direct-spawn cuts the Jarvis-Agent worker harness out of the critic call-path
so the OAuth token from ``~/.claude/.credentials.json`` is the only auth
surface — same path the user's interactive ``claude`` shell uses.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from ..stream_evidence import (
    capability_refusal_answer,
    diff_has_action_evidence,
    extract_verified_external_actions,
    informational_file_answer,
    is_informational_request,
    readonly_answer,
)
from ..workers.claude_direct_worker import _claude_error_is_model_unavailable
from ..workers.process_utils import create_worker_subprocess
from .escalation import choose_critic_model
from .log_summarizer import TriageFn, summarize_log
from .prompts import render_critic_prompt
from .verdict import (
    CRITIC_JSON_SCHEMA,
    REQUIRED_AXES,
    CriticAxis,
    CriticSchemaInvalid,
    CriticTimeout,
    CriticVerdict,
    CriticVerdictInconsistent,
    aggregate_axes_status,
    is_approval_valid,
)

logger = logging.getLogger(__name__)


def _render_external_action_evidence(worker_log: str) -> str:
    """Render correlated successful MCP results as a critic evidence block."""
    actions = extract_verified_external_actions(worker_log)
    if not actions:
        return ""
    lines = [
        "diff --external-action-evidence",
        "# verified-external-action",
    ]
    for name, result in actions:
        compact = " ".join(result.split())
        lines.append(f"+tool: {name}")
        lines.append(f"+result: {compact}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Capability-Honesty Gate (Layer 3c of the Capability Coupling spec)
# ---------------------------------------------------------------------------


@dataclass
class CapabilityHonestyCheck:
    """Wraps a CriticVerdict with capability-honesty metadata.

    This is a *sibling artefact* that augments the frozen ``CriticVerdict``
    without touching ``verdict.py`` (which has ``extra="forbid"`` and a
    frozen schema shared with the Jarvis-Agent worker subprocess contract).

    Attributes:
        verdict: The underlying CriticVerdict returned by the LLM.
        tool_call_evidence: Tuple of tool-name strings parsed from the worker
            output.  Empty when no tool-call markers were found.
        capability_id: The resolved capability id (e.g.
            ``"mcp.gmail/send_mail"``), or ``None`` when the registry is not
            yet available or the intent did not match any registered
            capability.
        honesty_overridden: True when ``enforce_capability_honesty`` replaced
            a false-positive approval with a failure verdict.
    """

    verdict: CriticVerdict
    tool_call_evidence: tuple[str, ...] = field(default_factory=tuple)
    capability_id: str | None = None
    honesty_overridden: bool = False


# --- Tool-call evidence extraction ---


# Patterns for the known harness output formats.
# 1. Jarvis-Agent worker stream.jsonl: ``"type": "tool_use"`` frames with ``"name": "<tool>"``
_RE_TOOL_USE_NAME = re.compile(
    r'"type"\s*:\s*"tool_use"[^}]*?"name"\s*:\s*"([^"]+)"',
    re.DOTALL,
)
# 2. CLI / legacy markers emitted by older worker stubs.
_RE_TOOL_USE_MARKER = re.compile(r'\[TOOL_USE\]\s*([^\s\]]+)')
# 3. dispatch-result event names (mission event bus serialisation).
_RE_DISPATCH_RESULT = re.compile(r'"dispatch-result"[^}]*?"tool"\s*:\s*"([^"]+)"', re.DOTALL)
# 4. Codex ``exec --json`` NDJSON: real actions are ``item.started`` /
#    ``item.completed`` events whose item type is command_execution /
#    file_change / mcp_tool_call / web_search. Live mission 019eb17d
#    (2026-06-10): a codex worker genuinely analysed Gmail and wrote
#    email-analyse.html, but the gate saw zero evidence (it only knew the
#    Claude tool_use shape) and discarded three 12-minute iterations.
#    ``agent_message`` / ``reasoning`` / ``collab_tool_call`` deliberately do
#    NOT count: prose and sub-agent orchestration prove no side-effect.
_RE_CODEX_ACTION_ITEM = re.compile(
    r'"type"\s*:\s*"item\.(?:started|completed)"[^\n]*?'
    r'"type"\s*:\s*"(command_execution|file_change|mcp_tool_call|web_search)"'
)

_ACTION_ITEM_TYPES: Final[frozenset[str]] = frozenset(
    {"command_execution", "file_change", "mcp_tool_call", "web_search"}
)
_FAILED_RESULT_STATUSES: Final[frozenset[str]] = frozenset(
    {
        "approval_denied",
        "blocked",
        "cancelled",
        "canceled",
        "denied",
        "error",
        "expired",
        "failed",
        "outcome_unknown",
        "rejected",
        "timed_out",
        "timeout",
        "unavailable",
    }
)
_RE_DENIED_RESULT = re.compile(
    r"^(?:approval[ _-]?denied|permission[ _-]?denied|denied|blocked|rejected|"
    r"cancelled|canceled|timed[ _-]?out|timeout|expired|outcome[ _-]?unknown)"
    r"(?:\b|[ _:-])",
    re.IGNORECASE,
)


def _result_signals_failure(value: Any) -> bool:
    """Return whether a provider result explicitly reports non-execution.

    The mission tool broker returns structured ``success``/``status`` fields,
    while Claude-style streams commonly use ``is_error`` and some adapters
    serialize that mapping into the result's text content. All three shapes
    must override the preceding call frame: an attempted action is not proof
    that the action completed.
    """
    if isinstance(value, dict):
        is_error = value.get("is_error", value.get("isError"))
        if is_error is True or (
            isinstance(is_error, str) and is_error.strip().lower() == "true"
        ):
            return True
        for key in ("success", "ok"):
            flag = value.get(key)
            if flag is False or (
                isinstance(flag, str) and flag.strip().lower() == "false"
            ):
                return True
        for key in ("exit_code", "return_code"):
            code = value.get(key)
            if code is not None and str(code).strip() not in {"", "0"}:
                return True
        status = str(value.get("status", "")).strip().lower().replace("-", "_")
        if status in _FAILED_RESULT_STATUSES:
            return True
        error = value.get("error")
        if error not in (None, "", False, [], {}):
            return True
        return any(
            _result_signals_failure(value[key])
            for key in (
                "content",
                "result",
                "structuredContent",
                "structured_content",
                "text",
            )
            if key in value
        )
    if isinstance(value, list):
        return any(_result_signals_failure(item) for item in value)
    if not isinstance(value, str):
        return False

    text = value.strip()
    if not text:
        return False
    if "<tool_use_error>" in text.lower():
        return True
    if text[0] in "[{":
        try:
            decoded = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            pass
        else:
            return _result_signals_failure(decoded)
    return _RE_DENIED_RESULT.match(text) is not None


def _json_stream_records(worker_output: str) -> tuple[dict[str, Any], ...]:
    """Best-effort JSON/NDJSON records without interpreting quoted prose."""
    stripped = worker_output.strip()
    if not stripped:
        return ()
    try:
        decoded = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        decoded = None
    if isinstance(decoded, dict):
        return (decoded,)
    if isinstance(decoded, list):
        return tuple(item for item in decoded if isinstance(item, dict))

    records: list[dict[str, Any]] = []
    for raw in worker_output.splitlines():
        try:
            decoded = json.loads(raw.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(decoded, dict):
            records.append(decoded)
        elif isinstance(decoded, list):
            records.extend(item for item in decoded if isinstance(item, dict))
    return tuple(records)


def _message_blocks(record: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Return tool blocks from canonical and flattened provider frames."""
    if record.get("type") in {"tool_use", "tool_result"}:
        return (record,)
    message = record.get("message")
    container = message if isinstance(message, dict) else record
    content = container.get("content")
    if isinstance(content, dict):
        return (content,)
    if isinstance(content, list):
        return tuple(item for item in content if isinstance(item, dict))
    return ()


def _extract_tool_call_evidence(worker_output: str) -> tuple[str, ...]:
    """Parse tool-call evidence from worker output in a defensive, format-agnostic way.

    Supports four harness output formats:
    - Jarvis-Agent worker stream.jsonl ``"type":"tool_use"`` frames.
    - ``[TOOL_USE] <tool_name>`` CLI markers.
    - ``"dispatch-result"`` mission event bus entries.
    - Codex ``exec --json`` action items (command_execution / file_change /
      mcp_tool_call / web_search) — the matched item type doubles as the
      evidence name.

    A correlated failure result removes its call from the evidence set. This
    prevents a denied, blocked, cancelled, or otherwise failed attempt from
    satisfying a side-effecting capability merely because ``tool_use`` was
    emitted first. Uncorrelated call frames retain their historical treatment
    for compatibility with streaming providers that do not persist results.

    If the output format is unrecognised or the text is empty, returns an
    empty tuple — the caller (``enforce_capability_honesty``) treats this as
    conservative failure for ``requires_evidence=True`` capabilities.
    """
    if not worker_output:
        return ()

    calls: list[tuple[str | None, str]] = []
    outcomes: dict[str, bool] = {}
    saw_structured_call = False

    for record in _json_stream_records(worker_output):
        record_type = str(record.get("type", "")).strip()
        if record_type in {"item.started", "item.completed"}:
            item = record.get("item")
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type", "")).strip()
            if item_type not in _ACTION_ITEM_TYPES:
                continue
            saw_structured_call = True
            item_id = str(item.get("id", "")).strip()
            key = f"codex:{item_id}" if item_id else f"codex-anonymous:{len(calls)}"
            calls.append((key, item_type))
            if record_type == "item.completed":
                outcomes[key] = outcomes.get(key, False) or _result_signals_failure(item)
            continue

        if record_type == "dispatch-result":
            name = str(record.get("tool", "")).strip()
            if name:
                saw_structured_call = True
                key = f"dispatch:{len(calls)}"
                calls.append((key, name))
                outcomes[key] = _result_signals_failure(record)
            continue

        for block in _message_blocks(record):
            block_type = str(block.get("type", "")).strip()
            if block_type == "tool_use":
                name = str(block.get("name", "")).strip()
                if not name:
                    continue
                saw_structured_call = True
                tool_id = str(block.get("id", "")).strip()
                calls.append((f"tool:{tool_id}" if tool_id else None, name))
            elif block_type == "tool_result":
                tool_id = str(
                    block.get("tool_use_id")
                    or block.get("tool_call_id")
                    or block.get("call_id")
                    or block.get("toolUseId")
                    or block.get("toolCallId")
                    or block.get("callId")
                    or ""
                ).strip()
                if tool_id:
                    key = f"tool:{tool_id}"
                    outcomes[key] = outcomes.get(key, False) or _result_signals_failure(
                        block
                    )

    names = [
        name
        for key, name in calls
        if key is None or outcomes.get(key) is not True
    ]

    # Preserve compatibility with old, non-JSON logs. Once structured calls
    # were parsed, their correlated outcomes are authoritative and a regex
    # fallback must not accidentally re-add a failed call.
    if not saw_structured_call:
        names.extend(_RE_TOOL_USE_NAME.findall(worker_output))
        names.extend(_RE_DISPATCH_RESULT.findall(worker_output))
        names.extend(_RE_CODEX_ACTION_ITEM.findall(worker_output))
    names.extend(_RE_TOOL_USE_MARKER.findall(worker_output))

    # Deduplicate while preserving first-seen order.
    seen: set[str] = set()
    unique: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            unique.append(n)
    return tuple(unique)


# --- Capability resolver (defensive: works when capabilities.py absent) ---


def _resolve_capability_requires_evidence(user_request: str) -> tuple[bool, str | None]:
    """Return ``(requires_evidence, capability_id)`` for the given utterance.

    Attempts to use ``jarvis.core.capabilities.get_registry()`` if Agent A
    has already shipped the module.  Falls back to a conservative heuristic
    when the module is absent (pre-Agent-A state): any utterance that
    contains an action verb AND a plausible external-system noun is assumed
    to require evidence, keeping the gate safe by default.

    Returns:
        ``(True, capability_id)`` when a ``requires_evidence=True``
        capability is matched.
        ``(False, None)`` when no capability is registered or the intent
        looks like a read-only / smalltalk task.
    """
    # --- Preferred path: use the CapabilityRegistry if available ---
    try:
        from jarvis.core.capabilities import get_registry  # type: ignore[import]
        registry = get_registry()
        cap = registry.resolve_intent(user_request)
        if cap is not None:
            # A registered capability wins unconditionally (both True and False).
            return (cap.requires_evidence, cap.id)
        # cap is None: registry has no entry for this intent.  Fall through to
        # the heuristic rather than returning (False, None) which would silently
        # accept all unregistered side-effecting tasks.
    except (ImportError, AttributeError):
        pass

    # --- Fallback heuristic (pre-Agent-A) ---
    # Action verbs that imply a side-effecting operation.
    # Note: German separable verbs (eintragen, abschicken) are listed by their
    # root stem since the prefix often appears elsewhere in the sentence.
    _ACTION_VERBS = re.compile(
        r'\b(send|schick|sende|create|erstell|add|anlegen|anlege|post|poste|'
        r'delete|l\xf6sch|loesc|buy|bestell|schedule|trag|eintrag|book|reservier|'  # i18n-allow
        r'write|schreib|install|installier|run|starte|execute|f\xfchr|fuehr)\b',
        re.I,
    )
    # External-system nouns that imply a real-world effect.
    _EXTERNAL_NOUNS = re.compile(
        r'\b(email|e-mail|mail|calendar|kalender|termin|appointment|whatsapp|'
        r'telegram|sms|pizza|order|bestellung|post|tweet|x\.com|github|'
        r'issue|ticket|slack|discord)\b',
        re.I,
    )
    has_action = bool(_ACTION_VERBS.search(user_request))
    has_external = bool(_EXTERNAL_NOUNS.search(user_request))
    if has_action and has_external:
        return (True, None)
    return (False, None)


# --- Messaging-action discriminator (for the diff-as-evidence gate) ---
#
# A real worktree diff proves the worker DID file work — valid ground truth for
# an artefact task (HTML/report/code), so it satisfies the honesty gate for a
# prose-only CLI worker (agy/gemini) that writes files but emits no tool_use
# frame. It is NOT valid for a real SEND action: writing draft.txt does not
# prove an email/SMS/chat message was sent, so those must still show a real
# messaging/MCP tool call. We require BOTH a send-verb AND a messaging noun so
# an artefact task that merely mentions the topic ("write an HTML report ABOUT
# my emails") is NOT misclassified as a send — only "send an email", "tweet
# this", "reply to the message" are.
_SEND_VERB_RE = re.compile(
    r"\b(send|sende|sendest|schick|schicke|verschick|verschicke|post|poste|"
    r"tweet|reply|antworte|antworten|message|nachricht|dm)\b",  # i18n-allow
    re.I,
)
_MESSAGING_NOUN_RE = re.compile(
    r"\b(e-?mails?|mails?|sms|whatsapp|telegram|discord|slack|tweets?|"
    r"nachrichten?|message|messages|dm|dms)\b",
    re.I,
)


def _request_is_messaging_action(user_request: str) -> bool:
    """True when the request is to SEND a message (email/SMS/chat/tweet).

    Such an action can never be satisfied by a file write, so the diff-as-
    evidence credit must NOT apply to it — the honesty gate keeps requiring a
    real messaging tool call. Requires a send-verb AND a messaging noun so an
    artefact task that only mentions the topic keeps its diff credit.
    """
    return bool(_SEND_VERB_RE.search(user_request)) and bool(
        _MESSAGING_NOUN_RE.search(user_request)
    )


# --- Main gate function ---

_CAPABILITY_NOT_EXECUTED_SUMMARY_DE: Final[str] = (  # i18n-allow
    "Konnte ich nicht ausführen — mir fehlt für diese Aufgabe das passende "  # i18n-allow: German voice/chat readback (*_DE)
    "Werkzeug. Worker hat keinen Tool-Aufruf gemacht."  # i18n-allow
)
_CAPABILITY_NOT_EXECUTED_REASON: Final[str] = "capability_not_executed"


def enforce_capability_honesty(
    *,
    user_request: str,
    verdict: CriticVerdict,
    worker_output: str,
    worker_diff: str = "",
) -> CapabilityHonestyCheck:
    """Apply the capability-honesty gate to a CriticVerdict.

    Post-processes the LLM verdict:
    1. Parses ``worker_output`` for tool-call evidence.
    2. Resolves the mission's capability (requires_evidence flag).
    3. If ``requires_evidence=True`` and no tool-call evidence is present,
       overrides the verdict to failure with a deterministic German summary.

    The LLM verdict is NEVER trusted on its own for side-effecting
    capabilities — only real tool-call evidence in the worker output counts.

    Args:
        user_request: The original user utterance (mission prompt).
        verdict: CriticVerdict returned by the LLM critic.
        worker_output: Raw worker log / stream.jsonl content used for
            evidence extraction.
        worker_diff: The worker's git diff (in-worktree hunks + Kontrollierer
            augmentations). A real diff is ground-truth evidence of an executed
            action for CLI subscription workers (Antigravity ``agy``, Gemini
            ``--yolo``) that DO the work but emit only prose / plain text and so
            carry no machine-readable tool_use frame. Defaults to "" so existing
            callers and the prose-only anti-hallucination contract are unchanged.

    Returns:
        A ``CapabilityHonestyCheck`` wrapping the (possibly overridden) verdict.
    """
    evidence = _extract_tool_call_evidence(worker_output)
    requires_ev, cap_id = _resolve_capability_requires_evidence(user_request)

    # A real worktree diff is ground-truth evidence of an executed file/action —
    # the canonical artefact the gate demands. agy/gemini write real files but
    # narrate over a PTY/pipe (no tool_use frame), so frame-based extraction is
    # always empty for them; crediting the diff is what lets a genuinely
    # completed CLI mission pass instead of looping 3× to exhaustion (live
    # mission 019eefda, 2026-06-22). It is NOT a weakening of the honesty gate:
    # a prose-only claim with an EMPTY diff still has no evidence and is still
    # overridden (see test_gate_still_blocks_prose_only_email_claim). The one
    # exception is a real SEND action (email/SMS/chat) — a file write does not
    # prove a message was sent, so those still require a real messaging tool
    # call even when a diff exists.
    fs_evidence = diff_has_action_evidence(worker_diff) and not (
        _request_is_messaging_action(user_request)
    )

    if requires_ev and not evidence and not fs_evidence:
        # Override: LLM approved but no real tool-call evidence found.
        logger.warning(
            "enforce_capability_honesty: requires_evidence=True but no tool-call "
            "evidence found in worker output — overriding verdict to failure. "
            "capability_id=%r user_request=%r verdict_was=%r",
            cap_id,
            user_request[:120],
            verdict.verdict,
        )
        # CriticVerdict is frozen; use model_copy to produce the corrected variant.
        from .verdict import CriticAxis  # noqa: PLC0415 — avoid circular at module level
        failure_axis = CriticAxis(
            status="fail",
            evidence=["no tool-call evidence found in worker output"],
        )
        overridden_verdict = verdict.model_copy(
            update={
                "verdict": "revise",
                "axes": {ax: failure_axis for ax in REQUIRED_AXES},
                "correction_instruction": (
                    "The worker claimed to execute an action but produced no "
                    "tool-call evidence. For side-effecting tasks (email, "
                    "calendar, file writes, etc.) the worker MUST make an actual "
                    "tool call — text assertions like 'I have sent the email' are "
                    "never sufficient. Retry and ensure the correct tool is invoked."
                ),
                "summary": (
                    "Worker claimed success but made no tool call. "
                    "Capability not executed."
                ),
                "summary_de": _CAPABILITY_NOT_EXECUTED_SUMMARY_DE,
            }
        )
        return CapabilityHonestyCheck(
            verdict=overridden_verdict,
            tool_call_evidence=evidence,
            capability_id=cap_id,
            honesty_overridden=True,
        )

    # When no tool_use frame was found but the worktree diff proves a real
    # action, surface that as the evidence so telemetry / downstream readers
    # see the mission produced ground-truth work (not "no evidence").
    effective_evidence = evidence or (
        ("filesystem-change",) if fs_evidence else ()
    )
    return CapabilityHonestyCheck(
        verdict=verdict,
        tool_call_evidence=effective_evidence,
        capability_id=cap_id,
        honesty_overridden=False,
    )


MAX_CRITIC_LOOPS: Final[int] = 3
"""Hardcoded per ADR-0009. Not configurable without a new decision record."""

DEFAULT_TIMEOUT_SECONDS: Final[float] = 240.0
"""Subprocess wall-clock cap for one Critic call."""


def _win32_creationflags() -> int:
    """CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB."""
    if sys.platform != "win32":
        return 0
    import subprocess  # noqa: PLC0415

    return (
        getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
    )


def build_critic_cmd(
    prompt: str,
    *,
    model: str,
    schema_json: str,
    use_bare: bool,
    provider: str | None = None,
) -> list[str]:
    """Build the Jarvis-Agent worker argv for the critic subprocess.

    `model` and `use_bare` stay in the signature for compatibility with older
    callers. The effective provider/model comes from the Jarvis-Agent SubJarvis
    fallback chain so worker and critic use the same backend policy.
    """
    del model, use_bare

    from jarvis.missions.worker_runtime.provider_map import to_worker_slug
    from jarvis.missions.workers.provider_chain import (
        _build_worker_cmd,
        _resolve_provider_chain,
        _resolve_worker_argv_prefix,
    )

    chain = _resolve_provider_chain(requested_provider=provider)
    primary = chain[0]
    worker_slug = to_worker_slug(primary.provider)
    augmented_prompt = (
        f"{prompt}\n\n"
        "---\n"
        "Output contract: return exactly one JSON object matching this JSON "
        "schema. No prose, markdown, or code fences before or after it.\n"
        f"{schema_json}\n"
    )
    return _build_worker_cmd(
        augmented_prompt,
        binary=_resolve_worker_argv_prefix(),
        session_id="critic",
        worker_slug=worker_slug,
        model=primary.model,
        timeout_s=DEFAULT_TIMEOUT_SECONDS,
        extra_args=("--agent", "critic"),
    )

# --- Codex structured-output schema (Welle 6 follow-up, 2026-05-24) ---------
#
# The codex CLI (ChatGPT subscription) is an *agent*, not a print tool. Given
# a "return JSON" prompt it sometimes answers with conversational prose
# ("I'm deliberately not calling ExitPlanMode: ...") instead of the verdict,
# which made the codex-critic fail with CriticSchemaInvalid and the whole
# mission show as `error` in the Outputs view even though the worker had
# written the file correctly (live repro 2026-05-24, mission_019e5952).
#
# Fix: codex exec supports `--output-schema <FILE>` (OpenAI structured
# output) which FORCES schema-valid JSON. OpenAI strict mode rejects the
# full CRITIC_JSON_SCHEMA (it has $defs + optional fields), so we feed codex
# a FLAT all-required schema with just the decision fields and reconstruct
# the full CriticVerdict (axes etc.) from the result.
_CODEX_CRITIC_OUTPUT_SCHEMA: Final[dict] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": ["approve", "revise", "reject"]},
        "confidence": {"type": "number"},
        "summary": {"type": "string"},
        "summary_de": {"type": "string"},
        "correction_instruction": {"type": "string"},
        "blocking_issue": {"type": "boolean"},
        "correctness_status": {"type": "string", "enum": ["pass", "fail"]},
        "correctness_evidence": {"type": "string"},
        "completeness_status": {"type": "string", "enum": ["pass", "fail"]},
        "completeness_evidence": {"type": "string"},
        "side_effects_status": {"type": "string", "enum": ["pass", "fail"]},
        "side_effects_evidence": {"type": "string"},
        "security_status": {"type": "string", "enum": ["pass", "fail"]},
        "security_evidence": {"type": "string"},
    },
    "required": [
        "verdict", "confidence", "summary", "summary_de",
        "correction_instruction", "blocking_issue",
        "correctness_status", "correctness_evidence",
        "completeness_status", "completeness_evidence",
        "side_effects_status", "side_effects_evidence",
        "security_status", "security_evidence",
    ],
}


def _verdict_from_codex_flat(flat: dict) -> CriticVerdict:
    """Reconstruct a full CriticVerdict from codex's flat structured output.

    New structured outputs carry one status and one concise evidence reference
    per axis plus an explicit blocking flag. Older five-field payloads remain
    readable for compatibility, using the historical synthetic-axis fallback.

    The semantic fields are authoritative when a model contradicts its own
    verdict label: all-pass evidence with no blocker is an approval, while a
    claimed approval with a blocker or failed axis is a revision. This keeps the
    original-goal threshold deterministic without treating file existence as
    success. The reconstructed full verdict goes through the shared tolerant
    validator, so a voice-summary length cap cannot discard a valid decision.
    """
    verdict = str(flat.get("verdict", "revise"))
    axis_field_names = {
        field_name
        for axis in REQUIRED_AXES
        for field_name in (f"{axis}_status", f"{axis}_evidence")
    }
    has_any_axis_field = bool(axis_field_names.intersection(flat))
    has_axis_fields = all(
        f"{axis}_status" in flat and f"{axis}_evidence" in flat
        for axis in REQUIRED_AXES
    )
    if (has_any_axis_field or "blocking_issue" in flat) and (
        not has_axis_fields or "blocking_issue" not in flat
    ):
        raise ValueError("Incomplete Codex flat verdict axis fields")
    if has_axis_fields:
        axes = {
            axis: CriticAxis(
                status=str(flat[f"{axis}_status"]),  # type: ignore[arg-type]
                evidence=(
                    [str(flat[f"{axis}_evidence"]).strip()]
                    if str(flat[f"{axis}_evidence"]).strip()
                    else []
                ),
            )
            for axis in REQUIRED_AXES
        }
        empty_evidence_axes = [
            axis for axis, axis_verdict in axes.items() if not axis_verdict.evidence
        ]
        if empty_evidence_axes:
            raise ValueError(
                "Codex flat verdict has empty evidence for axis/axes: "
                + ", ".join(sorted(empty_evidence_axes))
            )
        blocking_issue = bool(flat.get("blocking_issue", verdict != "approve"))
        all_axes_pass = all(axis.status == "pass" for axis in axes.values())
        has_failed_axis = not all_axes_pass
        if blocking_issue != has_failed_axis:
            raise ValueError(
                "Codex flat verdict contradicts its blocking flag and axis statuses"
            )
        if all_axes_pass:
            if verdict != "approve":
                logger.info(
                    "CriticRunner: normalized flat verdict=%s to approve "
                    "because every grounded axis passed and no blocker was reported",
                    verdict,
                )
            verdict = "approve"
        elif verdict == "approve":
            verdict = "revise"
    else:
        axis_status = "pass" if verdict == "approve" else "fail"
        evidence = (
            ["codex structured-output verdict"]
            if verdict == "approve" else []
        )
        axes = {
            axis: CriticAxis(status=axis_status, evidence=list(evidence))
            for axis in REQUIRED_AXES
        }

    next_action = {
        "approve": "accept", "revise": "retry", "reject": "escalate_to_user",
    }.get(verdict, "retry")
    payload = {
        "verdict": verdict,
        "axes": {axis: value.model_dump() for axis, value in axes.items()},
        "issues": [],
        "correction_instruction": str(flat.get("correction_instruction", "")),
        "summary": str(flat.get("summary", "")),
        "summary_de": str(flat.get("summary_de", "")),
        "confidence": flat.get("confidence", 0.5),
        "suggested_next_action": next_action,
    }
    return _validate_verdict_tolerant(json.dumps(payload))


def _verdict_has_grounded_axes(verdict: CriticVerdict) -> bool:
    """Return whether every required axis carries at least one evidence item."""
    return REQUIRED_AXES.issubset(verdict.axes) and all(
        verdict.axes[axis].evidence for axis in REQUIRED_AXES
    )


class CriticRunner:
    """Out-of-process critic backed by the Jarvis-Agent worker harness."""

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        log_triage: TriageFn | None = None,
        job_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._log_triage = log_triage
        # Per-mission process-containment job (Windows Job Object / POSIX
        # process-group reaper, see jarvis.missions.isolation.job_object).
        # None keeps the pre-fix behaviour exactly (graceful no-op) for
        # callers/tests that construct CriticRunner without one — the
        # bootstrap path (jarvis.missions.init.bootstrap_missions) passes the
        # SAME factory the orchestrator's Kontrollierer uses for workers.
        self._job_factory = job_factory

    @staticmethod
    def _assign_job(job: Any, pid: int, *, label: str) -> None:
        """Best-effort: place `pid` in the containment job.

        Mirrors ``ClaudeDirectWorker.spawn``'s ``job.assign(proc.pid)`` — a
        failure here must never abort the critic run, only log (AP-10).
        """
        if job is None:
            return
        try:
            job.assign(pid)
        except Exception:  # noqa: BLE001
            logger.warning("%s: job.assign(pid=%d) failed", label, pid, exc_info=True)

    async def run(
        self,
        *,
        mission_prompt: str,
        worker_diff: str,
        worker_log: str,
        prior_reflections: str,
        iteration: int,
        worktree: Path,
        env: dict[str, str],
        security_tag: bool = False,
        prior_confidence: float | None = None,
        _capability_check: bool = True,
    ) -> CriticVerdict:
        """Execute one Critic iteration and return the validated verdict.

        Args:
            mission_prompt: Original user text — anchor token, NEVER paraphrased.
            worker_diff: `git diff` from the worker workspace.
            worker_log: Raw stream.jsonl or stderr content.
            prior_reflections: Pre-rendered string from ReflectionMemory.
            iteration: 0..MAX_CRITIC_LOOPS-1.
            worktree: cwd for the Critic subprocess (read-only via plan mode).
            env: env dict from build_worker_env (allowlist only).
            security_tag: True when the mission touches auth/crypto/db -> Opus forced.
            prior_confidence: From the previous iteration; <0.4 -> Opus forced.

        Returns:
            Validated `CriticVerdict`.

        Raises:
            CriticTimeout: Wall-clock cap exceeded.
            CriticSchemaInvalid: JSON output does not match the Pydantic schema
                even after retry.
            CriticVerdictInconsistent: Empty-evidence approval even after retry.
        """
        if iteration < 0 or iteration >= MAX_CRITIC_LOOPS:
            raise ValueError(
                f"iteration must be in [0, {MAX_CRITIC_LOOPS}); got {iteration}"
            )

        # BUG-LIVE-02 (2026-05-14) + LIVE-VERIFY-2026-05-15 — Pre-Gate against
        # Critic hallucination. The previous version used `not diff AND not log`
        # which left a backdoor: when the worker ran but wrote outside the
        # worktree (or skipped file_write entirely and only claimed success in
        # its reply text), `_capture_diff` returned "" but `stream.jsonl` was
        # non-empty (toolSummary, tokens, finalAssistantVisibleText). The Critic
        # LLM then read the log claim "file successfully created" and approved
        # without checking the diff — classic hearsay-evidence sycophancy
        # (Kim & Kim EMNLP 2025, Snorkel "Self-Critique Paradox" 2025). Live
        # repro mission_019e2c18 (2026-05-15): worker tools = [exec,
        # memory_search] only, no file_write, no file on disk, Critic
        # approved with confidence=0.9 citing log_line:46.
        #
        # The diff is the ground truth: it is the only place where filesystem
        # changes are observable. An empty diff is therefore a hard veto —
        # whatever the worker says in its log, it did NOT achieve the goal.
        # We still report the log-vs-diff distinction in the correction so
        # subsequent iterations know whether the worker is in a "ran but
        # produced nothing" loop (where re-prompting won't help) or a "never
        # ran" loop (where infrastructure is the problem).
        # 2026-05-17 (BUG-LIVE-09): legitimate Read-Only tasks
        # ("summarise BUG-021", "explain how X works") produce a non-empty
        # worker_log full of Read/Grep/Glob tool_use records plus a real
        # answer-text — but the diff is empty by design. The pre-gate
        # below was originally meant to catch the "worker hallucinated
        # success without actually writing a file" attack from 2026-05-14
        # (BUG-LIVE-02). For Read-Only tasks it's a false-positive that
        # forces three deterministic revise iterations followed by
        # `critic_loop_exhausted` and a confusing "three tries weren't
        # enough" announcement — even though Sonnet answered
        # perfectly on iter0.
        #
        # The distinguishing signal: genuine tool-call evidence in the worker
        # log. Whenever the worker actually invoked a tool we let the Critic
        # LLM grade the on-disk state instead of auto-rejecting.
        # This covers three legitimate empty-diff shapes:
        #   - Read-Only task (Read/Grep, no write) — BUG-LIVE-09 2026-05-17.
        #   - Edit-only task whose final diff is empty because the requested
        #     state already held (an Edit re-applied byte-identical content on
        #     a tracked file → ``git diff --cached HEAD`` is empty) —
        #     2026-05-27 hardening finding #5. Auto-revising this burned all
        #     three critic loops into ``critic_loop_exhausted`` on valid work.
        #   - MCP-only side-effect task (e.g. send a mail via mcp_tool_call):
        #     real external action, nothing written to the worktree. The old
        #     check matched only Claude's ``"type":"tool_use"`` shape, so a
        #     codex worker burned all three loops here (2026-06-10, sibling
        #     blindness to the honesty-gate fix for mission 019eb17d).
        # ``_extract_tool_call_evidence`` recognises all harness formats —
        # the honesty gate and this pre-gate must judge by the SAME evidence.
        # The hard veto STAYS for an empty diff with NO genuine tool-call
        # record — the "claims success without invoking any tool"
        # hallucination (BUG-LIVE-02, mission_019e2c18): there is nothing on
        # disk for the LLM to grade, so log text alone cannot earn an approve.
        # Correlated, non-errored MCP results are observable external-action
        # evidence even though they leave no worktree file. Surface them through
        # the same diff-like channel used for verified command/desktop actions.
        # A bare tool call or prose claim never renders this block.
        if not worker_diff.strip():
            external_action_diff = _render_external_action_evidence(worker_log)
            if external_action_diff:
                worker_diff = external_action_diff

        _defer_empty_diff_to_llm = bool(_extract_tool_call_evidence(worker_log))

        if not worker_diff.strip() and is_informational_request(mission_prompt):
            # Pure informational/advisory requests have no file deliverable.
            # The spoken answer is the result, including when the worker used
            # search/read tools before answering. File, code, and side-effect
            # tasks are excluded by the request-shape gate and keep their
            # normal critic path.
            info_answer = readonly_answer(
                worker_diff, worker_log, prompt=mission_prompt
            )
            if info_answer is not None:
                logger.info(
                    "CriticRunner: empty diff, but the request is "
                    "informational and the worker answered (%d chars) -> "
                    "approve (the spoken answer is the deliverable); no "
                    "deterministic revise (iter=%d).",
                    len(info_answer), iteration,
                )
                answer_axis = CriticAxis(
                    status="pass",
                    evidence=[f"informational answer delivered: {info_answer[:200]}"],
                )
                return CriticVerdict(
                    verdict="approve",
                    axes={ax: answer_axis for ax in REQUIRED_AXES},
                    issues=[],
                    correction_instruction="",
                    summary=(
                        "Informational request answered; the spoken answer is "
                        "the deliverable."
                    ),
                    summary_de=(
                        "Frage beantwortet; die Antwort selbst ist das Ergebnis."  # i18n-allow: summary_de localization field
                    ),
                    confidence=1.0,
                    suggested_next_action="accept",
                )

        if not worker_diff.strip() and not _defer_empty_diff_to_llm:
            # A pure question / informational request has NO file deliverable —
            # the worker's spoken answer IS the result. Auto-revising it burned
            # all three critic loops -> critic_loop_exhausted -> FAILED (live
            # mission 019ec638, 2026-06-14: "which city would you recommend for
            # a trip to Australia?"). readonly_answer() keys this off the
            # REQUEST shape (is_informational_request), NEVER the worker's claim,
            # so a DO-task that only claims "done" with no tools still falls
            # through to the deterministic veto below (hallucination guard).
            info_answer = readonly_answer(
                worker_diff, worker_log, prompt=mission_prompt
            )
            if info_answer is not None:
                logger.info(
                    "CriticRunner: empty diff + no tool calls, but the request "
                    "is informational and the worker answered (%d chars) -> "
                    "approve (the spoken answer is the deliverable); no "
                    "deterministic revise (iter=%d).",
                    len(info_answer), iteration,
                )
                answer_axis = CriticAxis(
                    status="pass",
                    evidence=[f"informational answer delivered: {info_answer[:200]}"],
                )
                return CriticVerdict(
                    verdict="approve",
                    axes={ax: answer_axis for ax in REQUIRED_AXES},
                    issues=[],
                    correction_instruction="",
                    summary=(
                        "Informational request answered; the spoken answer is "
                        "the deliverable."
                    ),
                    summary_de=(
                        "Frage beantwortet; die Antwort selbst ist das Ergebnis."  # i18n-allow: summary_de localization field
                    ),
                    confidence=1.0,
                    suggested_next_action="accept",
                )
            # Honest capability refusal: the worker invoked NO tools, wrote
            # nothing, and its answer says it CANNOT do the task ("book me a
            # trip" -> "I can't access travel booking systems"; live mission
            # 019ec674, 2026-06-14). Re-prompting cannot grant a missing
            # capability, so a 3-loop revise -> critic_loop_exhausted is pure
            # waste and surfaces a scary "three attempts failed" ERROR for a
            # request that was simply impossible. Return a ONE-SHOT reject ->
            # the orchestrator maps it to critic_rejected (honest, terminal),
            # carrying the worker's own words in the verdict summary. Gated on
            # the empty-diff + no-tools branch, so the hallucination veto below
            # still owns the "claims done without doing anything" attack.
            refusal = capability_refusal_answer(worker_log, prompt=mission_prompt)
            if refusal is not None:
                logger.info(
                    "CriticRunner: empty diff + no tool calls + honest "
                    "capability refusal (%d chars) -> one-shot reject "
                    "(critic_rejected, not a 3-loop revise) (iter=%d).",
                    len(refusal), iteration,
                )
                refusal_axis = CriticAxis(
                    status="fail",
                    evidence=[
                        f"worker reported it cannot perform the task: {refusal[:200]}"
                    ],
                )
                # CriticVerdict.summary / summary_de are Field(max_length=280) —
                # refusals are wordy, so the snippet MUST be sliced to fit the
                # whole string (prefix + body), else pydantic raises
                # ValidationError on construction and the one-shot reject becomes
                # an uncaught crash. summary_de is a fixed short German phrase so
                # the German TTS field never carries the worker's English refusal.
                refusal_snippet = " ".join(refusal.split())
                summary = (
                    "Worker reports the task is outside its capabilities; "
                    f"its answer: {refusal_snippet}"
                )[:280]
                summary_de = "Aufgabe außerhalb der Fähigkeiten des Workers."  # i18n-allow
                return CriticVerdict(
                    verdict="reject",
                    axes={ax: refusal_axis for ax in REQUIRED_AXES},
                    issues=[],
                    correction_instruction="",
                    summary=summary,
                    summary_de=summary_de,
                    confidence=1.0,
                    suggested_next_action="escalate_to_user",
                )
            ran_but_no_output = bool(worker_log.strip())
            if ran_but_no_output:
                hint = (
                    "Worker ran but produced no filesystem changes (empty diff). "
                    "Either file_write was never invoked, or the write landed "
                    "outside the per-task worktree. The log claims do not count "
                    "as evidence — only the diff does. Retry and ensure files "
                    "are created INSIDE the worktree at the cwd you were given."
                )
                summary_en = (
                    "Worker ran but the diff is empty; log claims are not "
                    "ground truth. Deterministic revise."
                )
                summary_de = (  # i18n-allow
                    "Worker lief, aber keine sichtbaren Datei-Aenderungen. "  # i18n-allow: German voice/chat readback (summary_de)
                    "Automatische Wiederholung."
                )
            else:
                hint = (
                    "Worker produced no observable changes — retry and "
                    "make sure the requested artefact is actually written "
                    "into the workspace."
                )
                summary_en = "No worker output detected; deterministic revise."
                summary_de = (  # i18n-allow
                    "Worker hat keine sichtbaren Aenderungen erzeugt; "  # i18n-allow: German voice/chat readback (summary_de)
                    "automatische Wiederholung."
                )
            logger.warning(
                "CriticRunner: empty worker_diff on iter=%d (log_was_empty=%s) "
                "-> deterministic revise (no LLM spawn)",
                iteration,
                not ran_but_no_output,
            )
            empty_axis = CriticAxis(
                status="fail",
                evidence=["worker produced no observable diff changes"],
            )
            return CriticVerdict(
                verdict="revise",
                axes={ax: empty_axis for ax in REQUIRED_AXES},
                issues=[],
                correction_instruction=hint,
                summary=summary_en,
                summary_de=summary_de,
                confidence=1.0,
                suggested_next_action="retry",
            )

        model = choose_critic_model(
            iteration,
            security_tag=security_tag,
            prior_confidence=prior_confidence,
        )
        log_summary = await summarize_log(worker_log, triage_fn=self._log_triage)
        schema_json = json.dumps(CRITIC_JSON_SCHEMA)
        use_bare = False

        # First round — no adversarial reframe.
        verdict = await self._invoke_once(
            mission_prompt=mission_prompt,
            worker_diff=worker_diff,
            log_summary=log_summary,
            prior_reflections=prior_reflections,
            iteration=iteration,
            worktree=worktree,
            env=env,
            model=model,
            schema_json=schema_json,
            use_bare=use_bare,
            adversarial_reframe=False,
        )

        # Aggregation check FIRST (deterministic):
        # If the Critic returns verdict=approve but any axis is fail,
        # we immediately downgrade to revise. The LLM gave us the information;
        # only the verdict label is inconsistent — no second LLM round needed.
        if (
            verdict is not None
            and verdict.verdict == "approve"
            and aggregate_axes_status(verdict) != "pass"
        ):
            logger.warning(
                "CriticRunner: verdict=approve with fail-axis -> deterministic downgrade to revise"
            )
            verdict = verdict.model_copy(
                update={
                    "verdict": "revise",
                    "summary": (
                        "Aggregation override: verdict=approve was inconsistent with "
                        "axis-fail. Downgraded to revise. Original: "
                        + (verdict.summary or "")
                    )[:280],
                }
            )

        # JSON error or any ungrounded axis -> one LLM retry with reframe.
        # Unsupported revisions are as harmful as unsupported approvals: they
        # spend another worker round and can falsely exhaust the mission.
        retry_needed = (
            verdict is None
            or not _verdict_has_grounded_axes(verdict)
        )
        if retry_needed:
            logger.info(
                "CriticRunner: ungrounded verdict or JSON error -> retry with "
                "adversarial reframe (iter=%d)",
                iteration,
            )
            verdict = await self._invoke_once(
                mission_prompt=mission_prompt,
                worker_diff=worker_diff,
                log_summary=log_summary,
                prior_reflections=prior_reflections,
                iteration=iteration,
                worktree=worktree,
                env=env,
                model=model,
                schema_json=schema_json,
                use_bare=use_bare,
                adversarial_reframe=True,
            )

            # After retry: check aggregation downgrade again (deterministic).
            if (
                verdict is not None
                and verdict.verdict == "approve"
                and aggregate_axes_status(verdict) != "pass"
            ):
                verdict = verdict.model_copy(
                    update={
                        "verdict": "revise",
                        "summary": (
                            "Aggregation override (post-retry): "
                            + (verdict.summary or "")
                        )[:280],
                    }
                )

        if verdict is None:
            raise CriticSchemaInvalid(
                "Critic returned no schema-valid JSON output twice."
            )

        # Any ungrounded verdict after retry is an abstention, not a decision.
        if not _verdict_has_grounded_axes(verdict):
            raise CriticVerdictInconsistent(
                "Critic returned missing or empty axis evidence after "
                "adversarial reframe."
            )

        if verdict.verdict == "approve" and not is_approval_valid(verdict):
            raise CriticVerdictInconsistent("Critic approval remained inconsistent.")

        # --- Capability-Honesty Gate (Layer 3c, Capability Coupling spec) ---
        # Must run AFTER the sycophancy / schema checks above so we only
        # apply the gate to structurally valid verdicts.  Gated behind
        # ``_capability_check`` so tests can opt-out when testing other paths.
        if _capability_check:
            honesty = enforce_capability_honesty(
                user_request=mission_prompt,
                verdict=verdict,
                worker_output=worker_log,
                worker_diff=worker_diff,
            )
            if honesty.honesty_overridden:
                logger.info(
                    "CriticRunner: capability-honesty gate overrode verdict "
                    "for mission_prompt=%r (capability_id=%r)",
                    mission_prompt[:80],
                    honesty.capability_id,
                )
            verdict = honesty.verdict

        # Last-resort net for an informational request answered as a prose
        # document. The critic above keeps FULL authority on every round — a
        # web_search-sourced report (codex worker, commit 18071ed4) is approved
        # on merit there, so quality/sourcing verification is preserved. This
        # only rescues a substantive research/advisory document the critic would
        # otherwise TERMINALLY fail: a one-shot `reject`, or a `revise` on the
        # final iteration (which the orchestrator turns into
        # critic_loop_exhausted). Live mission 019ecb56 (2026-06-15): a complete
        # AI-news report failed 3x because the critic graded prose with a code
        # rubric and called real 2026 model releases "hallucinated future
        # claims" — a critic-epistemics gap web_search on the worker cannot
        # close. We deliver the report (the user judges it) instead of a scary
        # "three attempts failed" ERROR. Anti-hallucination intact:
        # informational_file_answer gates on the REQUEST being informational AND
        # a real, substantive, prose-only document on disk — a code diff, a
        # named-file/side-effect do-task, or a stub never qualifies.
        if verdict.verdict != "approve" and (
            verdict.verdict == "reject" or iteration >= MAX_CRITIC_LOOPS - 1
        ):
            info_document = informational_file_answer(
                worker_diff, prompt=mission_prompt
            )
            if info_document is not None:
                logger.info(
                    "CriticRunner: critic would terminally fail an "
                    "informational prose deliverable (%d chars, verdict=%s) on "
                    "iter=%d -> last-resort advisory approve (the document is "
                    "the answer; critic feedback retained in evidence).",
                    len(info_document),
                    verdict.verdict,
                    iteration,
                )
                doc_axis = CriticAxis(
                    status="pass",
                    evidence=[
                        f"informational report delivered "
                        f"({len(info_document)} chars prose); critic note: "
                        f"{(verdict.summary or '')[:120]}"
                    ],
                )
                return CriticVerdict(
                    verdict="approve",
                    axes={ax: doc_axis for ax in REQUIRED_AXES},
                    issues=[],
                    correction_instruction="",
                    summary=(
                        "Informational/research request answered with a written "
                        "report; delivered as the deliverable after critic "
                        "review."
                    ),
                    summary_de=(  # i18n-allow: German voice readback (TTS exception)
                        "Rechercheauftrag als Bericht geliefert; das Dokument "  # i18n-allow
                        "ist das Ergebnis."  # i18n-allow
                    ),
                    confidence=1.0,
                    suggested_next_action="accept",
                )

        return verdict

    # --- Internal: ensure the `critic` agent is registered in state-dir ---

    async def _ensure_critic_agent_registered(
        self, *, env: dict[str, str], worktree: Path
    ) -> None:
        """Make sure the `critic` Jarvis-Agent agent exists in the per-mission
        `MISSION_STATE_DIR` before we call `openclaw agent --agent critic`.

        2026-05-17 (BUG-024-Episode-2 fix): the old implementation shelled
        out to ``openclaw agents add critic --workspace <wt>``. Empirically
        that subprocess can take 30-60 s on a cold Windows install (full
        Jarvis-Agent worker bootstrap: skills sync, MCP config, runtime plugins, …),
        regularly exceeding the 15 s timeout we used. When it timed out
        the next ``openclaw agent --agent critic`` call surfaced
        ``Error: Unknown agent id "critic"`` and the whole Critic-Loop
        died with ``critic_loop_exhausted`` — exactly the symptom every
        voice mission hit yesterday.

        The Jarvis-Agent worker harness stores the agent registration as a plain JSON entry in
        ``<state_dir>/openclaw.json`` (`agents.list[]`). We can write that
        ourselves in milliseconds and skip the subprocess entirely. The
        materialised shape matches what ``openclaw agents add`` produces
        on success (verified against mission_019e358e/openclaw.json):

            {
              "agents": {
                "list": [
                  {"id": "main"},
                  {
                    "id": "critic",
                    "name": "critic",
                    "workspace": "<worktree>",
                    "agentDir": "<state_dir>/agents/critic/agent"
                  }
                ]
              },
              "meta": {...}
            }

        We preserve any existing ``agents.list`` entries (the worker spawn
        in provider_chain.py already wrote ``cliBackends`` etc.) and only
        add/update the ``critic`` row. Idempotent: if ``critic`` is
        already there, we update the workspace pointer in case the
        worktree path changed across iterations, then return.

        Defensive: if the env doesn't carry ``MISSION_STATE_DIR`` (e.g.
        in unit tests that bypass build_worker_env), we silently skip —
        the worker harness will then auto-create ``main`` on first use and the
        critic call will surface ``Unknown agent`` as before. Production
        always sets MISSION_STATE_DIR in build_worker_env.
        """
        state_dir_env = env.get("MISSION_STATE_DIR")
        if not state_dir_env:
            logger.debug(
                "CriticRunner: MISSION_STATE_DIR missing from env — "
                "skipping inline agent registration (test path?)"
            )
            return
        state_dir = Path(state_dir_env)
        state_dir.mkdir(parents=True, exist_ok=True)
        config_path = state_dir / "openclaw.json"

        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
        except (OSError, ValueError):
            existing = {}

        agents_cfg = existing.setdefault("agents", {})
        agent_list = agents_cfg.setdefault("list", [])

        critic_dir = state_dir / "agents" / "critic" / "agent"
        critic_dir.mkdir(parents=True, exist_ok=True)
        critic_entry = {
            "id": "critic",
            "name": "critic",
            "workspace": str(worktree),
            "agentDir": str(critic_dir),
        }

        # Find existing critic row (idempotency) — preserve order; update
        # in place. If not present, append.
        found = False
        for i, row in enumerate(agent_list):
            if isinstance(row, dict) and row.get("id") == "critic":
                agent_list[i] = critic_entry
                found = True
                break
        if not found:
            agent_list.append(critic_entry)

        # Make sure `main` exists too — the Jarvis-Agent worker expects it. If the worker
        # spawn already wrote it we leave the row alone.
        has_main = any(
            isinstance(r, dict) and r.get("id") == "main"
            for r in agent_list
        )
        if not has_main:
            agent_list.insert(0, {"id": "main"})

        # 2026-05-17 (BUG-024-Episode-3): the Critic subprocess crashes
        # with `LLM request rejected: You're out of extra usage` because
        # The Jarvis-Agent worker routes Claude through the `anthropic` Messages-API
        # backend (which needs paid extra-usage credits) instead of the
        # `claude-cli` OAuth backend (which goes through the user's Claude
        # Max subscription). The Worker-spawn has the same problem and
        # fixes it by injecting `cliBackends["claude-cli"]` with the
        # right --permission-mode + --add-dir + --verbose args into the
        # mission's openclaw.json (provider_chain.py:486-505).
        #
        # The Critic must inject the SAME block — without it the Jarvis-Agent worker
        # falls back to the anthropic Messages-API path and the Critic
        # call dies with HTTP 400 "out of extra usage". Live repro:
        # mission_019e35a4 today (2026-05-17 13:14) — Critic stderr
        # exactly that error. Fixing here makes the Critic use the same
        # OAuth path as the Worker.
        #
        # We resolve the same provider chain the Critic itself uses
        # (via _resolve_provider_chain) so the cliBackends override
        # only fires when the resolved provider is actually claude-cli.
        try:
            from jarvis.missions.worker_runtime.provider_map import (
                NoWorkerSlugMappingError,
                to_worker_slug,
            )
            from jarvis.missions.workers.provider_chain import (
                _resolve_provider_chain,
            )
            _chain = _resolve_provider_chain()
            _primary = _chain[0] if _chain else None
            _slug = (
                to_worker_slug(_primary.provider) if _primary else None
            )
        except (NoWorkerSlugMappingError, Exception):  # noqa: BLE001
            _slug = None
        if _slug == "claude-cli":
            defaults_cfg = agents_cfg.setdefault("defaults", {})
            defaults_cfg["workspace"] = str(worktree)
            defaults_cfg.setdefault("agentRuntime", {"id": "claude-cli"})
            cli_backends_cfg = defaults_cfg.setdefault("cliBackends", {})
            cli_backends_cfg["claude-cli"] = {
                "command": "claude",
                "args": [
                    "--add-dir", str(worktree),
                    "--permission-mode", "bypassPermissions",
                    "--verbose",
                ],
            }

        existing["agents"] = agents_cfg
        try:
            config_path.write_text(
                json.dumps(existing, indent=2), encoding="utf-8"
            )
            logger.info(
                "CriticRunner: registered `critic` agent inline via JSON "
                "(state_dir=%s, claude-cli-backend=%s)",
                state_dir, _slug == "claude-cli",
            )
        except OSError as exc:
            logger.warning(
                "CriticRunner: openclaw.json write failed (%s) — critic "
                "spawn will fall back to the Jarvis-Agent worker's own auto-registration",
                exc,
            )

    # --- Internal: single subprocess spawn + parse ---

    async def _invoke_once(
        self,
        *,
        mission_prompt: str,
        worker_diff: str,
        log_summary: str,
        prior_reflections: str,
        iteration: int,
        worktree: Path,
        env: dict[str, str],
        model: str,
        schema_json: str,
        use_bare: bool,
        adversarial_reframe: bool,
    ) -> CriticVerdict | None:
        """Spawns the critic subprocess once; returns the verdict or None on JSON error.

        Branches on the resolved primary provider:
          * ``claude-api``  -> ``claude --print`` directly (ClaudeDirectCritic
            path, CRIT-1 from 2026-05-17 audit). Bypasses the Jarvis-Agent worker harness.
          * any other slug  -> classic Jarvis-Agent ``agent --agent critic`` path.

        Raises ``CriticTimeout`` on wall-clock cap; other subprocess errors
        surface as ``None`` so the outer caller can run the adversarial retry.
        """
        prompt = render_critic_prompt(
            mission_prompt=mission_prompt,
            worker_diff=worker_diff,
            log_summary=log_summary,
            prior_reflections=prior_reflections,
            iteration=iteration,
            adversarial_reframe=adversarial_reframe,
        )
        codex_prompt = render_critic_prompt(
            mission_prompt=mission_prompt,
            worker_diff=worker_diff,
            log_summary=log_summary,
            prior_reflections=prior_reflections,
            iteration=iteration,
            adversarial_reframe=adversarial_reframe,
            codex_flat=True,
        )

        # Append the JSON-only output contract once at the call site so both
        # the Jarvis-Agent worker and the direct paths see the same prompt. (The old
        # `build_critic_cmd` appended it for the Jarvis-Agent worker path; we lift that
        # so the direct path doesn't end up without the contract.)
        prompt_for_subprocess = (
            f"{prompt}\n\n"
            "---\n"
            "Output contract: return exactly one JSON object matching this "
            "JSON schema. No prose, markdown, or code fences before or after "
            f"it.\n{schema_json}\n"
        )
        codex_prompt_for_subprocess = (
            f"{codex_prompt}\n\n"
            "---\n"
            "Output contract: return exactly one flat JSON object matching "
            "this JSON schema. No prose, markdown, or code fences before or "
            f"after it.\n{json.dumps(_CODEX_CRITIC_OUTPUT_SCHEMA)}\n"
        )

        primary_provider, primary_model = _resolve_critic_provider_model()
        # Auth-viability gate (2026-07-07, mission 019f3d18 / BUG-042 defect 5):
        # this branch used to spawn `claude --print` UNCONDITIONALLY — with a
        # dead Claude CLI auth both critic attempts exited 1, the mission died
        # `critic_unavailable`, and the worker's delivered work (already graded
        # onto disk via the cross-family walk) was thrown away. A non-viable
        # Claude falls through to the codex / in-process API critic below.
        claude_cli_viable = _claude_cli_critic_viable()
        claude_cli_attempted = False
        if primary_provider == "claude-api":
            if claude_cli_viable:
                claude_cli_attempted = True
                claude_verdict = await self._invoke_via_claude_direct(
                    prompt=prompt_for_subprocess,
                    worktree=worktree,
                    env=env,
                    model=primary_model or model,
                    iteration=iteration,
                    adversarial_reframe=adversarial_reframe,
                )
                if claude_verdict is not None:
                    return claude_verdict
                logger.warning(
                    "CriticRunner: claude critic produced no schema-valid "
                    "verdict; crossing to the other viable critic families."
                )
            else:
                logger.warning(
                    "CriticRunner: the `claude` CLI critic is not auth-viable "
                    "(dead/expired login, no classic Anthropic key) — crossing "
                    "to the codex / API-key critic families. Run `claude "
                    "/login` to restore the Claude critic."
                )
        # Welle 6 (2026-05-18): ChatGPT subscription path via codex exec.
        # Same JSON-verdict contract -- codex exec --json runs the model
        # non-interactively, the prompt enforces strict JSON output, the
        # critic spawns read-only because no file should change during
        # review.
        from jarvis.codex_auth_state import codex_needs_reauth
        from jarvis.codex_quota_state import codex_in_quota_cooldown

        if (
            primary_provider in ("chatgpt", "openai-codex")
            and not codex_needs_reauth()
            and not codex_in_quota_cooldown()
        ):
            # NB: `model` here is the Anthropic-shaped slug from
            # `choose_critic_model` (e.g. "claude-sonnet-4-6"); codex
            # would reject it as "unknown model". `_normalize_model_for_codex`
            # strips both the legacy Anthropic aliases (sonnet/opus/haiku)
            # and explicit claude-* / anthropic-* prefixes, so passing
            # any of them through hits a clean empty-string fallback.
            from jarvis.missions.workers.codex_direct_worker import (
                _normalize_model_for_codex,
            )
            effective_critic_model = _normalize_model_for_codex(
                primary_model or model
            )
            codex_verdict = await self._invoke_via_codex_direct(
                prompt=codex_prompt_for_subprocess,
                worktree=worktree,
                env=env,
                model=effective_critic_model,
                iteration=iteration,
                adversarial_reframe=adversarial_reframe,
            )
            if codex_verdict is not None:
                return codex_verdict
            # Codex critic produced no schema-valid verdict — commonly a dead
            # ChatGPT OAuth token ("Please log in again"). Fall back to the
            # claude critic so a dead codex login does not fail a mission with
            # `critic_unavailable` even though the worker delivered real work
            # (2026-06-08 incident, mission 019ea8a5: claude worker, 7.8 KB diff,
            # codex critic → critic_unavailable). Run `codex login` to use the
            # codex critic again.
            if claude_cli_viable:
                logger.warning(
                    "CriticRunner: codex critic produced no verdict — falling "
                    "back to the claude critic (model=%r). Run `codex login` to "
                    "restore the codex critic.",
                    model,
                )
                claude_cli_attempted = True
                claude_verdict = await self._invoke_via_claude_direct(
                    prompt=prompt_for_subprocess,
                    worktree=worktree,
                    env=env,
                    model=model,
                    iteration=iteration,
                    adversarial_reframe=adversarial_reframe,
                )
                if claude_verdict is not None:
                    return claude_verdict
                logger.warning(
                    "CriticRunner: both codex and claude produced no "
                    "schema-valid verdict; crossing to the API critic "
                    "families."
                )
            if not claude_cli_viable:
                logger.warning(
                    "CriticRunner: codex critic produced no verdict and the "
                    "claude critic is not auth-viable; crossing to the "
                    "in-process API critic families."
                )

        # Any other provider (grok / gemini / openrouter / unset) falls back to
        # the direct claude critic. The Jarvis-Agent worker subprocess critic path was
        # removed alongside the Jarvis-Agent worker — it shared the ~92% nested-
        # claude hang failure mode (see docs/BUGS.md). The direct claude CLI
        # path is the proven critic surface.
        #
        # Use the claude critic model from `choose_critic_model` (`model`) here —
        # NOT `primary_model`. In this branch `primary_model` is the foreign
        # provider's model (e.g. "grok-4.3" / "gemini-3.1-pro-preview"), which
        # `claude --model` rejects with returncode=1. That failed the critic
        # twice -> `critic_unavailable` and the whole mission FAILED even though
        # the worker delivered real work (sibling of the ClaudeDirectWorker
        # provider-refusal bug; forensic 2026-06-08 verify run, grok sub-agent).
        # B2 (open-source AP-22): grade IN-PROCESS via a keyed API brain BEFORE the
        # legacy claude-CLI critic, so openrouter/openai/gemini/antigravity/unset
        # missions are reviewed with the user's OWN key instead of the absent
        # `claude` binary. Falls through to claude-direct only when NO API key exists.
        attempted_api_providers: set[str] = set()
        while True:
            api_provider, api_model = _resolve_api_critic_provider(
                primary_provider,
                primary_model,
                excluded_providers=attempted_api_providers,
            )
            if not api_provider or api_provider in attempted_api_providers:
                break
            attempted_api_providers.add(api_provider)
            logger.info(
                "CriticRunner: grading in-process via the %r API brain "
                "(worker provider=%r).", api_provider, primary_provider,
            )
            api_verdict = await self._invoke_via_api_critic(
                prompt=prompt_for_subprocess,
                model=api_model,
                provider=api_provider,
                iteration=iteration,
                adversarial_reframe=adversarial_reframe,
            )
            if api_verdict is not None:
                return api_verdict
            logger.warning(
                "CriticRunner: in-process API critic (%r) produced no "
                "schema-valid verdict; trying the next viable family.",
                api_provider,
            )
        if claude_cli_viable and not claude_cli_attempted:
            logger.info(
                "CriticRunner: API critic families are exhausted; grading "
                "the Jarvis-Agent output with the claude critic model %r.",
                model,
            )
            return await self._invoke_via_claude_direct(
                prompt=prompt_for_subprocess,
                worktree=worktree,
                env=env,
                model=model,
                iteration=iteration,
                adversarial_reframe=adversarial_reframe,
            )
        logger.error(
            "CriticRunner: every viable critic family failed to return a "
            "schema-valid verdict."
        )
        return None

    # --- Internal: direct claude --print path (CRIT-1, 2026-05-17) ---

    async def _invoke_via_claude_direct(
        self,
        *,
        prompt: str,
        worktree: Path,
        env: dict[str, str],
        model: str,
        iteration: int,
        adversarial_reframe: bool,
    ) -> CriticVerdict | None:
        """Spawn ``claude --print`` directly with the prompt on stdin.

        Mirrors ``ClaudeDirectWorker`` semantics minus the writes:
          * ``--permission-mode plan`` — Critic is read-only by design.
          * ``--add-dir <worktree>`` so claude can still read files the
            worker touched if the prompt asks it to verify (useful when
            the diff includes new file paths whose content is referenced
            in the verdict).
          * No ``--output-format stream-json``: the prompt asks for a
            single JSON object as the entire output, ``--print`` writes
            that text verbatim to stdout. Simpler parse path than the
            Jarvis-Agent worker ``payloads[].text`` wrapper.

        OAuth: ``env`` carries ``ANTHROPIC_OAUTH_TOKEN`` and
        ``ANTHROPIC_API_KEY`` from ``build_worker_env`` -> the binary
        uses the user's Claude Max subscription instead of the paid
        Messages-API path that the Jarvis-Agent worker routes through by default.
        """
        from jarvis.missions.workers.claude_direct_worker import (
            _resolve_claude_argv_prefix,
        )

        argv_prefix = _resolve_claude_argv_prefix()
        cmd: list[str] = [
            *argv_prefix,
            "--print",
            # 2026-05-24 fix: was "--permission-mode plan". Plan mode makes
            # claude --print treat the request as a *planning* task and emit
            # meta-commentary about ExitPlanMode ("I'm deliberately not
            # calling ExitPlanMode: ...") instead of the requested JSON verdict
            # — which failed every critic with CriticSchemaInvalid and made
            # missions show "error" in the Outputs view even when the worker
            # had written the file correctly (live repro mission_019e5960).
            # bypassPermissions (same as the worker) makes claude answer the
            # prompt directly. The critic is read-only by intent: the diff is
            # in the prompt and the prompt never asks for a write, so no file
            # is touched regardless of permission mode.
            "--permission-mode", "bypassPermissions",
            "--add-dir", str(worktree),
        ]
        if model:
            cmd.extend(["--model", model])

        logger.info(
            "CriticRunner: spawn (claude-direct) cwd=%s model=%s adv_reframe=%s",
            worktree,
            model,
            adversarial_reframe,
        )

        t0 = time.perf_counter()
        # Per-mission process containment (AP-10): mirrors ClaudeDirectWorker,
        # whose orchestrator wraps the whole spawn in ``async with job:``. A
        # missing/None factory keeps the pre-fix behaviour exactly (graceful
        # no-op) — see ``self._job_factory``.
        job = self._job_factory() if self._job_factory is not None else None
        if job is not None:
            await job.__aenter__()
        try:
            try:
                # create_worker_subprocess sources the Windows flags and degrades
                # CREATE_BREAKAWAY_FROM_JOB gracefully (WinError 5) — same fix as
                # the worker (live mission 019ec61b, 2026-06-14: the critic spawn
                # died on breakaway in the app's restrictive job).
                proc = await create_worker_subprocess(
                    cmd,
                    cwd=str(worktree),
                    env=env,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                logger.warning(
                    "CriticRunner: claude binary not found: %s — cmd=%r",
                    exc, cmd,
                )
                return None

            self._assign_job(job, proc.pid, label="CriticRunner (claude-direct)")

            # Write the prompt to stdin then close to signal EOF.
            try:
                assert proc.stdin is not None  # noqa: S101 - PIPE always present
                proc.stdin.write(prompt.encode("utf-8"))
                await proc.stdin.drain()
                proc.stdin.close()
            except (BrokenPipeError, ConnectionResetError) as exc:
                logger.warning(
                    "CriticRunner: claude stdin write failed: %s", exc
                )

            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=self._timeout
                )
            except TimeoutError as exc:
                with _suppress(ProcessLookupError):
                    proc.kill()
                # Audit-2 H3 -- always wait() after kill() so the transport
                # is torn down and we don't leak a zombie + open pipes. The
                # job (when present) is closed in the outer finally below,
                # reaping any grandchild the CLI spawned (e.g. MCP servers)
                # that a bare proc.kill() would leave orphaned.
                with _suppress(Exception):
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                raise CriticTimeout(
                    f"Critic (claude-direct) exceeded {self._timeout}s — killed."
                ) from exc
        finally:
            if job is not None:
                await job.__aexit__(None, None, None)

        wall_ms = int((time.perf_counter() - t0) * 1000)
        if proc.returncode != 0:
            stderr_text = stderr_b.decode("utf-8", errors="replace")[:1000]
            stdout_text = stdout_b.decode("utf-8", errors="replace")
            # Model-unavailable recovery (live mission 019ec61b, 2026-06-14):
            # the critic model (FRONTIER_MODEL=claude-fable-5) is approved-
            # access-only and the Claude Max subscription can't reach it via
            # the CLI. The CLI default IS accessible — retry once without
            # --model rather than failing the critic (-> critic_unavailable ->
            # whole mission FAILED even though the worker delivered).
            if model and _claude_error_is_model_unavailable(
                stderr_text + " " + stdout_text
            ):
                logger.warning(
                    "CriticRunner: claude critic model %r rejected by the CLI "
                    "— retrying without --model (accessible CLI default).",
                    model,
                )
                return await self._invoke_via_claude_direct(
                    prompt=prompt,
                    worktree=worktree,
                    env=env,
                    model="",
                    iteration=iteration,
                    adversarial_reframe=adversarial_reframe,
                )
            logger.warning(
                "CriticRunner: claude-direct returncode=%d wall_ms=%d stderr=%r",
                proc.returncode, wall_ms, stderr_text,
            )
            return None

        stdout_text = stdout_b.decode("utf-8", errors="replace")
        # Fast path: the prompt enforces a JSON-only contract and the model
        # often honours it (possibly wrapped in ```json fences -- stripped).
        cleaned = _strip_json_fences(stdout_text.strip())
        try:
            return _validate_verdict_tolerant(cleaned)
        except (json.JSONDecodeError, ValueError):
            pass
        # Recovery path: under bypassPermissions claude runs as an agent and
        # narrates its ground-truth verification before emitting the JSON
        # ("Issuing the JSON verdict: {...}"). Pull the verdict object out of
        # the surrounding prose. Validate last-to-first -- the verdict is the
        # final balanced object, after any tool-call narration.
        for candidate in reversed(_iter_balanced_json_objects(stdout_text)):
            try:
                return _validate_verdict_tolerant(_strip_json_fences(candidate))
            except (json.JSONDecodeError, ValueError):
                continue
        logger.warning(
            "CriticRunner: claude-direct JSON-parse failed (no valid verdict "
            "object in output) iter=%d adv=%s output[:300]=%r",
            iteration, adversarial_reframe, stdout_text[:300],
        )
        return None

    # --- Internal: direct codex exec path (Welle 6, 2026-05-18) ---

    async def _invoke_via_api_critic(
        self,
        *,
        prompt: str,
        model: str | None,
        provider: str,
        iteration: int,
        adversarial_reframe: bool,
    ) -> CriticVerdict | None:
        """Grade the mission IN-PROCESS via the provider's own BrainProvider.

        No external CLI. Used for API-key providers with no native CLI critic
        backend so a mission's review never requires the absent `claude` binary
        (open-source AP-22, B2). Returns ``None`` on any failure so the caller's
        claude-direct fallback / adversarial retry still runs.
        """
        try:
            from jarvis.brain.provider_registry import BrainProviderRegistry
            from jarvis.core.config import (
                get_jarvis_agent_secret,
                override_provider_secrets,
            )
            from jarvis.core.protocols import BrainMessage, BrainRequest

            cls = BrainProviderRegistry().get_class(provider)
            worker_key = get_jarvis_agent_secret(provider)
            with override_provider_secrets({provider: worker_key}):
                try:
                    brain = cls(model) if model else cls()  # type: ignore[call-arg]
                except TypeError:
                    brain = cls()  # type: ignore[call-arg]

            req = BrainRequest(
                messages=(BrainMessage(role="user", content=prompt),),
                system=(
                    "You are a strict, adversarial mission-output critic. Return "
                    "EXACTLY one JSON object matching the schema in the prompt — "
                    "no prose and no markdown fences before or after it."
                ),
                temperature=0.0,
                max_tokens=2048,
            )
            parts: list[str] = []
            async with asyncio.timeout(self._timeout):
                with override_provider_secrets({provider: worker_key}):
                    async for delta in brain.complete(req):
                        chunk = getattr(delta, "content", None)
                        if chunk:
                            parts.append(chunk)
            return _parse_verdict_from_text(
                "".join(parts), iteration=iteration, adversarial_reframe=adversarial_reframe,
            )
        except TimeoutError:
            logger.warning(
                "CriticRunner: API critic (%s) timed out after %.0fs.",
                provider, self._timeout,
            )
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "CriticRunner: API critic (%s) failed (%s) — falling back.",
                provider, exc,
            )
            return None

    async def _invoke_via_codex_direct(
        self,
        *,
        prompt: str,
        worktree: Path,
        env: dict[str, str],
        model: str,
        iteration: int,
        adversarial_reframe: bool,
    ) -> CriticVerdict | None:
        """Spawn ``codex exec --json`` directly with the prompt on stdin.

        Mirrors ``_invoke_via_claude_direct`` but for OpenAI's codex CLI:
          * ``--sandbox read-only`` -- the critic must never modify files.
          * ``-c approval_policy=never`` -- no interactive prompts.
          * ``--add-dir <worktree>`` -- read access to what the worker did.
          * ``--skip-git-repo-check`` -- worktrees are nested inside the
            parent repo and codex would otherwise refuse to run.
          * ``--json`` -- emit codex's JSONL event stream so we can pick
            the agent_message text out reliably.
          * ``OPENAI_API_KEY`` stripped from the worker env so codex
            uses its ``~/.codex/auth.json`` ChatGPT-OAuth bearer.

        The prompt already enforces the JSON-only output contract; we
        parse codex's ``item.completed`` agent_message frames to find
        the verdict text, then strip any stray ```json fences.
        """
        from jarvis.missions.workers.codex_direct_worker import (
            _resolve_codex_binary,
        )

        codex_bin = _resolve_codex_binary() or "codex"
        # Welle 6 follow-up (2026-05-24): force structured JSON via
        # --output-schema so codex cannot answer with conversational prose.
        # Written to a real temp file (tempfile gives a proper Windows path
        # codex can open; a bash /tmp path does not resolve for the binary).
        import tempfile as _tempfile
        schema_fh = _tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        schema_path = schema_fh.name
        json.dump(_CODEX_CRITIC_OUTPUT_SCHEMA, schema_fh)
        schema_fh.close()

        cmd: list[str] = [
            codex_bin, "exec",
            "--json",
            "--skip-git-repo-check",
            # Welle 6: same --ignore-user-config as the worker -- skip
            # MCP plugin bootstrap so an expired plugin OAuth token does
            # not kill the critic before it can output its verdict.
            "--ignore-user-config",
            "--sandbox", "read-only",
            "-c", "approval_policy=never",
            "--add-dir", str(worktree),
            "--output-schema", schema_path,
        ]
        if model:
            cmd.extend(["--model", model])

        # OAuth: strip OPENAI_API_KEY so codex falls back to auth.json.
        # Also strip CODEX_HOME (build_worker_env sets it per-mission,
        # but the OAuth bearer lives in the user's global ~/.codex/auth.json).
        env_for_codex = {
            k: v for k, v in env.items()
            if k not in ("OPENAI_API_KEY", "CODEX_HOME")
        }

        logger.info(
            "CriticRunner: spawn (codex-direct) cwd=%s model=%s adv_reframe=%s",
            worktree, model, adversarial_reframe,
        )

        t0 = time.perf_counter()
        # Per-mission process containment (AP-10): mirrors ClaudeDirectWorker /
        # _invoke_via_claude_direct above. A missing/None factory keeps the
        # pre-fix behaviour exactly (graceful no-op) — see self._job_factory.
        job = self._job_factory() if self._job_factory is not None else None
        if job is not None:
            await job.__aenter__()
        try:
            try:
                # create_worker_subprocess: breakaway-flag degradation (WinError 5).
                proc = await create_worker_subprocess(
                    cmd,
                    cwd=str(worktree),
                    env=env_for_codex,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                logger.warning(
                    "CriticRunner: codex binary not found: %s -- cmd=%r",
                    exc, cmd,
                )
                return None

            self._assign_job(job, proc.pid, label="CriticRunner (codex-direct)")

            try:
                assert proc.stdin is not None  # noqa: S101 -- PIPE always present
                proc.stdin.write(prompt.encode("utf-8"))
                await proc.stdin.drain()
                proc.stdin.close()
            except (BrokenPipeError, ConnectionResetError) as exc:
                logger.warning(
                    "CriticRunner: codex stdin write failed: %s", exc,
                )

            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=self._timeout
                )
            except TimeoutError as exc:
                with _suppress(ProcessLookupError):
                    proc.kill()
                with _suppress(Exception):
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                with _suppress(OSError):
                    __import__("os").unlink(schema_path)
                raise CriticTimeout(
                    f"Critic (codex-direct) exceeded {self._timeout}s -- killed."
                ) from exc
            finally:
                with _suppress(OSError):
                    __import__("os").unlink(schema_path)
        finally:
            if job is not None:
                await job.__aexit__(None, None, None)

        wall_ms = int((time.perf_counter() - t0) * 1000)

        # Parse codex JSONL stream FIRST -- codex emits valid agent_message
        # frames even when its MCP plugin bootstrap throws (live repro
        # 2026-05-18: Cloudflare MCP plugin's OAuth bearer expired and
        # codex exits with returncode=1, yet still produces a clean
        # turn.completed with the verdict text. We accept any
        # parseable agent_message regardless of exit code; only fall
        # back to returncode-based failure when no valid frame at all.
        stdout_text = stdout_b.decode("utf-8", errors="replace")
        agent_texts: list[str] = []
        terminal_ok = False
        for raw_line in stdout_text.splitlines():
            line = raw_line.strip()
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
                        agent_texts.append(txt)
            elif t == "turn.completed":
                terminal_ok = True

        if not agent_texts:
            stderr_text = stderr_b.decode("utf-8", errors="replace")[:1000]
            logger.warning(
                "CriticRunner: codex-direct no agent_message frames "
                "returncode=%d wall_ms=%d stderr=%r",
                proc.returncode, wall_ms, stderr_text,
            )
            return None

        if proc.returncode != 0 and not terminal_ok:
            # Hard failure: codex died before completing the turn.
            stderr_text = stderr_b.decode("utf-8", errors="replace")[:1000]
            logger.warning(
                "CriticRunner: codex-direct turn did NOT complete "
                "returncode=%d wall_ms=%d stderr=%r",
                proc.returncode, wall_ms, stderr_text,
            )
            return None

        # Last agent_message wins. With --output-schema codex returns the
        # FLAT decision object (decision fields plus grounded per-axis fields);
        # reconstruct the full CriticVerdict from it. Fall back to validating
        # raw full-verdict text for older runs and non-schema paths.
        candidate = agent_texts[-1]
        cleaned = _strip_json_fences(candidate.strip())
        try:
            flat = json.loads(cleaned)
            if isinstance(flat, dict) and "verdict" in flat and "axes" not in flat:
                return _verdict_from_codex_flat(flat)
            # Already a full verdict shape (or unexpected) -- validate as-is,
            # tolerating only an over-long TTS summary field.
            return _validate_verdict_tolerant(cleaned)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "CriticRunner: codex-direct JSON-parse failed: %s "
                "iter=%d adv=%s candidate[:200]=%r",
                exc, iteration, adversarial_reframe, candidate[:200],
            )
            return None


# --- Helpers ---


def _claude_cli_critic_viable() -> bool:
    """True when the ``claude`` CLI can actually AUTHENTICATE for a critic spawn.

    Binary presence alone is not viability (BUG-042, 2026-07-07): with the
    OAuth token expired in place and no classic Anthropic key, every
    ``claude --print`` critic exits 1 — two attempts per mission →
    ``critic_unavailable`` kills a mission whose worker already delivered.
    Reuses the worker factory's shared auth probe so worker and critic agree.
    """
    try:
        from jarvis.missions.init import _claude_cli_auth_viable
        from jarvis.missions.workers.claude_direct_worker import (
            _resolve_claude_binary,
        )

        return _resolve_claude_binary() is not None and _claude_cli_auth_viable()
    except Exception:  # noqa: BLE001 — unreadable probe => not viable
        return False


def _resolve_critic_provider_model() -> tuple[str | None, str | None]:
    """Returns ``(jarvis_slug, model)`` for the active Critic backend.

    Reads ``[brain.sub_jarvis]`` via the same chain resolver the Worker
    uses, so Worker and Critic always agree on the backend. Returns
    ``(None, None)`` when no chain is resolvable (test paths, broken
    config) so the caller falls back to the Jarvis-Agent path.
    """
    try:
        from jarvis.missions.workers.provider_chain import (
            _resolve_provider_chain,
        )
        chain = _resolve_provider_chain()
        if not chain:
            return (None, None)
        primary = chain[0]
        return (primary.provider, primary.model)
    except Exception:  # noqa: BLE001
        return (None, None)


# API-key brain providers that can grade IN-PROCESS via their own BrainProvider
# (no external CLI). Order = the cross-family fallback preference.
_API_CRITIC_PROVIDERS: tuple[str, ...] = (
    "openrouter",
    "openai",
    "gemini",
    "claude-api",
    "grok",
    "nvidia",
)


def _provider_picked_model(provider: str) -> str | None:
    """The user's configured model for ``provider`` ([brain.providers[p]].model).

    Used so a cross-family critic reuses the user's PICK instead of falling to the
    plugin's hardcoded ``DEFAULT_MODEL`` (OpenRouter = a paid Anthropic id). Returns
    ``None`` when nothing is configured — the plugin default then applies, which is
    free for the gateway (§3/AP-22).
    """
    try:
        from jarvis.core.config import load_config

        pc = (load_config().brain.providers or {}).get(provider)
        picked = (getattr(pc, "model", "") or "").strip()
        return picked or None
    except Exception:  # noqa: BLE001 — config read must never break critic resolution
        return None


def _resolve_api_critic_provider(
    primary_provider: str | None,
    primary_model: str | None,
    *,
    excluded_providers: Collection[str] = (),
) -> tuple[str | None, str | None]:
    """Pick a keyed API brain provider to grade the mission IN-PROCESS (B2, AP-22).

    Prefer the active sub-agent provider when it is itself a keyed API provider;
    otherwise the first API provider that actually has a usable key at runtime. So
    a mission whose worker ran on antigravity/openrouter/gemini is still reviewed
    with the user's OWN key instead of the absent `claude` CLI binary. Returns
    ``excluded_providers`` contains families already attempted during this
    review turn. Returns ``(None, None)`` when no untried API family is viable,
    allowing the caller to continue to a separately authenticated CLI family.
    """
    # Viability-gated, not existence-gated (BUG-042 defect 3, critic edition):
    # a stale sk-ant-oat claude-api credential or a family a worker just
    # proved quota-depleted must be walked past here too.
    from jarvis.missions.init import _api_key_family_viable

    order: list[str] = []
    if primary_provider in _API_CRITIC_PROVIDERS:
        order.append(primary_provider)  # type: ignore[arg-type]
    order += [p for p in _API_CRITIC_PROVIDERS if p not in order]
    excluded = set(excluded_providers)
    for prov in order:
        if prov in excluded:
            continue
        try:
            if _api_key_family_viable(prov):
                # Same provider as the worker → reuse its model. Cross-family →
                # the user's PICK for that provider, never None (which would let
                # the in-process critic fall to the plugin's hardcoded DEFAULT_MODEL
                # = a paid Anthropic id on the OpenRouter gateway). §3/AP-21/AP-22.
                model = (
                    primary_model
                    if prov == primary_provider
                    else _provider_picked_model(prov)
                )
                return prov, model
        except Exception:  # noqa: BLE001, S112 -- skip an unreadable family probe
            continue
    return None, None


def _parse_verdict_from_text(
    stdout_text: str, *, iteration: int, adversarial_reframe: bool
) -> CriticVerdict | None:
    """Parse a CriticVerdict from raw model output — clean JSON, ```json-fenced, or
    embedded in narration. Shared by the claude-direct and in-process API paths.
    Returns ``None`` when no valid verdict object is present.
    """
    cleaned = _strip_json_fences(stdout_text.strip())
    last_error: Exception | None = None
    try:
        return _validate_verdict_tolerant(cleaned)
    except (json.JSONDecodeError, ValueError, ValidationError) as exc:
        last_error = exc
    for candidate in reversed(_iter_balanced_json_objects(stdout_text)):
        try:
            return _validate_verdict_tolerant(_strip_json_fences(candidate))
        except (json.JSONDecodeError, ValueError, ValidationError) as exc:
            last_error = exc
            continue
    logger.warning(
        "CriticRunner: JSON-parse failed (no valid verdict object) iter=%d adv=%s "
        "validation=%r output[:300]=%r",
        iteration, adversarial_reframe, str(last_error)[:600], stdout_text[:300],
    )
    return None


class _suppress:
    """Minimal `contextlib.suppress` without an extra import."""

    def __init__(self, *exc_types: type[BaseException]) -> None:
        self._types = exc_types

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb):  # type: ignore[no-untyped-def]
        return exc_type is not None and issubclass(exc_type, self._types)


def _extract_json_payload(stdout_text: str) -> str:
    """Extract the schema-validated CriticVerdict JSON from critic stdout.

    The Jarvis-Agent worker returns a JSON document whose first payload text normally contains
    the reviewer JSON. Older fixtures may pass the verdict JSON directly, so
    we keep the bare-object fallback.
    """
    stripped = stdout_text.strip()
    if not stripped:
        return ""

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return _strip_json_fences(stripped)

    if isinstance(data, dict):
        payloads = data.get("payloads")
        if isinstance(payloads, list) and payloads:
            first = payloads[0]
            if isinstance(first, dict) and isinstance(first.get("text"), str):
                return _strip_json_fences(first["text"])

        structured = data.get("structured_output")
        if isinstance(structured, dict):
            return json.dumps(structured)

        response = data.get("response")
        if isinstance(response, str):
            return _strip_json_fences(response)

        result = data.get("result")
        if isinstance(result, str):
            return _strip_json_fences(result)

        return json.dumps(data)

    return stripped


def _strip_json_fences(text: str) -> str:
    stripped = text.strip()
    for fence in ("```json", "```JSON", "```"):
        if stripped.startswith(fence):
            stripped = stripped[len(fence):].lstrip()
            if stripped.endswith("```"):
                stripped = stripped[:-3].rstrip()
            break
    return stripped


# Fields whose ``max_length`` is a presentation cap (TTS readback), not a
# correctness invariant. An over-long value here must NOT sink an otherwise
# valid verdict — see ``_validate_verdict_tolerant``.
_TRUNCATABLE_VERDICT_FIELDS: Final[tuple[str, ...]] = ("summary", "summary_de")


def _field_max_length(field_name: str) -> int | None:
    """Return the ``max_length`` declared on a CriticVerdict string field."""
    field_info = CriticVerdict.model_fields.get(field_name)
    if field_info is None:
        return None
    for meta in field_info.metadata:
        max_len = getattr(meta, "max_length", None)
        if max_len is not None:
            return int(max_len)
    return None


def _validate_verdict_tolerant(payload: str) -> CriticVerdict:
    """Validate critic JSON, tolerating only over-long TTS summary fields.

    Live root cause (mission 019e7f6d, 2026-05-31 21:08/21:09): the critic
    returned a fully valid ``approve`` verdict over a rich 597-line HTML
    deliverable, but ``summary`` (322 chars) and ``summary_de`` (322 chars)
    exceeded the ``max_length=280`` TTS cap on ``CriticVerdict``. Pydantic
    rejected the entire object, the runner returned ``None`` on both the first
    attempt and the adversarial-reframe retry, and the mission was marked
    ``critic_unavailable`` — discarding the worker's real work. The richer the
    worker output, the longer the critic's summary prose, so this
    false-negative bites *more* often as worker quality improves.

    Strategy: try a strict validation first. If it fails *only* because one or
    more of the presentation-only summary fields are too long, truncate those
    fields to their declared cap and re-validate. Any other validation error
    (missing axes, bad enum, out-of-range confidence, wrong types) is re-raised
    unchanged — a genuinely malformed verdict still fails, and the empty-
    evidence / aggregation checks downstream are untouched.

    Raises:
        json.JSONDecodeError: ``payload`` is not valid JSON.
        ValidationError: the verdict is invalid for a reason other than an
            over-long summary field.
    """
    try:
        return CriticVerdict.model_validate_json(payload)
    except ValidationError as exc:
        def _is_truncatable(err: dict) -> bool:
            loc = err.get("loc") or ()
            return (
                bool(loc)
                and loc[0] in _TRUNCATABLE_VERDICT_FIELDS
                and err.get("type") == "string_too_long"
            )

        errors = exc.errors()
        offending = {err["loc"][0] for err in errors if _is_truncatable(err)}
        # Re-raise unless EVERY error is an over-long summary field.
        if not offending or any(not _is_truncatable(err) for err in errors):
            raise

        data = json.loads(payload)  # already valid JSON (pydantic parsed it)
        for field_name in offending:
            cap = _field_max_length(field_name) or 280
            value = data.get(field_name)
            if isinstance(value, str) and len(value) > cap:
                # Keep the leading content, leave room for an ellipsis so the
                # truncation is visible rather than a silent cut.
                data[field_name] = value[: cap - 1].rstrip() + "…"
        logger.info(
            "CriticRunner: truncated over-long verdict summary field(s) %s to "
            "the TTS cap to preserve an otherwise valid verdict",
            sorted(offending),
        )
        return CriticVerdict.model_validate(data)


def _iter_balanced_json_objects(text: str) -> list[str]:
    """Return every top-level balanced ``{...}`` substring of ``text``.

    ``claude --print`` under ``--permission-mode bypassPermissions`` behaves
    as a full agent: it may run ``Read``/``Glob`` to verify the worker's diff
    against the on-disk worktree, then *narrate* its findings ("Direct
    verification complete ... Issuing the JSON verdict:") before emitting the
    JSON object. ``model_validate_json`` parses the entire string as one JSON
    value, so a single prose sentence ahead of the ``{`` throws.

    This scanner walks the text tracking brace depth while ignoring braces
    inside string literals (honouring ``\\`` escapes), and collects each
    balanced object. Callers validate candidates last-to-first because the
    verdict is emitted at the *end*, after any tool-call narration.
    """
    objects: list[str] = []
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    objects.append(text[start:i + 1])
                    start = -1
    return objects

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_CRITIC_LOOPS",
    "CriticRunner",
    "CapabilityHonestyCheck",
    "build_critic_cmd",
    "enforce_capability_honesty",
]
