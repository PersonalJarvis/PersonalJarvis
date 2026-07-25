"""Does this utterance address a terminal of the open workspace?

The Agentic IDE's whole promise is that you *talk* to the agents instead of
typing at them. That only works if "tell Kai to review the wake path" reaches
Kai. Live failure 2026-07-25 (voice session 15:47): it did not — the utterance
contained the depth marker "deep dive", the router's deterministic force-spawn
heuristic recognised it as heavy work, and a background mission worker was
dispatched into a fresh git worktree while Kai's terminal sat idle. The user
saw nothing happen.

The root cause is a routing-precedence bug, not a missing feature: two
deterministic paths claimed the same turn and the older one ran first. This
module is the tie-breaker, and it is deliberately a *detector*, not a policy —
it answers one question ("which open terminal is this turn about, and does the
user want it to DO something or REPORT something?") and lets the callers decide.

Precedence, as implemented by the callers:

1. The user naming the spawn vehicle ("spawn an agent", "im Hintergrund") still
   wins — asking for a background worker *while* a workspace is open is a
   legitimate, unambiguous request, and the workspace must not swallow it.
2. Otherwise, an addressed terminal wins over force-spawn: "let Kai do a deep
   dive" means *Kai* does it, not a new invisible worker.
3. An utterance that names no terminal is none of this module's business.

Why matching is conservative: a false positive here silently withholds a
background mission the user wanted, so a bare mention of a name is not enough.
The utterance must both name a running terminal AND carry an addressing shape
(an imperative aimed at it, a "tell X to …" construction, or a question about
what it is doing). Everything is derived from the *configured* call-signs, never
from a fixed word list — a workspace whose panes are called "Bruno" and "Vega"
behaves exactly like one with the defaults.

Cost: pure regex + an in-memory registry read, no IO, no LLM (AP-9 / AP-11), so
it is safe on the voice hot path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .names import resolve

# Kinds of turn this module recognises.
KIND_PROMPT = "prompt"   # the user wants the agent to do something
KIND_REPORT = "report"   # the user asks what the agent is up to

# Addressing shapes, per supported locale. These match *input vocabulary* — the
# words a person actually says to hand work to a named agent — not prose.
# ``{name}`` is substituted with the live call-signs at match time.
_DIRECTIVE_TEMPLATES: tuple[str, ...] = (
    # "sag Mika, sie soll …" / "sage Kai …" / "tell Nova to …" / "dile a Aria …"
    r"\b(?:sag|sage|sagst)\b[^.!?]{{0,40}}?\b{name}\b",
    r"\btell\b[^.!?]{{0,40}}?\b{name}\b",
    r"\b(?:dile|d[ií]gale|dile\s+a)\b[^.!?]{{0,40}}?\b{name}\b",
    # "Mika soll …" / "lass Kai …" / "Nova should …" / "let Aria …"
    r"\b{name}\b[^.!?]{{0,20}}?\b(?:soll|sollte|kann|k[oö]nnte)\b",
    r"\b{name}\b[^.!?]{{0,20}}?\b(?:should|can|could|please)\b",
    r"\b{name}\b[^.!?]{{0,20}}?\b(?:deber[ií]a|puede|podr[ií]a)\b",
    r"\b(?:lass|l[aä]sst)\b[^.!?]{{0,20}}?\b{name}\b",
    r"\blet\b[^.!?]{{0,20}}?\b{name}\b",
    # "schick(e) das an Kai" / "gib Mika …" / "frag Nova …" / "beauftrage Kai …"
    r"\b(?:schick|schicke|gib|gibt|frag|frage|beauftrag|beauftrage|"
    r"[uü]bergib|[uü]bergebe|weiterleit\w*)\b[^.!?]{{0,40}}?\b{name}\b",
    r"\b(?:send|give|ask|hand|forward|assign)\b[^.!?]{{0,40}}?\b{name}\b",
    r"\b(?:env[ií]a|manda|preg[uú]nta|pasa|asigna)\b[^.!?]{{0,40}}?\b{name}\b",
    # "an Kai:" / "in Mika:" / "bei Nova" / "über Kai" — a routing preposition
    # immediately in front of the call-sign.
    r"\b(?:an|in|bei|[uü]ber|via|to|into|en|a)\s+{name}\b",
    # Vocative: the name opens the utterance ("Kai, mach mal …").
    r"^{name}\b\s*[,:]",
)

# "what is Mika doing?" — a read, never a spawn either. Both word orders are
# covered per language: German and Spanish put the verb before the name ("was
# macht Mika"), English after it ("what is Mika doing").
_REPORT_TEMPLATES: tuple[str, ...] = (
    r"\bwas\b[^.!?]{{0,30}}?\b{name}\b[^.!?]{{0,30}}?\b(?:macht|tut|treibt|arbeitet)\b",
    r"\bwas\b[^.!?]{{0,20}}?\b(?:macht|tut|treibt|arbeitet)\b[^.!?]{{0,20}}?\b{name}\b",
    r"\b(?:what|how)\b[^.!?]{{0,30}}?\b{name}\b[^.!?]{{0,30}}?\b(?:doing|up\s+to|going|at)\b",
    r"\b(?:qu[eé]|c[oó]mo)\b[^.!?]{{0,30}}?\b{name}\b[^.!?]{{0,30}}?\b(?:hace|haciendo|va)\b",
    r"\b(?:qu[eé]|c[oó]mo)\b[^.!?]{{0,20}}?\b(?:hace|haciendo|va)\b[^.!?]{{0,20}}?\b{name}\b",
    r"\b{name}\b[^.!?]{{0,20}}?\b(?:status|fortschritt|progress|estado)\b",
    r"\b(?:status|fortschritt|progress|estado)\b[^.!?]{{0,20}}?\b{name}\b",
    # "ist Kai fertig / stuck / hängt Mika?"
    r"\b(?:ist|is|est[aá])\b\s+{name}\b",
    r"\b(?:h[aä]ngt|steckt|stuck|fertig|done|listo)\b[^.!?]{{0,20}}?\b{name}\b",
)

# Leading conversational filler that carries no instruction for the agent
# ("kannst du mal bitte kurz …"). Stripped from the extracted instruction so the
# composer sees the work, not the politeness.
_FILLER_PREFIX_RE = re.compile(
    r"^(?:"
    r"(?:hey|hallo|hi|ok|okay|also|so|und|na)\b[\s,]*"
    r"|(?:kannst|k[oö]nntest|w[uü]rdest|willst|magst)\s+du\b\s*"
    r"|(?:could|can|would|will)\s+you\b\s*"
    r"|(?:puedes|podr[ií]as)\b\s*"
    r"|(?:mal|bitte|kurz|schnell|eben|just|please|quickly|por\s+favor)\b[\s,]*"
    r")+",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class TerminalIntent:
    """A turn that belongs to one named terminal of the open workspace."""

    terminal: str
    """The resolved call-sign as the session spells it ("Kai")."""

    kind: str
    """``prompt`` (make it do something) or ``report`` (say what it is doing)."""

    instruction: str
    """The user's words minus the addressing preamble — what the agent should do.

    Best-effort and only a *fallback*: the prompt composer receives the whole
    utterance as well, because a deterministic split cannot reliably tell an
    addressing clause from the work in a long spoken sentence.
    """

    utterance: str
    """The original utterance, unmodified."""


def _running_names() -> list[str]:
    """Call-signs of the open workspace, or ``[]`` when none is open."""
    try:
        from .session import get_registry

        session = get_registry().session
    except Exception:  # noqa: BLE001 - the detector must never break a turn
        return []
    if session is None:
        return []
    return [term.name for term in session.terminals]


def _name_alternatives(name: str) -> str:
    """Regex alternation matching ``name`` and its common transcript garble.

    A call-sign arrives through speech recognition, so "Kai" also shows up as
    "Kay" and "Mika" as "Micah". The phonetic folding in ``names.resolve`` covers
    that for *word* matching; here we only need a pattern loose enough to locate
    the name inside a sentence, and ``resolve`` re-checks the hit afterwards.
    """
    escaped = re.escape(name)
    return escaped


def _compile(templates: tuple[str, ...], names: list[str]) -> list[tuple[str, re.Pattern[str]]]:
    out: list[tuple[str, re.Pattern[str]]] = []
    for name in names:
        token = _name_alternatives(name)
        for template in templates:
            try:
                out.append((name, re.compile(template.format(name=token), re.IGNORECASE)))
            except re.error:  # pragma: no cover - a call-sign that breaks regex
                continue
    return out


def _strip_addressing(text: str, name: str) -> str:
    """The utterance with the addressing preamble and the call-sign removed."""
    cleaned = re.sub(
        r"\b(?:sag|sage|sagst|tell|dile|d[ií]gale|schick|schicke|send|gib|give|"
        r"frag|frage|ask|lass|let|beauftrage?|assign|env[ií]a|manda)\b\s*",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        rf"\b(?:an|to|a|bei|in|[uü]ber|via)?\s*{re.escape(name)}\b\s*[,:]?\s*",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    # "…, dass er/sie …" / "…, that it should …" — the subordinating conjunction
    # left over once the addressee is gone.
    cleaned = re.sub(
        r"^\s*(?:und\s+zwar\s+)?(?:dass|damit|that|que)\s+(?:er|sie|es|it|he|she|they)?\s*",
        "",
        cleaned.strip(),
        flags=re.IGNORECASE,
    )
    cleaned = _FILLER_PREFIX_RE.sub("", cleaned.strip())
    return " ".join(cleaned.split())


def detect(user_text: str, *, names: list[str] | None = None) -> TerminalIntent | None:
    """The terminal this turn is about, or ``None``.

    ``names`` is injectable so tests (and callers that already hold the session)
    do not need the process-wide registry.
    """
    text = (user_text or "").strip()
    if len(text) < 3:
        return None
    candidates = _running_names() if names is None else list(names)
    if not candidates:
        return None

    # A report question is checked first: "what is Kai doing?" also matches the
    # looser "ist Kai …" directive shape, and answering it as a prompt would type
    # the question into the agent.
    for name, pattern in _compile(_REPORT_TEMPLATES, candidates):
        if pattern.search(text):
            return TerminalIntent(
                terminal=name, kind=KIND_REPORT, instruction="", utterance=text
            )

    for name, pattern in _compile(_DIRECTIVE_TEMPLATES, candidates):
        if pattern.search(text):
            return TerminalIntent(
                terminal=name,
                kind=KIND_PROMPT,
                instruction=_useful_instruction(text, name),
                utterance=text,
            )

    # Last resort: the utterance names a terminal (possibly garbled) and reads as
    # an instruction. ``names.resolve`` does the phonetic work; requiring a verb
    # keeps a passing mention ("Kai is a nice name") from being addressed.
    matched = resolve(text, candidates)
    if matched is not None and _looks_like_instruction(text):
        return TerminalIntent(
            terminal=matched,
            kind=KIND_PROMPT,
            instruction=_useful_instruction(text, matched),
            utterance=text,
        )
    return None


# Below this, stripping has eaten the task rather than the addressing — "schick
# das an Kai" reduces to "das". The full utterance is then the honest input; the
# composer reads both and can tell them apart.
_MIN_INSTRUCTION_CHARS = 12


def _useful_instruction(text: str, name: str) -> str:
    """The stripped instruction, or the whole utterance when stripping over-ate."""
    stripped = _strip_addressing(text, name)
    return stripped if len(stripped) >= _MIN_INSTRUCTION_CHARS else text


_INSTRUCTION_VERB_RE = re.compile(
    r"\b(?:mach|machen|bau|baue|schreib|schreibe|fix|fixe|behebe|pr[uü]f|pr[uü]fe|"
    r"check|checke|analysier\w*|review\w*|refactor\w*|teste?|starte?|implementier\w*|"
    r"[aä]nder\w*|erstell\w*|f[uü]g\w*|entfern\w*|l[oö]sch\w*|untersuch\w*|"
    r"build|write|make|run|test|start|implement|change|create|add|remove|delete|"
    r"investigate|look|read|find|search|explain|document|"
    r"haz|escribe|crea|revisa|arregla|ejecuta|implementa|analiza)\b",
    re.IGNORECASE,
)


def _looks_like_instruction(text: str) -> bool:
    return bool(_INSTRUCTION_VERB_RE.search(text))


def owns_turn(user_text: str, *, names: list[str] | None = None) -> bool:
    """True when the open workspace should handle this turn instead of a spawn.

    Used by the router's force-spawn guard. An utterance that explicitly names
    the spawn vehicle is NOT owned here — asking for a background agent while a
    workspace happens to be open is a real request, and stealing it would be the
    mirror image of the bug this module fixes.
    """
    from jarvis.brain.spawn_gate import names_spawn_vehicle

    text = (user_text or "").strip()
    if not text or names_spawn_vehicle(text):
        return False
    return detect(text, names=names) is not None


__all__ = [
    "KIND_PROMPT",
    "KIND_REPORT",
    "TerminalIntent",
    "detect",
    "owns_turn",
]
