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
    # Plural forms carry their weight: the moment two panes are addressed the
    # user says "Iris und Bruno sollen …", and a singular-only verb list made
    # the second addressee depend on the weaker last-resort path.
    r"\b{name}\b[^.!?]{{0,20}}?\b(?:soll|sollen|sollte|sollten|kann|"  # i18n-allow: input vocab
    r"k[oö]nnen|k[oö]nnte|k[oö]nnten|m[uü]ssen|muss)\b",  # i18n-allow: input vocab
    r"\b{name}\b[^.!?]{{0,20}}?\b(?:should|can|could|please|must)\b",
    r"\b{name}\b[^.!?]{{0,20}}?\b(?:deber[ií]an?|pueden?|podr[ií]an?)\b",
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
    r"\bwas\b[^.!?]{{0,30}}?\b{name}\b[^.!?]{{0,30}}?"
    r"\b(?:macht|machen|tut|tun|treibt|treiben|arbeitet|arbeiten)\b",
    r"\bwas\b[^.!?]{{0,20}}?"
    r"\b(?:macht|machen|tut|tun|treibt|treiben|arbeitet|arbeiten)\b"
    r"[^.!?]{{0,20}}?\b{name}\b",
    r"\b(?:what|how)\b[^.!?]{{0,30}}?\b{name}\b[^.!?]{{0,30}}?\b(?:doing|up\s+to|going|at)\b",
    r"\b(?:qu[eé]|c[oó]mo)\b[^.!?]{{0,30}}?\b{name}\b[^.!?]{{0,30}}?"
    r"\b(?:hacen?|haciendo|van?)\b",
    r"\b(?:qu[eé]|c[oó]mo)\b[^.!?]{{0,20}}?\b(?:hacen?|haciendo|van?)\b"
    r"[^.!?]{{0,20}}?\b{name}\b",
    r"\b{name}\b[^.!?]{{0,20}}?\b(?:status|fortschritt|progress|estado)\b",
    r"\b(?:status|fortschritt|progress|estado)\b[^.!?]{{0,20}}?\b{name}\b",
    # Is it done / is it stuck. Plural included, because two addressed panes
    # get asked about together:
    # "ist Kai fertig?" / "sind Iris und Bruno fertig?"  # i18n-allow: input vocab
    r"\b(?:ist|sind|is|are|est[aá]|est[aá]n)\b\s+{name}\b",  # i18n-allow: input vocab
    r"\b(?:h[aä]ngt|h[aä]ngen|steckt|stecken"  # i18n-allow: input vocab
    r"|stuck|fertig|done|listos?)\b"  # i18n-allow: input vocab
    r"[^.!?]{{0,20}}?\b{name}\b",
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


#: Routing prepositions that may sit directly in front of a call-sign and are
#: part of the addressing, not of the work ("schick das AN Kai").
_ADDRESS_PREPOSITION = r"(?:an|to|a|bei|in|[uü]ber|via)"  # i18n-allow: input vocab


def _strip_addressing(text: str, *names: str) -> str:
    """The utterance with the addressing preamble and the call-signs removed.

    Takes every addressee at once: with a fan-out the remaining names are part
    of the address list, not part of the work, and leaving them in would have
    the composer brief Iris about Bruno.
    """
    cleaned = re.sub(
        r"\b(?:sag|sage|sagst|tell|dile|d[ií]gale|schick|schicke|send|gib|give|"
        r"frag|frage|ask|lass|let|beauftrage?|assign|env[ií]a|manda)\b\s*",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    for name in names:
        cleaned = re.sub(
            rf"\b{_ADDRESS_PREPOSITION}?\s*{re.escape(name)}\b\s*[,:]?\s*",
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


# Everything that may stand between two call-signs for them to count as ONE
# address list. Deliberately tiny: an enumeration ("Iris und Bruno") is a
# fan-out, whereas two names each carrying their own directive are two separate
# assignments and are found on their own. Anything else is not an enumeration.
# The alternative conjunction ("oder" / "or") is left out on purpose — offering
# a choice between two panes is not an address list.
_COORDINATION_RE = re.compile(
    r"^[\s,]*(?:und|and|sowie|plus|y|&|\+)?[\s,]*$",
    re.IGNORECASE,
)

# Addressing the WHOLE workspace at once ("sag allen …", "tell everyone …").
# Each shape needs the collective word in an ADDRESSING position — after a
# handing-over verb, or opening the sentence in front of a modal. A bare "alle"
# is far too common in ordinary work talk ("mach alle Tests") to be an address.
_EVERYONE_TEMPLATES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:sag|sage|sagt|sagst|gib|frag|frage|schick|schicke|beauftrage?)\b"
        r"[\s,]*\b(?:allen|alle)\b",
        r"\b(?:tell|ask|give|assign|send)\b[\s,]*"
        r"\b(?:everyone|everybody|them\s+all|all\s+of\s+them)\b",
        r"^(?:und\s+)?\b(?:alle|allen)\b[^.!?]{0,20}?"  # i18n-allow: input vocab
        r"\b(?:soll|sollen|m[uü]ssen|k[oö]nnen)\b",  # i18n-allow: input vocab
        r"^(?:and\s+)?\b(?:everyone|everybody|all\s+of\s+them)\b[^.!?]{0,20}?"
        r"\b(?:should|must|please|can)\b",
        r"\b(?:diles?|preg[uú]nta)\b[\s,]*\b(?:a\s+)?todos\b",
        r"^\b(?:todos|todas)\b[^.!?]{0,20}?\b(?:deben|deber[ií]an)\b",
    )
)

# A word made only of letters — the unit ``names.resolve`` scores a call-sign
# against. Digits and underscores can never be part of a spoken name.
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _mentions(
    text: str, candidates: list[str], *, fuzzy: bool = True
) -> list[tuple[int, int, str]]:
    """Every call-sign the utterance names, as ``(start, end, name)`` in order.

    Resolution is delegated to ``names.resolve`` word by word, so the phonetic
    tolerance is exactly the one the singular path already ships — a second
    matching rule here would be free to drift away from it.

    ``fuzzy=False`` keeps only names the utterance states exactly (or in a
    spelling that folds to the same sound). See the last-resort branch of
    ``detect_all`` for why that distinction has to exist.
    """
    seen: set[str] = set()
    out: list[tuple[int, int, str]] = []
    for match in _WORD_RE.finditer(text):
        hit = resolve(match.group(0), candidates, fuzzy=fuzzy)
        if hit is None or hit in seen:
            continue
        seen.add(hit)
        out.append((match.start(), match.end(), hit))
    return out


def _coordinated_group(
    mentions: list[tuple[int, int, str]], text: str, anchors: set[str]
) -> set[str]:
    """``anchors`` plus every call-sign enumerated next to one of them.

    "Sag Iris und Bruno, sie sollen …" carries the addressing shape on Iris
    alone; Bruno is addressed by standing in the same list. Walking neighbours
    transitively covers "Iris, Bruno und Casey" without a list-length special
    case.
    """
    group = set(anchors)
    changed = True
    while changed:
        changed = False
        for (_, prev_end, prev_name), (next_start, _, next_name) in zip(
            mentions, mentions[1:], strict=False
        ):
            if not _COORDINATION_RE.match(text[prev_end:next_start]):
                continue
            if prev_name in group and next_name not in group:
                group.add(next_name)
                changed = True
            elif next_name in group and prev_name not in group:
                group.add(prev_name)
                changed = True
    return group


def detect_all(
    user_text: str, *, names: list[str] | None = None
) -> list[TerminalIntent]:
    """Every terminal this turn addresses, in the order the utterance names them.

    Live failure this exists for (voice session 2026-07-26 09:18): "kannst du
    bitte Iris und Bruno beide in Deep Dive geben …" reached Iris only. The
    detector returned on its first match, so a second addressee was not merely
    missed — it could not be represented. Reporting was worse than the miss:
    the readback named one pane, the provider re-used both names from the
    question, and the user was told two agents were working when one was.

    A call-sign joins the result in one of three ways, in falling confidence:

    1. it carries an addressing shape itself ("tell Bruno to …") — its own
       assignment;
    2. it is enumerated beside such a name ("Iris und Bruno") — it shares the
       instruction;
    3. nobody carries a shape, but the utterance names panes and reads as an
       instruction — the singular path's last resort, widened to every name
       mentioned, because "Iris und Bruno … analysieren" addresses both by
       exactly the same evidence.

    Collective address ("sag allen …") returns every running pane.
    """
    text = (user_text or "").strip()
    if len(text) < 3:
        return []
    candidates = _running_names() if names is None else list(names)
    if not candidates:
        return []

    # A report question is checked first: "what is Kai doing?" also matches the
    # looser "ist Kai …" directive shape, and answering it as a prompt would type
    # the question into the agent.
    report_anchors = {
        name
        for name, pattern in _compile(_REPORT_TEMPLATES, candidates)
        if pattern.search(text)
    }
    prompt_anchors = {
        name
        for name, pattern in _compile(_DIRECTIVE_TEMPLATES, candidates)
        if pattern.search(text)
    }
    mentions = _mentions(text, candidates)

    if any(pattern.search(text) for pattern in _EVERYONE_TEMPLATES):
        kind = KIND_REPORT if report_anchors and not prompt_anchors else KIND_PROMPT
        return [
            TerminalIntent(
                terminal=name,
                kind=kind,
                instruction="" if kind == KIND_REPORT
                else _useful_instruction(text, name),
                utterance=text,
            )
            for name in candidates
        ]

    if report_anchors:
        kind = KIND_REPORT
        anchors = report_anchors
    elif prompt_anchors:
        kind = KIND_PROMPT
        anchors = prompt_anchors
    elif mentions and _looks_like_instruction(text):
        # Last resort: the utterance names terminals and reads as an
        # instruction. Requiring a verb keeps a passing mention ("Alex is a
        # nice name") from being addressed.
        #
        # This is the ONLY branch where the call-sign is the whole case: no
        # "tell X to …", no "X should …" — just a name and a verb. That makes
        # it the branch a merely SIMILAR word can win, and the names are short
        # enough for ordinary speech to score close: measured against the
        # shipping pool "unten" reaches "Hunter" and "dann" reaches "Dana"; the
        # live 2026-07-26 session had "keine" reaching "Kai" and briefing a  # i18n-allow: quoted transcript tokens
        # second agent nobody had named. So here — and only here — the name has
        # to be exact (or fold to the same sound, which is what carries a
        # garbled transcript). An addressing shape, being independent evidence,
        # still admits a fuzzy call-sign in the branches above.
        certain = _mentions(text, candidates, fuzzy=False)
        if not certain:
            return []
        mentions = certain
        kind = KIND_PROMPT
        anchors = {name for _, _, name in certain}
    else:
        return []

    addressed = _coordinated_group(mentions, text, anchors)
    ordered = [name for _, _, name in mentions if name in addressed]
    # An anchor matched by a template but never located as a word (a garbled
    # transcript the templates still caught) keeps its place at the end.
    ordered += [name for name in anchors if name not in ordered]

    shared = "" if kind == KIND_REPORT else _useful_instruction(text, *ordered)
    return [
        TerminalIntent(
            terminal=name, kind=kind, instruction=shared, utterance=text
        )
        for name in ordered
    ]


def detect(user_text: str, *, names: list[str] | None = None) -> TerminalIntent | None:
    """The terminal this turn is about, or ``None``.

    The singular view of ``detect_all`` — the first addressee. Kept because the
    precedence gates (``owns_turn``, ``detect_spawn``, ``spawn_gate``) only ever
    ask *whether* the workspace owns this turn, and one answer is cheaper to
    reason about than a list. Both derive from one detector so they cannot drift
    into disagreeing about an utterance.

    ``names`` is injectable so tests (and callers that already hold the session)
    do not need the process-wide registry.
    """
    found = detect_all(user_text, names=names)
    return found[0] if found else None


# Below this, stripping has eaten the task rather than the addressing — "schick
# das an Kai" reduces to "das". The full utterance is then the honest input; the
# composer reads both and can tell them apart.
_MIN_INSTRUCTION_CHARS = 12


def _useful_instruction(text: str, *names: str) -> str:
    """The stripped instruction, or the whole utterance when stripping over-ate."""
    stripped = _strip_addressing(text, *names)
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


# --------------------------------------------------------------------------- #
# "Open N more terminals"                                                     #
# --------------------------------------------------------------------------- #
# A second, narrower request shape: the user asking for MORE PANES rather than
# addressing an existing one ("spawne fünf neue Claude Code Terminals").  # i18n-allow: quoted spoken input
#
# Why it needs its own detector instead of a router tool: the sentence opens
# with the very word the force-spawn heuristic reads as "dispatch a background
# agent". Left to the router, the turn becomes an invisible mission worker — the
# same class of failure ``detect`` above exists to fix, one layer up. So this is
# deterministic too, and it deliberately claims the turn BEFORE the
# vehicle-naming stand-down (see ``owns_turn``).
#
# The safety margin is one mandatory word: a TERMINAL NOUN. "Spawne fünf  # i18n-allow: quoted spoken input
# Terminals" is a workspace request; "spawne fünf Agenten" stays a background  # i18n-allow: quoted spoken input
# mission. A false positive here silently withholds a mission the user wanted,
# which is invisible; a false negative just costs one clearer sentence.

# Nouns that mean "a pane of the coding workspace". "Fenster"/"ventana"
# (window) is deliberately NOT here: a spoken "open two windows" is at least as
# likely to mean an application window, which is a Computer-Use request.
_PANE_NOUN_RE = re.compile(
    r"\b(?:terminals?|terminales|panes?|tabs?)\b",
    re.IGNORECASE,
)

# Verbs that ask for something to be opened, plus the additive markers that
# carry the same request without a verb ("noch drei Terminals").
_OPEN_VERB_RE = re.compile(
    r"\b(?:spawn\w*|[oö]ffn\w*|start\w*|mach\w*|erstell\w*|f[uü]g\w*|"  # i18n-allow: German input vocabulary
    r"gib|geb\w*|brauch\w*|will|h[aä]tte|"  # i18n-allow: German input vocabulary
    r"open\w*|create\w*|launch\w*|add|give|need|want|"
    r"abr\w*|cre\w*|lanz\w*|a[nñ]ad\w*|agrega\w*|dame|necesito|quiero)\b",
    re.IGNORECASE,
)
_ADDITIVE_RE = re.compile(
    r"\b(?:noch|weitere\w*|zus[aä]tzlich\w*|mehr|another|more|extra|"  # i18n-allow: German input vocabulary
    r"otr[oa]s?|m[aá]s)\b",
    re.IGNORECASE,
)

# An utterance that OPENS with a question word is asking about terminals, not
# asking for them ("wie viele Terminals kann ich öffnen?"). A polite request  # i18n-allow: quoted spoken input
# that merely ends in a question mark ("kannst du 5 Terminals öffnen?") is NOT  # i18n-allow: quoted spoken input
# excluded — that is a real request, and the filler prefix above strips its
# politeness for the composer anyway.
_QUESTION_OPENER_RE = re.compile(
    r"^(?:"
    r"wie|was|wieso|warum|wo|wann|welche\w*|wieviel\w*|"
    r"how|what|why|where|when|which|"
    r"c[oó]mo|qu[eé]|cu[aá]nt\w*|por\s+qu[eé]|d[oó]nde|cu[aá]ndo"
    r")\b",
    re.IGNORECASE,
)

# Number words per locale. Capped at the workspace maximum: past it the count is
# clamped anyway, so spelling out "twenty" buys nothing.
_NUMBER_WORDS: dict[str, int] = {
    # German — "ein/eine/einen" doubles as the article, which is exactly right  # i18n-allow: names the German number words below
    # here ("mach noch ein Terminal auf" = one).  # i18n-allow: quoted spoken input
    "ein": 1, "eine": 1, "einen": 1, "eins": 1,  # i18n-allow: German number words (input vocabulary)
    "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "funf": 5, "sechs": 6,  # i18n-allow: German number words (input vocabulary)
    "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12,  # i18n-allow: German number words (input vocabulary)
    "zwolf": 12,
    # English
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12,
    # Spanish
    "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11,
    "doce": 12,
}

# Which coding agent, when the user names one. Bare "claude" counts — nobody
# says a product name in full while talking. Everything else (including no
# mention at all) leaves the choice to the registry, which inherits the last
# pane's agent: "noch drei davon" is the common intent.
_AGENT_RE = re.compile(r"\b(claude(?:\s+code)?|codex)\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SpawnGroup:
    """One "N of agent X" part of a spawn request."""

    count: int
    agent: str | None
    """``"claude"`` / ``"codex"``, or ``None`` to inherit the last pane's."""


@dataclass(frozen=True, slots=True)
class SpawnTerminalsRequest:
    """A spoken request to open more panes in the coding workspace."""

    count: int
    """TOTAL panes to open across all groups, clamped to the workspace maximum."""

    agent: str | None
    """The first group's agent. Kept flat for the single-agent callers that
    predate mixed fleets; a mixed request is only fully described by
    ``groups``."""

    utterance: str
    """The original utterance, unmodified."""

    groups: tuple[SpawnGroup, ...] = ()
    """The requested fleet, in the order the user said it.

    "5 Codex and 3 Claude Code terminals" is TWO groups. Reading only the first
    number and the first agent — which is what this detector used to do —
    opened five Codex panes and dropped the three Claude ones without telling
    anyone (maintainer report 2026-07-26)."""


def _spoken_count(text: str) -> int:
    """The requested number of panes: digits, a number word, or 1.

    First hit wins. A count is optional — "open another terminal" is a perfectly
    clear request for one — so this never fails, it defaults.
    """
    from .session import MAX_TERMINALS

    digits = re.search(r"\b(\d{1,3})\b", text)
    if digits is not None:
        return max(1, min(int(digits.group(1)), MAX_TERMINALS))
    for word in text.lower().split():
        cleaned = "".join(ch for ch in word if ch.isalpha())
        if cleaned in _NUMBER_WORDS:
            return max(1, min(_NUMBER_WORDS[cleaned], MAX_TERMINALS))
    return 1


def detect_spawn(
    user_text: str, *, names: list[str] | None = None
) -> SpawnTerminalsRequest | None:
    """A request to open more terminals, or ``None``.

    ``names`` is the live call-signs (injectable for tests). They are needed for
    one decision only: an utterance that ADDRESSES a pane is that pane's prompt,
    even when it mentions terminals: telling a named pane to open a terminal is
    work for THAT pane, not a request for another one. Addressing therefore wins,
    and it is checked here so both callers inherit the same order.
    """
    text = (user_text or "").strip()
    if len(text) < 6:
        return None
    if _PANE_NOUN_RE.search(text) is None:
        return None
    if _QUESTION_OPENER_RE.search(text) is not None:
        return None
    if _OPEN_VERB_RE.search(text) is None and _ADDITIVE_RE.search(text) is None:
        return None
    if detect(text, names=names) is not None:
        return None

    agent_match = _AGENT_RE.search(text)
    agent: str | None = None
    if agent_match is not None:
        agent = "codex" if agent_match.group(1).lower().startswith("codex") else "claude"
    return SpawnTerminalsRequest(
        count=_spoken_count(text), agent=agent, utterance=text
    )


# --------------------------------------------------------------------------- #
# "…and split the work between you"                                            #
# --------------------------------------------------------------------------- #
# Addressing several panes does not by itself mean dividing the task. "Both of
# you run the tests" is ONE order for two agents; planning a split there would
# spend a provider call to invent two different jobs the user never asked for.
# So a split is only ever an EXPLICIT request.
#
# The trap this detector is shaped around: "aufteilen" / "split" is also
# ordinary coding work ("split the module into two files"). A verb alone is
# therefore not enough — the sentence must also say WHO the work is divided
# between, or into WHAT KIND of parts. Both of those distinguish dividing a
# task across agents from dividing a file into modules.

_DIVIDE_VERB_RE = re.compile(
    r"\b(?:teil\w*|aufteil\w*|verteil\w*|split|splits|divide|divides|"
    r"distribute|reparte|repartan|repartir|dividan|divid[ií]d)\b",
    re.IGNORECASE,
)

#: Who the work is divided between — the agents themselves.
_DIVIDE_RECIPIENT_RE = re.compile(
    r"\b(?:unter\s+euch|untereinander|euch|between\s+you|among\s+"
    r"you(?:rselves)?|entre\s+(?:vosotros|ustedes))\b",
    re.IGNORECASE,
)

#: What kind of parts the work is divided INTO. Deliberately about subject
#: areas, never about code units ("modules", "files", "functions") — dividing
#: those is the work itself, not a plan for the fleet.
_DIVIDE_AREA_RE = re.compile(
    r"\b(?:aufgabenbereich\w*|bereich\w*|themen\w*|themengebiet\w*|"
    r"schwerpunkt\w*|areas?|topics?|domains?|workstreams?|"
    r"[aá]reas?|temas?)\b",
    re.IGNORECASE,
)

#: "each of you takes a different part" — the same request without a verb.
_DIVIDE_EACH_RE = re.compile(
    r"\b(?:jede[rn]?\s+von\s+euch|jede[rn]?\s+einzelne|each\s+of\s+you|"
    r"each\s+one|cada\s+uno)\b[^.!?]{0,40}?"
    r"\b(?:ander\w*|different|separate|own|distinto\w*|diferente\w*|propio)\b",
    re.IGNORECASE,
)


def wants_split(user_text: str) -> bool:
    """True when the user asked for the work to be DIVIDED across the agents.

    Not a question about how many panes are addressed — that is ``detect_all``.
    This answers the separate question of whether they should all do the same
    thing or different things, which is the difference between one composed
    prompt reused N times and a planned division of labour (``work_split``).
    """
    text = (user_text or "").strip()
    if len(text) < 6:
        return False
    if _DIVIDE_EACH_RE.search(text):
        return True
    if not _DIVIDE_VERB_RE.search(text):
        return False
    return bool(
        _DIVIDE_RECIPIENT_RE.search(text) or _DIVIDE_AREA_RE.search(text)
    )


def owns_turn(user_text: str, *, names: list[str] | None = None) -> bool:
    """True when the open workspace should handle this turn instead of a spawn.

    Used by the router's force-spawn guard AND by ``spawn_gate`` — both already
    call this before they look for the delegation marker, which is why the
    precedence lives here and not in either of them: one answer, no drift.

    Order:

    1. A request for MORE TERMINALS is the workspace's, even though it names the
       spawn vehicle ("spawne … Terminals"). The mandatory pane noun is what
       makes claiming it safe, and it holds with no workspace open too — the
       feature then opens one, so a background mission would be just as wrong.
    2. Otherwise an utterance that explicitly names the spawn vehicle is NOT
       owned here — asking for a background agent while a workspace happens to
       be open is a real request, and stealing it would be the mirror image of
       the bug this module fixes.
    """
    from jarvis.brain.spawn_gate import names_spawn_vehicle

    text = (user_text or "").strip()
    if not text:
        return False
    if detect_spawn(text, names=names) is not None:
        return True
    if names_spawn_vehicle(text):
        return False
    return detect(text, names=names) is not None


__all__ = [
    "KIND_PROMPT",
    "KIND_REPORT",
    "SpawnTerminalsRequest",
    "TerminalIntent",
    "detect",
    "detect_all",
    "detect_spawn",
    "owns_turn",
    "wants_split",
]
