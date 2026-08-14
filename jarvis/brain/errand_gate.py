"""Deterministic gate for LLM-chosen errand starts.

Maintainer mandate 2026-08-14: once the errand engine became live, the model
started handing QUESTIONS and UI requests to ``start_errand`` — "I need the
agentic IDE mode or the last transcription" opened a background errand
instead of being answered, and the {name}-Agents board filled with agents
nobody asked for. The tool description already says "not for questions";
a description is advice, not enforcement (same lesson as
``jarvis/brain/spawn_gate.py``).

This module is the enforcement. ``start_errand`` executes ONLY when the turn
reads as a real-world ORDER. Blocked shapes, deliberately word-based
(DE/EN/ES), mirroring the spawn gate's philosophy:

1. Knowledge questions — turns that open with a question word ("was ist…",
   "why does…", "cuánto…") or a tell/explain frame ("kannst du mir sagen",
   "do you know"). Polite orders phrased as questions ("Kannst du mir bitte
   eine Pizza bestellen?") are NOT blocked: the block keys on knowledge
   framing, never on question syntax alone.
2. UI / meta requests — the object of the sentence is a part of Jarvis
   itself (transcript, mode, section, settings, history, terminal…) and no
   real-world order verb appears. "Open the settings" is UI; "open a bank
   account" is an errand.
3. Research framing — "finde heraus", "look up", "recherchiere": research is
   answered inline (or explicitly delegated via the spawn gate), never run
   as an errand.

Everything else passes. The gate never FORCES an errand — a pass only means
the model MAY start one.

Consumers: ``jarvis.brain.tool_use_loop`` and ``jarvis.realtime.tools``.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

#: Registered runtime name of the errand door (jarvis/plugins/tool/start_errand.py).
ERRAND_VEHICLE_TOOL_NAME = "start_errand"

# Knowledge-question openers. Anchored to the start of the turn (after common
# hail words) so an order that merely CONTAINS "was" mid-sentence stays open.
_KNOWLEDGE_OPENER_RE: re.Pattern[str] = re.compile(
    r"^\s*[¿¡]?\s*(?:hey\s+\w+[,.!]?\s+|okay?[,.!]?\s+|also[,.!]?\s+)?"
    r"(?:"
    r"was|wie(?:so)?|warum|weshalb|wann|wo(?:her|hin)?|wer|welche[rsn]?|wie\s*viele?"
    r"|what|how|why|when|where|who|which|whose"
    r"|qu[eé]|c[oó]mo|por\s+qu[eé]|cu[aá]n(?:do|to|ta|tos|tas)|d[oó]nde|qui[eé]n|cu[aá]l(?:es)?"
    r")\b",
    re.IGNORECASE,
)

# Tell/explain frames: a question ABOUT something is not an order to do it.
_TELL_ME_RE: re.Pattern[str] = re.compile(
    r"(?:"
    r"kannst\s+du\s+(?:mir\s+)?(?:sagen|erkl[aä]ren|zeigen)"
    r"|wei[ßs]t\s+du"
    r"|erkl[aä]r(?:e|st)?\s+(?:mir|uns)"
    r"|can\s+you\s+(?:tell|explain|show)\s+me"
    r"|do\s+you\s+know"
    r"|puedes\s+(?:decirme|explicarme|mostrarme)"
    r"|sabes\b"
    r")",
    re.IGNORECASE,
)

# Research framing — answered inline, never run as a background errand.
_RESEARCH_RE: re.Pattern[str] = re.compile(
    r"(?:"
    r"\bfind[e]?\s+(?:heraus|raus)\b|\brecherchier\w*|\bnachschau\w*|\bnachschlag\w*"
    r"|\blook\s+(?:up|into)\b|\bresearch\b|\bfind\s+out\b"
    r"|\binvestig\w*|\baverigu\w*|\bbusca\s+informaci[oó]n"
    r")",
    re.IGNORECASE,
)

# Parts of Jarvis itself. A turn whose object is one of these is a UI/meta
# request unless a real-world order verb says otherwise.
_UI_OBJECT_RE: re.Pattern[str] = re.compile(
    r"(?:"
    r"\btranskript\w*|\btranscript\w*|\bdiktat\w*|\bdictation\b"
    r"|\bmodus\b|\bmode\b|\bmodi\b|\bmodo\b"
    r"|\bsektion\w*|\bsection\b|\bansicht\w*|\bview\b"
    r"|\beinstellung\w*|\bsettings?\b|\bconfig\w*|\bconfiguraci[oó]n\b"
    r"|\bverlauf\b|\bhistory\b|\bhistorial\b"
    r"|\bterminal\w*|\bworkspace\w*|\bpane\w*|\bsidebar\b|\bseitenleiste\b"
    r"|\bagentic[- ]?ide\b"
    r")",
    re.IGNORECASE,
)

# Real-world order verbs — the presence of one keeps a UI-word turn open
# ("book the flight and put it in my history" is still an order) and marks
# polite question-shaped orders as orders.
_WORLD_ORDER_RE: re.Pattern[str] = re.compile(
    r"(?:"
    r"\bbuch\w*|\bbestell\w*|\bkauf\w*|\bk[uü]ndig\w*|\breservier\w*"
    r"|\banmeld\w*|\bmeld\w*\s+(?:mich|uns)\b|\babbestell\w*|\bbeantrag\w*"
    r"|\borganisier\w*|\bbesorg\w*"
    r"|\bbook\b|\border\w*|\bbuy\b|\bcancel\b|\breserve\b|\bpurchase\w*"
    r"|\bsign\s+(?:me\s+)?up\b|\bsubscribe\b|\bunsubscribe\b|\barrange\w*|\bget\s+me\b"
    r"|\breserv\w*|\bcompr\w*|\bpid[eo]\w*|\bcancel[aá]\w*|\bcontrat\w*"
    r")",
    re.IGNORECASE,
)


#: Model-facing feedback when the Agentic-IDE screen blocks every agent vehicle.
AGENTIC_IDE_BLOCKED_FEEDBACK = (
    "Background agents are blocked right now: the user is looking at the "
    "Agentic IDE section, where they drive the terminals themselves. Do not "
    "start a mission or an errand. Answer inline, or act on the open "
    "workspace with the agentic-IDE tools if the user asked for that."
)


def agentic_ide_blocks_agents() -> bool:
    """Maintainer rule 2026-08-14: while the Agentic-IDE section is visible,
    NO background agent starts — neither a mission (spawn_worker/multi_spawn)
    nor an errand. The user is driving terminals there; a self-chosen
    background agent on top is exactly the noise this gate exists to stop.

    Reads the frontend-reported ``surface_on_screen`` flag via the agentic-IDE
    registry (lazy import, same seam ``BrainManager`` already uses). Unknown
    state degrades to "do not block" — a broken optional surface must never
    silence ordinary delegation.
    """
    try:
        from jarvis.agentic_ide.session import agentic_ide_on_screen

        return agentic_ide_on_screen()
    except Exception:  # noqa: BLE001 — optional surface, never fatal
        return False


def errand_block_reason(user_utterance: str) -> str | None:
    """``None`` when the turn may open an errand; otherwise a short reason."""
    text = (user_utterance or "").strip()
    if not text:
        return "empty-turn"
    is_order = bool(_WORLD_ORDER_RE.search(text))
    if _KNOWLEDGE_OPENER_RE.search(text) and not is_order:
        return "knowledge-question"
    if _TELL_ME_RE.search(text) and not is_order:
        return "tell-me-request"
    if _RESEARCH_RE.search(text) and not is_order:
        return "research-framing"
    if _UI_OBJECT_RE.search(text) and not is_order:
        return "ui-meta-request"
    return None


def llm_errand_allowed(user_utterance: str) -> bool:
    """True when the current turn is order-shaped enough to open an errand."""
    reason = errand_block_reason(user_utterance)
    if reason is not None:
        log.info("errand gate: start_errand blocked (%s)", reason)
    return reason is None


def errand_blocked_feedback(user_utterance: str) -> str:
    """Model-facing tool error explaining the block and the right move."""
    reason = errand_block_reason(user_utterance) or "not-an-order"
    return (
        "start_errand was not executed "
        f"(guard: {reason}). This turn reads as a question, a UI request or "
        "research — answer it directly, with your knowledge or the right "
        "tool, in this turn. An errand is ONLY for a real-world order — "
        "book, order, cancel, arrange, sign up — that no single direct tool "
        "call can finish. Do not announce a background job."
    )


__all__ = [
    "AGENTIC_IDE_BLOCKED_FEEDBACK",
    "ERRAND_VEHICLE_TOOL_NAME",
    "agentic_ide_blocks_agents",
    "errand_block_reason",
    "errand_blocked_feedback",
    "llm_errand_allowed",
]
