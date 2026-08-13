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

from .names import CALL_SIGN_WORD_RE, canonical_positions, resolve, spoken_positions

# Kinds of turn this module recognises.
KIND_PROMPT = "prompt"  # the user wants the agent to do something
KIND_REPORT = "report"  # the user asks what the agent is up to

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
    r"k[oö]e?nnen|k[oö]e?nnte|k[oö]e?nnten|m[uü]e?ssen|muss|"  # i18n-allow: input vocab
    r"m[uü]e?sste|m[uü]e?ssten|darf|"  # i18n-allow: input vocab
    r"d[uü]e?rfen|d[uü]e?rfte|d[uü]e?rften)\b",  # i18n-allow: input vocab
    r"\b{name}\b[^.!?]{{0,20}}?\b(?:should|can|could|please|must)\b",
    r"\b{name}\b[^.!?]{{0,20}}?\b(?:deber[ií]an?|pueden?|podr[ií]an?)\b",
    r"\b(?:lass|l[aä]sst)\b[^.!?]{{0,20}}?\b{name}\b",
    r"\blet\b[^.!?]{{0,20}}?\b{name}\b",
    # "prompt this terminal Kai …" / "prompte Mika …" / "instruct Nova to …".
    # The verb that literally MEANS handing a pane its work, and the one the
    # live 2026-07-27 failure was phrased with: "could you please prompt this
    # terminal Alex, do a deep dive …". It matched no shape here, so the
    # question mark at the end carried the turn into the read-only branch below,
    # nothing was typed into Alex, and the live model filled the silence by
    # claiming the agent had been briefed. Every neighbouring module already
    # knew this vocabulary (``clarify._WORKSPACE_NOUN_RE``,
    # ``_FLEET_BRIEF_RE``, ``_CLOSE_BRIEFING_RE``); only the list that decides
    # who gets the work did not.
    #
    # Stems, so "prompting" / "prompte" / "anprompten" / "promptea" all land.
    # Deliberately narrow: these verbs have no status-question reading, which is
    # what makes them safe to let outrank the question branch.
    r"\b(?:prompt\w*|anprompt\w*|instruct\w*|instruy\w*|anweis\w*)\b"
    r"[^.!?]{{0,40}}?\b{name}\b",
    # "schick(e) das an Kai" / "gib Mika …" / "frag Nova …" / "beauftrage Kai …"
    r"\b(?:schick|schicke|gib|gibt|frag|frage|beauftrag|beauftrage|"
    r"[uü]bergib|[uü]bergebe|weiterleit\w*)\b[^.!?]{{0,40}}?\b{name}\b",
    r"\b(?:send|give|ask|hand|forward|assign)\b[^.!?]{{0,40}}?\b{name}\b",
    r"\b(?:env[ií]a|manda|preg[uú]nta|pasa|asigna)\b[^.!?]{{0,40}}?\b{name}\b",
    # "an Kai:" / "in Mika:" / "bei Nova" / "über Kai" — a routing preposition
    # immediately in front of the call-sign.
    r"\b(?:an|in|bei|[uü]ber|via|to|into|en|a)\s+{name}\b",
    # "setz Kai auf den Wake-Bug an" / "put Nova on the audit" / "point Aria at
    # the failing test". The PREPOSITION is what makes these unambiguous — bare
    # "setz"/"put" is ordinary work talk ("put the file back"), but putting a
    # NAME on something is only ever an assignment.
    r"\b(?:setz|setze|ansetz\w*)\b[^.!?]{{0,20}}?\b{name}\b"  # i18n-allow: input vocab
    r"[^.!?]{{0,25}}?\bauf\b",  # i18n-allow: input vocab
    r"\b(?:put|point|set)\b\s+{name}\s+(?:on|at|onto|to)\b",
    r"\b(?:pon|p[oó]n|asigna|encarga\w*)\b[^.!?]{{0,25}}?\b{name}\b",
    # Vocative: the name opens the utterance ("Kai, mach mal …").
    r"^{name}\b\s*[,:]",
)

# Shapes that address a pane but are one step less certain than the ones above,
# so they only count when the utterance ALSO describes work (see
# ``_looks_like_instruction``). That second, independent piece of evidence is
# the whole safety argument: each template here has a reading that is not an
# assignment, and the work verb is what tells the two apart.
#
# They exist because the anchored shapes above share two blind spots that
# ordinary speech walks into constantly:
#
# 1. **The modal in front of the name.** "Kann Alex mal die Tests fixen?" is how
#    people actually hand over work out loud, and every template above expects
#    the modal AFTER the call-sign. Without this the sentence carried no
#    addressing shape at all, and its question mark then sent it to the
#    read-only branch — the same failure shape as the 2026-07-27 live miss.
# 2. **The German sentence bracket.** German puts the handing-over verb at the
#    END: "kannst du Alex bitte SAGEN, dass …", "würdest du Alex BITTEN, …".
#    Every handover template above looks for the verb in front of the name, so
#    the most natural polite German assignment matched nothing.
#
# "fragen"/"ask" is deliberately absent from the bracket list: "kannst du Alex
# fragen, was er macht" is a genuine status question, and the read branch has
# to keep it.
_WEAK_DIRECTIVE_TEMPLATES: tuple[str, ...] = (
    # Modal directly in front of the call-sign, all three locales.
    r"\b(?:kann|k[oö]e?nnte|soll|sollte|m[uü]e?sste|darf|d[uü]e?rfte|"  # i18n-allow: input vocab
    r"w[uü]e?rde|m[oö]e?ge|mag)\s+{name}\b",  # i18n-allow: input vocab
    r"\b(?:can|could|should|would|will|shall|must|may)\s+{name}\b",
    r"\b(?:puede|podr[ií]a|deber[ií]a|debe)\s+{name}\b",
    # "que Alex revise el área" — the Spanish way to order a third party about.
    # Anchored to the START of the utterance on purpose: mid-sentence "que" is
    # the ordinary relative pronoun ("quiero saber que Alex revisó"), and
    # matching it there would turn asking ABOUT a pane into typing at it.
    r"^(?:[¿¡]\s*)?que\s+{name}\b",
    # The German sentence bracket: name first, handing-over verb last.
    r"\b{name}\b[^.!?]{{0,30}}?\b(?:sagen|bitten|beauftragen|anweisen|"  # i18n-allow: input vocab
    r"[uü]bergeben|weiterleiten|zuweisen|prompten|briefen)\b",  # i18n-allow: input vocab
    # "have Alex review …" / "get Alex to fix …" / "I want Alex to …". The call
    # sign follows IMMEDIATELY: at any distance these verbs are ordinary speech
    # about a pane rather than an order to it ("I have a question about Alex").
    r"\b(?:have|get|need|want)\s+{name}\b",
)

# "what is Mika doing?" — a read, never a spawn either. Both word orders are
# covered per language: German and Spanish put the verb before the name ("was
# macht Mika"), English after it ("what is Mika doing").
#
# The optional ``ge`` prefix carries the German PERFECT, which is how spoken
# German asks about finished work at least as often as the present tense asks
# about running work: "was hat Dana gemacht" is the live 2026-07-27 miss, and
# `\bmacht` cannot match inside "gemacht" — there is no word boundary after
# "ge". One prefix covers "gemacht", "getan" and "gearbeitet" at once.
_REPORT_TEMPLATES: tuple[str, ...] = (
    r"\bwas\b[^.!?]{{0,30}}?\b{name}\b[^.!?]{{0,30}}?"
    r"\b(?:ge)?(?:macht|machen|tut|tun|tan|treibt|treiben|arbeitet|arbeiten)\b",
    r"\bwas\b[^.!?]{{0,20}}?"
    r"\b(?:ge)?(?:macht|machen|tut|tun|tan|treibt|treiben|arbeitet|arbeiten)\b"
    r"[^.!?]{{0,20}}?\b{name}\b",
    r"\b(?:what|how)\b[^.!?]{{0,30}}?\b{name}\b[^.!?]{{0,30}}?"
    r"\b(?:doing|done|do|did|up\s+to|going|at|been|built|found|changed)\b",
    r"\b(?:qu[eé]|c[oó]mo)\b[^.!?]{{0,30}}?\b{name}\b[^.!?]{{0,30}}?"
    r"\b(?:hacen?|haciendo|hecho|hizo|hicieron|van?)\b",
    r"\b(?:qu[eé]|c[oó]mo)\b[^.!?]{{0,20}}?"
    r"\b(?:hacen?|haciendo|hecho|hizo|hicieron|van?)\b"
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
        from .session import running_call_signs

        return running_call_signs()
    except Exception:  # noqa: BLE001 - the detector must never break a turn
        return []


def _name_alternatives(name: str) -> str:
    """Regex alternation matching ``name`` and its common transcript garble.

    A call-sign arrives through speech recognition, so "Kai" also shows up as
    "Kay" and "Mika" as "Micah". The phonetic folding in ``names.resolve`` covers
    that for *word* matching; here we only need a pattern loose enough to locate
    the name inside a sentence, and ``resolve`` re-checks the hit afterwards.

    The templates themselves stay anchored on the exact spelling — the garbled
    spellings are handled once, up front, by ``_canonical_text``, so every
    template inherits the tolerance instead of each one re-implementing it.
    """
    escaped = re.escape(name)
    return escaped


def _is_person_of_the_world(text: str, start: int, end: int) -> bool:
    """Whether the word at ``[start:end)`` carries a surname, i.e. is a person.

    Delegated to ``clarify`` so ONE rule decides it, and failure-tolerant on
    purpose: this guard may only ever WITHHOLD a pane reading, so a fault has
    to degrade to "not a person" rather than break detection.
    """
    try:
        from .clarify import is_part_of_full_name

        return is_part_of_full_name(text, start, end)
    except Exception:  # noqa: BLE001 - a guard fault must never break a turn
        return False


def _is_outside_world_talk(text: str) -> bool:
    """Whether the turn is about the world rather than the workspace.

    Imported lazily and failure-tolerant on purpose: the guard may only ever
    WITHHOLD a collective address, so a fault here has to degrade to the
    historical behaviour rather than break detection.
    """
    try:
        from .clarify import is_outside_world_talk

        return is_outside_world_talk(text)
    except Exception:  # noqa: BLE001 - a guard fault must never break a turn
        return False


def _canonical_text(text: str, candidates: list[str]) -> str:
    """The utterance with every same-sounding spelling replaced by the call-sign.

    Closes a gap that made the phonetic tolerance half-real: ``resolve`` maps
    "Elis" and "Ellys" onto "Ellis" without hesitating, but the addressing
    templates are built from ``re.escape(name)`` and therefore only ever matched
    the EXACT spelling. So "sag Elis, sie soll die Tests fixen" carried a
    textbook addressing shape, named a pane the resolver recognised — and was
    detected as nothing at all, because the one template that could have caught
    it was looking for a second "l".

    Only spellings that fold to the SAME SOUND are substituted (``fuzzy=False``).
    A merely similar word must not be rewritten into a call-sign: that is the
    branch where ordinary speech collides with the pool ("allen" scores 0.750
    against "Alex"), and rewriting it here would hand every template a name the
    user never said.

    ONE position is exempt, because there the sentence itself vouches for the
    word: whatever directly follows a briefing verb ("prompt …", "brief …",
    "beauftrage …") is being named as the addressee. Nothing else can stand
    there. So a garbled call-sign is admitted on the FUZZY band at that spot and
    at no other — which is what rescues the maintainer's own example, "hey,
    prompte adex" (2026-07-28): "adex" reaches "Alex" only fuzzily, so every
    template missed it, the last-resort branch rejected it for not being
    certain, and the turn addressed nothing at all.

    The collision risk that makes fuzzy matching dangerous elsewhere does not
    reach here: "unten"/"keine" score into the pool as ordinary sentence words,
    and an ordinary sentence word does not sit immediately behind "prompte".

    The SPOKEN POSITIONS are folded in first, by the same argument one level
    up: "prompte Terminal eins" and "prompte T1" are the same instruction, and
    rewriting the long form into the call-sign once here is what saves every
    template below from having to know that "terminal one", "tee one" and "the
    first terminal" all mean T1.
    """
    if not candidates:
        return text

    text = canonical_positions(text, candidates)

    briefed: set[int] = {match.start("word") for match in _BRIEFED_NAME_RE.finditer(text)}

    def _replace(match: re.Match[str]) -> str:
        word = match.group(0)
        hit = resolve(word, candidates, fuzzy=match.start() in briefed)
        return hit if hit is not None else word

    return _WORD_RE.sub(_replace, text)


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

#: How people point AT a pane before naming it: "prompt THIS TERMINAL Alex …".
#: Part of the address, never part of the work — leaving it in briefed an agent
#: with "this terminal do a deep dive", which reads like an instruction to open
#: one.
#: Wrapped as ONE optional group on purpose: a trailing ``?`` at the use site
#: would bind to this pattern's last token instead of the whole phrase, making
#: the pane noun mandatory and stripping nothing at all.
_ADDRESS_PANE_NOUN = (
    r"(?:"
    r"(?:(?:this|that|the|dieses?|diesem|das|dem|el|la|ese|este)\s+)?"  # i18n-allow: input vocab
    r"(?:terminals?|terminales|panes?|tabs?)\s+"
    r")?"
)


def _strip_addressing(text: str, *names: str) -> str:
    """The utterance with the addressing preamble and the call-signs removed.

    Takes every addressee at once: with a fan-out the remaining names are part
    of the address list, not part of the work, and leaving them in would have
    the composer brief Iris about Bruno.
    """
    cleaned = re.sub(
        r"\b(?:sag|sage|sagst|tell|dile|d[ií]gale|schick|schicke|send|gib|give|"
        r"frag|frage|ask|lass|let|beauftrage?|assign|env[ií]a|manda|"
        r"prompt\w*|anprompt\w*|instruct\w*|instruy\w*|anweis\w*)\b\s*",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    for name in names:
        cleaned = re.sub(
            rf"\b{_ADDRESS_PANE_NOUN}{_ADDRESS_PREPOSITION}?\s*"
            rf"{re.escape(name)}\b\s*[,:]?\s*",
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
        r"\b(?:jede[nr]?\s+(?:davon|dieser)|alle\s+davon)\b[^.!?]{0,50}?"
        r"\b(?:prompt\w*|anweis\w*|beauftrag\w*)\b",  # i18n-allow: input vocab
        r"\b(?:prompt\w*|anweis\w*|beauftrag\w*)\b[^.!?]{0,30}?"
        r"\b(?:jede[nr]?\s+(?:davon|dieser)|alle\s+davon)\b",  # i18n-allow: input vocab
        r"\b(?:each\s+(?:one|of\s+them)|all\s+of\s+them)\b[^.!?]{0,50}?"
        r"\b(?:prompt|brief|instruct|assign)\w*\b",
        r"\b(?:prompt|brief|instruct|assign)\w*\b[^.!?]{0,30}?"
        r"\b(?:each\s+(?:one|of\s+them)|all\s+of\s+them)\b",
        # The collective pronoun is the direct object: "prompt all except T2".
        # Requiring the briefing verb immediately beside it avoids reading
        # ordinary work nouns ("fix all tests") as a workspace fan-out.
        r"\b(?:alle|allen)\b(?:[\s,]+(?:terminals?|panes?))?[\s,]*"
        r"\b(?:prompt|brief|anweis|beauftrag)\w*\b",  # i18n-allow: input vocab
        r"\b(?:prompt|brief|instruct|assign)\w*\b[\s,]*"
        r"\b(?:all|everyone|everybody)\b(?:\s+(?:terminals?|panes?))?",
        r"\b(?:prompt|instruy|asigna|encarga)\w*\b[\s,]*(?:a\s+)?"
        r"\b(?:todos|todas)\b(?:\s+(?:terminales|paneles))?",
        r"^(?:und\s+)?\b(?:alle|allen)\b[^.!?]{0,20}?"  # i18n-allow: input vocab
        r"\b(?:soll|sollen|m[uü]ssen|k[oö]nnen)\b",  # i18n-allow: input vocab
        r"^(?:and\s+)?\b(?:everyone|everybody|all\s+of\s+them)\b[^.!?]{0,20}?"
        r"\b(?:should|must|please|can)\b",
        r"\b(?:diles?|preg[uú]nta)\b[\s,]*\b(?:a\s+)?todos\b",
        r"^\b(?:todos|todas)\b[^.!?]{0,20}?\b(?:deben|deber[ií]an)\b",
    )
)

# A named pane can be mentioned precisely because it must NOT receive the
# fleet instruction. These are selection operators, not task negations. The
# difference matters: "prompt T12 not to delete files" still addresses T12,
# while "prompt everyone except T12" addresses everyone else.
_EXCLUSION_CUE_RE = re.compile(
    r"\b(?:außer|ausser|ausgenommen|except(?:\s+for)?|excluding|apart\s+from|"
    r"but\s+not|not|nicht|excepto|salvo|menos|sino)\b",
    re.IGNORECASE,
)

_EXCLUSION_GAP_RE = re.compile(
    r"^[\s,:;\-]*(?:(?:the|den|dem|die|das|los|las)\s+)?"
    r"(?:(?:terminals?|terminales|panes?|tabs?)\s+)?$",
    re.IGNORECASE,
)

_OTHER_PANES_RE = re.compile(
    r"\b(?:alle\s+anderen|all\s+(?:the\s+)?others|everyone\s+else|"
    r"todos\s+los\s+dem[aá]s|todas\s+las\s+dem[aá]s)\b",
    re.IGNORECASE,
)

_COLLECTIVE_EXCEPTION_RE = re.compile(
    r"\b(?:alle|allen)(?:\s+(?:terminals?|panes?))?[\s,]+"
    r"(?:außer|ausser|ausgenommen)|"
    r"\b(?:all|everyone|everybody)(?:\s+(?:terminals?|panes?))?[\s,]+"
    r"(?:except(?:\s+for)?|excluding|apart\s+from|but\s+not)|"
    r"\b(?:todos|todas)(?:\s+(?:los|las))?(?:\s+(?:terminales|paneles))?"
    r"[\s,]+(?:excepto|salvo|menos)",
    re.IGNORECASE,
)

_NO_WORK_AFTER_NAME_RE = re.compile(
    r"^[^.!?;]{0,35}\b(?:"
    r"nichts\s+(?:mehr\s+)?(?:machen|tun)|"
    r"nicht\s+(?:mehr\s+)?(?:prompt\w*|weitermach\w*|weiterarbeit\w*)|"
    r"do\s+nothing|nothing\s+to\s+do|"
    r"do\s+not\s+(?:prompt|continue|keep\s+working)|"
    r"not\s+(?:prompt|continue|keep\s+working)|"
    r"nada\s+que\s+hacer|no\s+(?:los?\s+|las?\s+)?"
    r"(?:instruy\w*|asign\w*|contin[uú]\w*)"
    r")\b",
    re.IGNORECASE,
)

_TRAILING_NEGATION_RE = re.compile(
    r"^[\s,:\-]*(?:aber\s+)?(?:nicht|not|no)\s*[.!?]?\s*$",
    re.IGNORECASE,
)

_TASK_AFTER_EXCLUSION_RE = re.compile(
    r"^[\s,:\-]*(?:"
    r"(?:dass|damit)\s+(?:sie|ihr|diese)?\s*|"
    r"that\s+(?:they|those)?\s*|to\s+|"
    r"que\s+(?:ellos|ellas|estos|estas)?\s*"
    r")(?P<task>.+)$",
    re.IGNORECASE | re.DOTALL,
)

_OTHER_PANES_TASK_RE = re.compile(
    r"\b(?:alle\s+anderen|all\s+(?:the\s+)?others|everyone\s+else|"
    r"todos\s+los\s+dem[aá]s|todas\s+las\s+dem[aá]s)\b[^.!?;,]{0,12}?"
    r"\b(?:soll(?:en)?|m[uü]ss(?:en)?|should|must|deben|deber[ií]an)\b\s+"
    r"(?P<task>[^.!?;,]+)",
    re.IGNORECASE,
)

# One candidate call-sign word. Shared with ``clarify`` and the resolver
# (``names.CALL_SIGN_WORD_RE``) so the three cannot disagree about where a name
# starts and ends — a positional call-sign is letters PLUS digits, and a
# letters-only tokenizer silently cut "T1" down to "T".
_WORD_RE = CALL_SIGN_WORD_RE

#: Verbs that can ONLY mean "hand this pane its work". The templates above are
#: anchored on the call-sign and therefore bounded by distance, so a longer way
#: of saying the same thing ("could you prompt the terminal that is called Alex
#: to …") slips past every one of them. This is the un-anchored backstop for
#: that, and its whole safety argument is the word list: unlike "frag" or "ask",
#: none of these has a status-question reading, so "kannst du Alex fragen, was
#: er macht" — a genuine read — is untouched by it.
_BRIEFING_VERB_RE = re.compile(
    r"\b(?:prompt\w*|anprompt\w*|instruct\w*|instruy\w*|anweis\w*|"  # i18n-allow: input vocab
    r"beauftrag\w*|briefing|briefe\w*|assign\w*|encarga\w*)\b",  # i18n-allow: input vocab
    re.IGNORECASE,
)

#: The ADDRESSEE SLOT: the word a briefing verb hands its work to. Whatever
#: stands here is being named as the pane — that is the only thing the position
#: admits — so ``_canonical_text`` may repair a garbled call-sign here on the
#: fuzzy band without opening the door to the collisions that band causes
#: elsewhere. The optional filler is exactly what people put between the verb
#: and the name and nothing more: an article and/or a pane noun
#: ("prompt THE TERMINAL Alex"), each at most once.
_BRIEFED_NAME_RE = re.compile(
    r"\b(?:prompt\w*|anprompt\w*|instruct\w*|instruy\w*|anweis\w*|"  # i18n-allow: input vocab
    r"beauftrag\w*|briefe\w*|assign\w*|encarga\w*)\b\s+"  # i18n-allow: input vocab
    r"(?:(?:this|that|the|dieses?|diesem|den|dem|"  # i18n-allow: input vocab
    r"das|der|el|la|ese|este)\s+)?"  # i18n-allow: input vocab
    r"(?:(?:terminals?|terminales|panes?|tabs?)\s+)?"
    r"(?P<word>[^\W\d_]+\d*)",
    re.IGNORECASE,
)

# A singular pane reference whose meaning comes from the UI rather than from a
# spoken call-sign. This is intentionally narrower than a bare "terminal": it
# requires a demonstrative/definite article or an on-screen word, and is only
# consulted when chat view reports one visible pane. Matching vocabulary, not
# prose, across every supported input locale.
_VISIBLE_TERMINAL_RE = re.compile(
    r"(?:"
    r"\b(?:this|that|the|current|visible|"
    r"dies(?:es|em|en)?|das|der|dem|den|aktuelle[nmrs]?|sichtbare[nmrs]?|"
    r"el|este|ese|actual|visible)\s+(?:terminal|pane|tab)\b"
    r"|"
    r"\b(?:terminal|pane|tab)\b[^.!?]{0,20}?"
    r"\b(?:here|on\s+screen|hier|im\s+bild|aqu[ií]|en\s+pantalla)\b"
    r")",
    re.IGNORECASE,
)

_VISIBLE_REPORT_RE = re.compile(
    r"\b(?:"
    r"what|how|status|progress|doing|done|stuck|"
    r"was|wie|status|fortschritt|macht|tut|l[aä]uft|los|fertig|h[aä]ngt|"
    r"qu[eé]|c[oó]mo|estado|progreso|haciendo|hecho|atascado"
    r")\b",
    re.IGNORECASE,
)


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


def _people_of_the_world(text: str, candidates: list[str]) -> set[str]:
    """Call-signs this utterance uses as a PERSON's first name, not as a pane.

    A call-sign is a single given name, so "Dana Schmidt" is a human being and
    "Dana" is the terminal. Only names whose EVERY occurrence carries a surname
    count: a sentence that says both ("ask Dana whether Dana Schmidt replied")
    still addresses the pane.

    Applied to the REPORT side alone. The acting shapes keep their existing
    evidence untouched, because German capitalizes nouns and "sag Dana
    Bescheid" would read as a surname — withholding a report costs a sentence,
    withholding a prompt costs the user's work.
    """
    as_person: set[str] = set()
    as_pane: set[str] = set()
    for match in _WORD_RE.finditer(text):
        hit = resolve(match.group(0), candidates, fuzzy=False)
        if hit is None:
            continue
        if _is_person_of_the_world(text, match.start(), match.end()):
            as_person.add(hit)
        else:
            as_pane.add(hit)
    return as_person - as_pane


def _questioned_panes(text: str, candidates: list[str]) -> set[str]:
    """Panes a QUESTION asks about purely by naming them, or ``set()``.

    The general form of the report templates, and the reason they no longer
    have to enumerate tenses: what makes "was hat Dana gemacht" a question
    about a terminal is not the verb, it is that a running pane is called Dana
    and the sentence is a question.

    Three conditions, each removing a way of being wrong:

    1. **it is a question** — a trailing "?" or an interrogative opening it.
       A statement that merely mentions a pane is not a request for a status
       report, and the acting branches already own the ones that are;
    2. **the name is CERTAIN** — the exact call-sign or a spelling that folds
       to the same sound. Here the name is the whole case, so the fuzzy band
       is not admissible: measured against the shipping pool, ordinary speech
       reaches it ("unten" scores into "Hunter"), and an uncertain name is the
       clarify path's question to ask, not this path's answer to give;
    3. **it is not a person's full name** — "was hat Dana Schmidt gemacht" is
       about a human being, and a surname is the signal that says so.

    A question that HANDS WORK OVER is none of this branch's business, however
    it is worded. Politeness is the ordinary way to give an order out loud —
    "could you please prompt this terminal Alex, do a deep dive …?" is an
    instruction with a question mark on it — and reading it as a status request
    is how the live 2026-07-27 turn ended with an idle terminal and a spoken
    claim that the agent had been briefed. The anchored templates catch these
    when the verb sits near the name; ``_BRIEFING_VERB_RE`` catches the rest.
    """
    if not candidates:
        return set()
    if not (text.rstrip().endswith("?") or _QUESTION_OPENER_RE.search(text.lstrip())):
        return set()
    if _BRIEFING_VERB_RE.search(text):
        return set()
    return {name for _, _, name in _mentions(text, candidates, fuzzy=False)} - _people_of_the_world(
        text, candidates
    )


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


def _excluded_by_cue(text: str, mentions: list[tuple[int, int, str]]) -> set[str]:
    """Names immediately following an explicit set-subtraction cue."""
    anchors: set[str] = set()
    for cue in _EXCLUSION_CUE_RE.finditer(text):
        for start, _, name in mentions:
            if start < cue.end():
                continue
            if _EXCLUSION_GAP_RE.fullmatch(text[cue.end() : start]):
                anchors.add(name)
            break
    return _coordinated_group(mentions, text, anchors)


def _coordination_groups(
    mentions: list[tuple[int, int, str]], text: str
) -> list[list[tuple[int, int, str]]]:
    """Partition mentioned names into adjacent spoken enumerations."""
    if not mentions:
        return []
    groups = [[mentions[0]]]
    for previous, current in zip(mentions, mentions[1:], strict=False):
        if _COORDINATION_RE.match(text[previous[1] : current[0]]):
            groups[-1].append(current)
        else:
            groups.append([current])
    return groups


def _excluded_by_no_work_clause(text: str, mentions: list[tuple[int, int, str]]) -> set[str]:
    """Names in a collective sentence whose clause says Jarvis should skip them."""
    excluded: set[str] = set()
    for group in _coordination_groups(mentions, text):
        suffix = text[group[-1][1] :]
        sentence = re.split(r"[.!?;]", suffix, maxsplit=1)[0]
        if _NO_WORK_AFTER_NAME_RE.search(sentence) or _TRAILING_NEGATION_RE.fullmatch(
            suffix.strip()
        ):
            excluded.update(name for _, _, name in group)
    return excluded


def _selection_instruction(
    text: str,
    mentions: list[tuple[int, int, str]],
    excluded: set[str],
) -> str:
    """Best-effort task text after a target selector and its exception list."""
    excluded_mentions = [item for item in mentions if item[2] in excluded]
    if excluded_mentions:
        suffix = text[max(end for _, end, _ in excluded_mentions) :].strip()
        match = _TASK_AFTER_EXCLUSION_RE.match(suffix)
        if match:
            task = " ".join(match.group("task").split())
            if len(task) >= _MIN_INSTRUCTION_CHARS:
                return task
    if match := _OTHER_PANES_TASK_RE.search(text):
        task = " ".join(match.group("task").split())
        if len(task) >= _MIN_INSTRUCTION_CHARS:
            return task
    mentioned_names = [name for _, _, name in mentions]
    return _useful_instruction(text, *mentioned_names)


def detect_all(user_text: str, *, names: list[str] | None = None) -> list[TerminalIntent]:
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

    # Everything below reads the CANONICAL utterance — same words, but every
    # same-sounding spelling of a call-sign replaced by the call-sign itself, so
    # a transcript that wrote "Elis" is matched by the templates built for
    # "Ellis". Only ``utterance`` keeps the user's original wording, because
    # that is what the prompt composer must read back.
    working = _canonical_text(text, candidates)

    # A report question is checked first: "what is Kai doing?" also matches the
    # looser "ist Kai …" directive shape, and answering it as a prompt would type
    # the question into the agent.
    # A first name carrying a surname is a human being, so it is struck from
    # the READ side before anything else looks at it. Without this, widening
    # the report shapes to the German perfect turned "was hat Dana Schmidt
    # gemacht" — a question about a person — into a status request for the
    # pane called Dana.
    people = _people_of_the_world(working, candidates)
    report_anchors = {
        name for name, pattern in _compile(_REPORT_TEMPLATES, candidates) if pattern.search(working)
    } - people
    prompt_anchors = {
        name
        for name, pattern in _compile(_DIRECTIVE_TEMPLATES, candidates)
        if pattern.search(working)
    }
    # The weaker shapes need the utterance to describe work as well; see
    # ``_WEAK_DIRECTIVE_TEMPLATES`` for why each of them cannot stand alone.
    weak_prompt_anchors: set[str] = set()
    if _looks_like_instruction(working) or _BRIEFING_VERB_RE.search(working):
        weak_prompt_anchors = {
            name
            for name, pattern in _compile(_WEAK_DIRECTIVE_TEMPLATES, candidates)
            if pattern.search(working)
        }
    mentions = _mentions(working, candidates)
    explicit_exclusions = _excluded_by_cue(working, mentions)
    no_work_exclusions = _excluded_by_no_work_clause(working, mentions)
    collective = (
        any(pattern.search(working) for pattern in _EVERYONE_TEMPLATES)
        or bool(_COLLECTIVE_EXCEPTION_RE.search(working) and explicit_exclusions)
        or bool(_OTHER_PANES_RE.search(working) and no_work_exclusions)
    )
    exclusions = explicit_exclusions | (no_work_exclusions if collective else set())

    # The collective address reaches EVERY open pane, so it is the costliest
    # thing a misheard word can trigger — a question about a public figure that
    # speech recognition turned into "sag allen …" would brief the whole
    # workspace with it. Checked here rather than inside the templates because
    # it is a property of the sentence, not of any one addressing shape.
    if collective and not _is_outside_world_talk(working):
        kind = (
            KIND_REPORT
            if report_anchors and not (prompt_anchors or weak_prompt_anchors)
            else KIND_PROMPT
        )
        selected = [name for name in candidates if name not in exclusions]
        if not selected:
            return []
        shared = (
            "" if kind == KIND_REPORT else _selection_instruction(working, mentions, exclusions)
        )
        return [
            TerminalIntent(
                terminal=name,
                kind=kind,
                instruction=shared,
                utterance=text,
            )
            for name in selected
        ]

    if report_anchors:
        kind = KIND_REPORT
        anchors = report_anchors
    elif prompt_anchors:
        kind = KIND_PROMPT
        anchors = prompt_anchors
    elif weak_prompt_anchors:
        # Above the question branch on purpose: politeness and the German
        # sentence bracket both produce sentences that END in a question mark
        # while HANDING WORK OVER, and reading those as status requests is the
        # exact failure this whole area keeps coming back to. Below the report
        # shapes, so "ist Alex fertig?" is untouched.
        kind = KIND_PROMPT
        anchors = weak_prompt_anchors
    elif asked_about := _questioned_panes(working, candidates):
        # A QUESTION that names a running pane is about that pane. This is the
        # maintainer's own rule (2026-07-27): while a coding workspace is open,
        # a call-sign belongs to the terminal carrying it, even though somebody
        # out in the world shares the name.
        #
        # It exists because the template lists above can only ever cover the
        # verbs somebody thought of, and the live miss proved how thin that
        # cover is: "was hat Dana gemacht" — ordinary spoken German, perfect
        # tense — matched no report shape, so the turn was not the workspace's
        # at all and the live model answered that it did not know any Dana.
        # Every tense, every phrasing and every locale would each have needed
        # its own template; the NAME is the evidence that generalises.
        #
        # Safe to be this generous only because a report is READ-ONLY: the
        # caller answers from what the pane printed and never types into it.
        # The acting branches keep their stricter evidence, which is why this
        # sits BELOW them — an utterance carrying a real addressing shape is
        # still that shape, question mark or not.
        kind = KIND_REPORT
        anchors = asked_about
        mentions = [item for item in mentions if item[2] in asked_about]
    elif mentions and (_looks_like_instruction(working) or _BRIEFING_VERB_RE.search(working)):
        # Last resort: the utterance names terminals and reads as an
        # instruction. Requiring a verb keeps a passing mention ("Alex is a
        # nice name") from being addressed. A briefing verb counts as that verb
        # in its own right — "prompt Alex" is a complete order even though the
        # work itself is described with nouns.
        #
        # This is the ONLY branch where the call-sign is the whole case: no
        # "tell X to …", no "X should …" — just a name and a verb. That makes
        # it the branch a merely SIMILAR word can win, and the names are short
        # enough for ordinary speech to score close: measured against the
        # shipping pool "unten" reaches "Hunter" and "dann" reaches "Dana"; the
        # "keine" once reached "Kai" and briefed an unnamed agent.  # i18n-allow: transcript tokens
        # So here — and only here — the name has
        # to be exact (or fold to the same sound, which is what carries a
        # garbled transcript). An addressing shape, being independent evidence,
        # still admits a fuzzy call-sign in the branches above.
        certain = _mentions(working, candidates, fuzzy=False)
        if not certain:
            return []
        mentions = certain
        kind = KIND_PROMPT
        anchors = {name for _, _, name in certain}
    else:
        return []

    anchors -= explicit_exclusions
    addressed = _coordinated_group(mentions, working, anchors) - explicit_exclusions
    ordered = [name for _, _, name in mentions if name in addressed]
    # An anchor matched by a template but never located as a word (a garbled
    # transcript the templates still caught) keeps its place at the end.
    ordered += [name for name in anchors if name not in ordered]

    shared = (
        ""
        if kind == KIND_REPORT
        else (
            _selection_instruction(working, mentions, explicit_exclusions)
            if explicit_exclusions
            else _useful_instruction(working, *ordered)
        )
    )
    return [
        TerminalIntent(terminal=name, kind=kind, instruction=shared, utterance=text)
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


def detect_visible(
    user_text: str,
    *,
    terminal: str | None = None,
    names: list[str] | None = None,
) -> TerminalIntent | None:
    """Resolve "this terminal" to the one pane visibly filling chat view.

    Explicit call-signs always win. With no supplied ``terminal`` the active
    session is consulted, but only its ephemeral chat-stage context; grid view
    deliberately has no implicit target.
    """
    text = (user_text or "").strip()
    if len(text) < 3 or _VISIBLE_TERMINAL_RE.search(text) is None:
        return None
    candidates = _running_names() if names is None else list(names)
    if not candidates:
        return None
    # "Prompt T1, not this terminal" and every ordinary explicit address keep
    # the established resolver's answer, even while another pane is on stage.
    if detect_all(text, names=candidates):
        return None
    selected = terminal
    if selected is None:
        try:
            from .session import get_registry

            current = get_registry().session
            pane = current.contextual_terminal() if current is not None else None
            selected = pane.name if pane is not None else None
        except Exception:  # noqa: BLE001 - optional UI context, never fatal
            return None
    if not selected or selected not in candidates:
        return None
    if _BRIEFING_VERB_RE.search(text):
        kind = KIND_PROMPT
    elif _VISIBLE_REPORT_RE.search(text):
        kind = KIND_REPORT
    elif _looks_like_instruction(text):
        kind = KIND_PROMPT
    else:
        return None
    return TerminalIntent(
        terminal=selected,
        kind=kind,
        instruction=text if kind == KIND_PROMPT else "",
        utterance=text,
    )


# Below this, stripping has eaten the task rather than the addressing — "schick
# das an Kai" reduces to "das". The full utterance is then the honest input; the
# composer reads both and can tell them apart.
_MIN_INSTRUCTION_CHARS = 12


def _useful_instruction(text: str, *names: str) -> str:
    """The stripped instruction, or the whole utterance when stripping over-ate."""
    stripped = _strip_addressing(text, *names)
    return stripped if len(stripped) >= _MIN_INSTRUCTION_CHARS else text


# Umlauts are written ``[uü]e?`` throughout this module rather than ``[uü]``:
# voice transcripts carry the real character, but the same detectors serve
# TYPED turns, and a keyboard without a German layout produces "pruefen" /
# "uebernimmt". The bare class covered the dropped-umlaut spelling ("prufen")
# and missed the transliterated one, which is the spelling people actually
# type.
_INSTRUCTION_VERB_RE = re.compile(
    # Stems, not fixed forms. German hands work over in the INFINITIVE at least
    # as often as in the imperative — "sag Ellis, sie soll die Tests fixen" —
    # and a `fix|fixe` alternative matches neither "fixen" nor "Tests". The
    # effect was invisible and total: such a turn produced no addressed pane at
    # all, which is the same silent nothing this module was written to end.
    # ``analy[sz]`` rather than ``analysier``: the German stem alone missed the
    # plain English "analyze the codebase", which is how the live 2026-07-27
    # utterance described its work.
    r"\b(?:mach\w*|bau\w*|schreib\w*|fix\w*|beheb\w*|pr[uü]e?f\w*|"
    r"check\w*|analy[sz]\w*|review\w*|refactor\w*|test\w*|start\w*|implementier\w*|"
    r"[aä]e?nder\w*|erstell\w*|f[uü]e?g\w*|entfern\w*|l[oö]e?sch\w*|untersuch\w*|"
    r"repariere?\w*|debugg?\w*|commit\w*|"
    # Taking a job on is describing work just as much as doing it is: "Alex
    # übernimmt den Wake-Bug" is an assignment, and without the verb the
    # sentence carried a call-sign and nothing else.
    r"[uü]e?bernimm\w*|[uü]e?bernehm\w*|aktualisier\w*|schau\w*|guck\w*|"  # i18n-allow: input vocab
    r"k[uü]e?mmer\w*|"  # i18n-allow: input vocab
    r"audit\w*|inspect\w*|verify|update|handle|cover|take\s+over|"
    r"build|write|make|run|implement\w*|change|create|add|remove|delete|"
    r"investigate|look|read|find|search|explain|document|hunt\w*|"
    # "do a deep dive on X" describes the work with a noun phrase, and it is
    # the single most common way this workspace is asked for anything — yet
    # every verb in this list missed it, so "would you have Alex do a deep
    # dive?" carried no work at all and stayed a status question.
    r"deep[\s-]?dive|root[\s-]?cause|"
    # Spanish carries an order to a third party in the subjunctive — "que Lee
    # REVISE el área" is the ordinary way to say "have Lee review the area" —
    # so the stems have to reach past the 2nd-person imperative. Fixed forms
    # only made the Spanish half of every detector that consults this quietly
    # weaker than the German and English halves.
    r"haz|hag[ao]\w*|escrib\w*|crea|revis\w*|arregl\w*|ejecut\w*|analic\w*|analiz\w*)\b",
    re.IGNORECASE,
)


def _looks_like_instruction(text: str) -> bool:
    return bool(_INSTRUCTION_VERB_RE.search(text))


# --------------------------------------------------------------------------- #
# "Open N more terminals"                                                     #
# --------------------------------------------------------------------------- #
# A second, narrower request shape: the user asking for MORE PANES rather than
# addressing an existing one ("spawne fünf neue Claude Code  # i18n-allow: quoted spoken input
# Terminals").
#
# Why it needs its own detector instead of a router tool: the sentence opens
# with the very word the force-spawn heuristic reads as "dispatch a background
# agent". Left to the router, the turn becomes an invisible mission worker — the
# same class of failure ``detect`` above exists to fix, one layer up. So this is
# deterministic too, and it deliberately claims the turn BEFORE the
# vehicle-naming stand-down (see ``owns_turn``).
#
# The safety margin is one mandatory word: a TERMINAL NOUN.
# "Spawne fünf Terminals" is a workspace request;  # i18n-allow: quoted spoken input
# "spawne fünf Agenten" stays a background mission.  # i18n-allow: quoted spoken input
# A false positive here silently withholds a mission the user wanted,
# which is invisible; a false negative just costs one clearer sentence.

# Nouns that mean "a pane of the coding workspace". "Fenster"/"ventana"
# (window) is deliberately NOT here: a spoken "open two windows" is at least as
# likely to mean an application window, which is a Computer-Use request.
_PANE_NOUN_RE = re.compile(
    r"\b(?:terminals?|terminales|panes?|tabs?)\b",
    re.IGNORECASE,
)

# ``tab`` is shared vocabulary: inside the coding workspace it can mean a pane,
# but beside an explicit browser surface it means a browser tab. The workspace
# must never win that collision merely because it happens to be open. This is a
# context veto rather than deleting ``tab`` from the pane vocabulary, so the
# established shorthand "open three more tabs" keeps working.
_BROWSER_TAB_CONTEXT_RE = re.compile(
    r"\b(?:browser|chrome|firefox|safari|microsoft\s+edge|web\s*site|webpage|"
    r"incognito|private|guest|"
    r"webseite|internetseite|inkognito|privat|gast|"  # i18n-allow: input vocab
    r"navegador|sitio\s+web|p[aá]gina\s+web|"  # i18n-allow: input vocab
    r"inc[oó]gnit\w*|privad\w*)\b",  # i18n-allow: input vocab
    re.IGNORECASE,
)


def _pane_nouns(text: str) -> list[re.Match[str]]:
    """Return coding-pane nouns, excluding tabs owned by a named browser."""
    browser_owns_tabs = _BROWSER_TAB_CONTEXT_RE.search(text) is not None
    return [
        match
        for match in _PANE_NOUN_RE.finditer(text)
        if not (browser_owns_tabs and match.group(0).casefold() in {"tab", "tabs"})
    ]


# Verbs that ask for something to be opened, plus the additive markers that
# carry the same request without a verb ("noch drei Terminals").
_OPEN_VERB_RE = re.compile(
    # German separable verbs arrive both ways: "mach zwei Terminals auf" splits
    # and matches "mach", while "zwei Terminals aufmachen" keeps the prefix
    # attached — and a word boundary cannot see the "mach" inside it. Without
    # the prefixes here the whole request found no verb at all and was answered
    # with nothing (maintainer report 2026-07-28).
    r"\b(?:spawn\w*|[oö]ffn\w*|start\w*|"
    r"(?:auf|an|zu|hoch)?mach\w*|erstell\w*|f[uü]g\w*|"  # i18n-allow: input vocab
    r"gib|geb\w*|brauch\w*|will|h[aä]tte|"  # i18n-allow: German input vocabulary
    r"open\w*|create\w*|launch\w*|add|give|need|want|"
    r"abr\w*|cre\w*|lanz\w*|a[nñ]ad\w*|agrega\w*|dame|necesito|quiero)\b",
    re.IGNORECASE,
)
_ADDITIVE_RE = re.compile(
    r"\b(?:noch|weitere\w*|zus[aä]tzlich\w*|mehr|another|"  # i18n-allow: input vocab
    r"more|"
    r"extra|"
    r"otr[oa]s?|m[aá]s)\b",
    re.IGNORECASE,
)

# An utterance that OPENS with an information-question shape is asking about
# terminals, not asking for them.
# This includes prepositional questions ("with which shortcut …") and
# first-person modals ("can I …"); neither is an order to the assistant. A
# polite request that merely ends in a question mark is not excluded: it is a
# real request, and the filler prefix above strips its politeness for the
# composer anyway.
_QUESTION_OPENER_RE = re.compile(
    r"^\s*[¿]?(?:"
    # A preposition can legitimately precede the interrogative. Without this,
    # "with which shortcut can I open a browser tab" opened an IDE pane.
    r"(?:mit|in|auf|an|von|zu|unter|[uü]ber|f[üu]r|durch)"  # i18n-allow: input vocab
    r"\s+welche\w*|"  # i18n-allow: input vocab
    r"(?:with|in|on|at|from|to|under|for|by|through)\s+which|"
    r"(?:con|en|de|a|para|por)\s+(?:qu[eé]|cu[aá]les?)|"  # i18n-allow: input vocab
    # First-person and impersonal modals ask about possibility or advice. Keep
    # second-person forms out: "can you open …" remains a polite command.
    r"(?:kann|k[oö]nnte|soll(?:te)?|muss|darf|d[üu]rfte)"  # i18n-allow: input vocab
    r"\s+(?:ich|man)|"  # i18n-allow: input vocab
    r"(?:can|could|should|must|may|would|do)\s+i|"
    r"(?:puedo|podr[ií]a|debo|se\s+puede)|"  # i18n-allow: input vocab
    r"wie|was|wieso|warum|wo|wann|welche\w*|wieviel\w*|"
    r"how|what|why|where|when|which|"
    r"c[oó]mo|qu[eé]|cu[aá]nt\w*|por\s+qu[eé]|d[oó]nde|cu[aá]ndo"
    r")\b",
    re.IGNORECASE,
)

# A recombined turn can preserve the connector that introduced its latest
# clause ("Und was passiert, wenn ...?"). Ignore only these bounded discourse
# words when deciding whether that clause is an information question; second-
# person requests such as "Und kannst du ...?" remain commands.
_QUESTION_DISCOURSE_PREFIX_RE = re.compile(
    r"^\s*¿?\s*(?:(?:und|and|y)\b[\s,;:–—-]*)+",  # i18n-allow: input vocab
    re.IGNORECASE,
)

# Number words per locale. Capped at the workspace maximum: past it the count is
# clamped anyway, so spelling out "twenty" buys nothing.
_NUMBER_WORDS: dict[str, int] = {
    # German number words double as articles here.  # i18n-allow: context for matching data
    # here ("mach noch ein Terminal auf" = one).  # i18n-allow: quoted spoken input
    "ein": 1,  # i18n-allow: German number word input vocabulary
    "eine": 1,  # i18n-allow: German number word input vocabulary
    "einen": 1,  # i18n-allow: German number word input vocabulary
    "eins": 1,  # i18n-allow: German number word input vocabulary
    # Both ASCII spellings of every umlaut word: a transcript may drop the
    # umlaut ("funf") or transliterate it ("fuenf"), and only the first was
    # covered — so "starte fünf Agenten" fell back to a count of one whenever
    # the provider wrote it the common way.
    "zwei": 2,
    "drei": 3,
    "vier": 4,
    "fünf": 5,
    "funf": 5,
    "fuenf": 5,  # i18n-allow: number words
    "sechs": 6,
    "sieben": 7,
    "acht": 8,
    "neun": 9,
    "zehn": 10,
    "elf": 11,  # i18n-allow: numbers
    "zwölf": 12,
    "zwolf": 12,
    "zwoelf": 12,  # i18n-allow: number words
    # English
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    # Spanish
    "un": 1,
    "uno": 1,
    "una": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "once": 11,
    "doce": 12,
    # Past a dozen, spoken counts are the round ones — nobody asks for
    # thirty-seven terminals out loud, and the workspace maximum is a hundred.
    # Digits already cover every number in between; these exist because a
    # transcript writes what it hears, and "open twenty terminals" arrived as a
    # request for ONE (no digit, no listed word, silent fall back to the
    # default). A count the user said and did not get is the failure this whole
    # detector is shaped around.
    "fünfzehn": 15,
    "funfzehn": 15,
    "fuenfzehn": 15,  # i18n-allow: number words
    "zwanzig": 20,
    "dreißig": 30,
    "dreissig": 30,
    "vierzig": 40,  # i18n-allow: number words
    "fünfzig": 50,
    "funfzig": 50,
    "fuenfzig": 50,
    "sechzig": 60,  # i18n-allow: number words
    "siebzig": 70,
    "achtzig": 80,
    "neunzig": 90,
    "hundert": 100,  # i18n-allow: number words
    "fifteen": 15,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fourty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "hundred": 100,
    "quince": 15,
    "veinte": 20,
    "treinta": 30,
    "cuarenta": 40,  # i18n-allow: number words
    "cincuenta": 50,
    "sesenta": 60,
    "setenta": 70,
    "ochenta": 80,  # i18n-allow: number words
    "noventa": 90,
    "cien": 100,
    "ciento": 100,  # i18n-allow: number words
}


# Which coding agent, when the user names one. Bare "claude" counts — nobody
# says a product name in full while talking. Everything else (including no
# mention at all) leaves the choice to the registry, which inherits the last
# pane's agent: "noch drei davon" is the common intent.
#
# The spellings below are matching DATA, not prose: speech recognition writes a
# product name by ear, and every one of these is something a transcript has
# actually said or is one keystroke away from it. A name this parser could not
# spell used to cost a whole pane — "two new Codex terminals and one Cloude code
# terminal" opened two panes and dropped the third in silence, because one
# unmatched word takes the entire group with it (maintainer report 2026-07-27).
# The user's STT dictionary repairs this too, but only for the words that user
# thought to add; an arbitrary downloader with an empty dictionary gets the same
# request and must get the same three panes.
def _agent_spellings() -> dict[str, tuple[str, ...]]:
    """Every spelling that names a CLI on its own, per registry entry."""
    from jarvis.workspace import agents as workspace_agents

    return {
        agent.name: agent.spoken_aliases
        for agent in workspace_agents.coding_agents()
        if agent.spoken_aliases
    }


def _agent_spellings_requiring_suffix() -> dict[str, tuple[str, str]]:
    """Ambiguous spelling → ``(CLI, the word that must follow it)``.

    Some spellings are ordinary words in their own right, so on their own they
    mean nothing here — "in the cloud" is not a request for a pane. They count
    only with the product's second word behind them: "cloud code" is
    unmistakable.
    """
    from jarvis.workspace import agents as workspace_agents

    return workspace_agents.aliases_needing_suffix()


def _spelling_pattern(spelling: str) -> str:
    """One spelling as a regex fragment, tolerant of how it was transcribed.

    A product whose name is one word is regularly written as two ("opencode" /
    "open code"), and an acronym arrives spaced or dotted ("GLM" / "G L M" /
    "G.L.M."). Matching only the spelling as typed here would drop exactly the
    transcripts this table exists to catch, so the separator between characters
    and words is elastic in one direction: written apart, matched together.
    """
    tokens = spelling.split()
    if len(tokens) > 1:
        return _SPOKEN_NAME_SEPARATOR.join(re.escape(token) for token in tokens)
    return re.escape(spelling)


# Speech providers alternate freely between whitespace and hyphens in product
# names ("Claude Code" / "Claude-Code"). Treat both as the same spoken
# boundary, including whitespace around a dash; punctuation outside a product
# name remains untouched.
_SPOKEN_NAME_SEPARATOR = r"(?:\s*[-‐‑–—]+\s*|\s+)"


#: A plural ending on a product name. People count CLIs the way they count
#: anything else — "zwei Claudes", "two Codexes" — and the name then ends in a
#: letter the word boundary after the spelling cannot cross, so the whole group
#: vanished. Kept to the ``s`` plural on purpose: it is the one both spoken
#: languages here form this way, and anything looser starts swallowing the next
#: word.
_PLURAL_SUFFIX = r"(?:e?s)?"


def _agent_alternation() -> str:
    """The regex fragment matching every accepted spelling of a CLI's name.

    Longest first so a spelling that is the prefix of another (``claud`` of
    ``claude``) can never shadow it.
    """
    parts = [
        rf"{_spelling_pattern(spelling)}{_PLURAL_SUFFIX}"
        rf"(?:{_SPOKEN_NAME_SEPARATOR}code)?"
        for spellings in _agent_spellings().values()
        for spelling in spellings
    ]
    parts += [
        rf"{_spelling_pattern(spelling)}{_SPOKEN_NAME_SEPARATOR}"
        rf"{re.escape(suffix)}{_PLURAL_SUFFIX}"
        for spelling, (_agent, suffix) in _agent_spellings_requiring_suffix().items()
    ]
    return "|".join(sorted(parts, key=len, reverse=True))


_AGENT_ALTERNATION = _agent_alternation()

_AGENT_RE = re.compile(rf"\b(?P<agent>{_AGENT_ALTERNATION})\b", re.IGNORECASE)


def _canonical_agent(raw: str) -> str | None:
    """The CLI a matched name means, or ``None`` for one this parser cannot place.

    ``None`` is unreachable through the pattern above — every alternative it
    offers comes from the registry — and callers still handle it, because a
    spelling that stops resolving must degrade to "no CLI named" rather than to
    a wrong one.

    Two spellings of the same name are folded here rather than in the table: a
    trailing product word is dropped ("codex code" is Codex), and the spaces an
    acronym picks up in transcription are squeezed out ("g l m" is "glm"), so
    the registry only ever has to carry the name itself.

    An AMBIGUOUS spelling standing alone resolves to nothing, and that rule is
    load-bearing in both directions. "cloud" is not a request for a Claude Code
    pane, and "open" is the verb in "open a terminal" — reading either as a
    product name here would not merely mis-name a pane, it would take the
    sentence's own verb away from the parser and drop the whole request.
    """
    standalone = _standalone_spellings()
    name = re.sub(r"[-‐‑–—]+", " ", str(raw or "").casefold())
    name = " ".join(name.split())
    if name in standalone:
        return standalone[name]
    for spelling, (agent, suffix) in _agent_spellings_requiring_suffix().items():
        if name == f"{spelling} {suffix}":
            return agent
    if name.endswith(" code"):
        base = name[: -len(" code")].strip()
        if base in standalone:
            return standalone[base]
    # "g l m" and "g.l.m." are the same acronym as "glm".
    squeezed = standalone.get(re.sub(r"[\s.]+", "", name))
    if squeezed is not None:
        return squeezed
    # "two Claudes", "two Codexes" — people count products the way they count
    # anything else. Undone here rather than in the table so no entry has to
    # list its own plural, and only as a LAST resort: a spelling that already
    # names a CLI is never re-read, so nothing that resolves can be changed by
    # this. The recursion is bounded — the stripped name has no plural left.
    for singular in _depluralized(name):
        resolved = _canonical_agent(singular)
        if resolved is not None:
            return resolved
    return None


def _depluralized(name: str) -> list[str]:
    """The singulars ``name`` could be the plural of, longest stem first.

    BOTH endings are offered rather than the first that fits, because a spelling
    can be a plausible plural two ways and only one of them is a product:
    "clodes" is "clode" (a Claude spelling) with an s, and stripping the "es"
    first would leave "clod" — which is a different entry that means nothing on
    its own. Trying one and stopping dropped the group.

    Applies to the LAST word only, which is where the plural sits in every shape
    this parser sees ("cloud codes", "claudes"), and never shortens a word down
    to nothing.
    """
    head, _, last = name.rpartition(" ")
    out: list[str] = []
    for ending in ("s", "es"):
        if last.endswith(ending) and len(last) > len(ending) + 1:
            trimmed = last[: -len(ending)]
            out.append(f"{head} {trimmed}".strip() if head else trimmed)
    return out


def canonical_agent(raw: str) -> str | None:
    """The CLI a spoken name means, or ``None``.

    The public face of ``_canonical_agent``, for the callers outside this module
    that have to read a CLI name the same way the parser does — the spawn path's
    "did you mean Claude Code or Codex?" answer, above all. Reading it a second
    way there would mean a name the parser accepts and the answer does not.
    """
    return _canonical_agent(raw)


def _standalone_spellings() -> dict[str, str]:
    """Spelling → CLI, for the spellings that name a product on their own."""
    return {
        spelling: agent for agent, spellings in _agent_spellings().items() for spelling in spellings
    }


def _open_verbs(text: str) -> list[re.Match[str]]:
    """Where the utterance asks for something to be opened.

    A CLI's own NAME is never one of those places, however much it looks like a
    verb. The verb table matches ``open\\w*``, which happily swallows "opencode"
    — and the actor positions it returns are what splits an utterance into
    clauses, so one product name read as a verb re-cuts the sentence and a group
    of panes goes to the wrong CLI or is dropped entirely.
    """
    return [
        match for match in _OPEN_VERB_RE.finditer(text) if _canonical_agent(match.group(0)) is None
    ]


@dataclass(frozen=True, slots=True)
class UncertainCli:
    """A word standing where a CLI name belongs, and the CLIs it could be."""

    spoken: str
    """The word as the transcript spelled it ("Klaudi", "Codecks")."""

    candidates: tuple[str, ...]
    """CLI names it could mean, best first. Never empty, never certain."""


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

    uncertain_cli: tuple[UncertainCli, ...] = ()
    """Words that stand where a CLI name belongs but name none of them clearly.

    The caller ASKS rather than acts on these — the maintainer's directive of
    2026-07-28: "wenn unklar ist, ob Claude Code oder Codex gemeint ist, soll
    er einfach nachfragen".  # i18n-allow: quoted maintainer directive

    Same asymmetry the pane-name question is built on (see ``clarify``): a
    needless question costs one word, while a wrong guess opens the wrong
    coding agent, on the wrong subscription, and the user finds out when it
    starts answering in the wrong style.
    """

    unsupported: tuple[str, ...] = ()
    """Coding CLIs the user counted that this workspace does not offer.

    Named rather than dropped. A product this app has no terminal kind for used
    to fall out of the parse without a word — "two Claudes and one Gemini Code"
    opened two panes, and the third was missing with nothing anywhere saying
    why. The caller speaks these back next to the panes that did open.
    """

    groups: tuple[SpawnGroup, ...] = ()
    """The requested fleet, in the order the user said it.

    "5 Codex and 3 Claude Code terminals" is TWO groups. Reading only the first
    number and the first agent — which is what this detector used to do —
    opened five Codex panes and dropped the three Claude ones without telling
    anyone (maintainer report 2026-07-26)."""


@dataclass(frozen=True, slots=True)
class CloseTerminalsRequest:
    """A spoken request to close a whole kind of workspace terminal."""

    agent: str | None
    """``"claude"`` / ``"codex"``, or ``None`` for every coding CLI."""

    utterance: str


def _spoken_count(text: str) -> int:
    """The requested number of panes: digits, a number word, or 1.

    First hit wins. A count is optional — "open another terminal" is a perfectly
    clear request for one — so this never fails, it defaults.
    """
    from .session import MAX_TERMINALS

    # Read the FIRST number in speech order. Searching all digits before number
    # words contradicted this function's contract and turned "five terminals;
    # each may start 50 workers" into a request for 50 terminals.
    #
    # An article that sizes nothing is skipped rather than read (``_count_at``),
    # so a task word behind the fleet ("… und mach einen Deep Dive") cannot
    # become the fleet's size. The default of one still stands behind it: a
    # request with no number at all ("mach das Terminal auf") is one pane.
    for match in re.finditer(r"\b(?:\d{1,3}|[^\W\d_]+)\b", text, re.UNICODE):
        value = _count_at(text, match)
        if value is not None:
            return max(1, min(value, MAX_TERMINALS))
    return 1


#: A count followed by an agent name — "5 Codex", "drei neue Claude Code",
#: "tres terminales de Codex". The filler between them covers the words people
#: actually put there: the pane noun itself (Spanish and English both say
#: "three terminals of Codex" as often as "three Codex terminals") plus the
#: usual qualifiers. Bounded at two words so the count and the agent cannot
#: drift into different clauses of the sentence.
_COUNT_AGENT_RE = re.compile(
    r"\b(?P<count>\d{1,3}|[a-zäöüñ]+)\s+"  # i18n-allow: input vocab
    r"(?:(?:neue|weitere|zus[aä]tzliche|more|new|extra|"  # i18n-allow: input vocab
    r"additional|de|del|"
    r"otros|otras|m[aá]s|terminals?|terminales|panes?|tabs?)\s+){0,2}"
    rf"(?P<agent>{_AGENT_ALTERNATION})\b",
    re.IGNORECASE,
)

#: Any number, spoken or written, as its own token.
_COUNT_TOKEN_RE = re.compile(r"\b(?:\d{1,3}|[^\W\d_]+)\b", re.UNICODE)

#: How far a count may sit in front of the CLI name it belongs to.
#:
#: Wide enough for the words people put between them — "two of the new Claude
#: Code terminals" — and narrow enough that a number from an earlier part of the
#: clause cannot reach across into a group it was never about. Measured in
#: characters rather than words because that is what the rest of this module
#: already reasons in.
_COUNT_AGENT_MAX_GAP = 40


#: Number words that are ALSO the indefinite article, in all three locales.
#:
#: They have to be counted — "mach noch ein Terminal auf" is a real request for
#: one pane, and dropping them cost the user that pane. They also occur in
#: ordinary speech constantly, attached to nothing this parser cares about:
#: "also ein Deep Dive machen soll" is the live 2026-08-13 failure, where the
#: article sitting behind a mis-transcribed call-sign became the size of a
#: fleet nobody asked for.
#:
#: ``names._NUMBER_WORDS`` refuses the article outright and says why. It cannot
#: be refused here, because sizing a fleet with it is a real request — so it
#: has to EARN the reading instead, from what it stands in front of. "eins" and
#: "uno" are absent on purpose: those are the bare numbers, never articles.
_ARTICLE_ONES: frozenset[str] = frozenset(
    # i18n-allow: speech-recognition input vocabulary, not prose
    {"ein", "eine", "einen", "a", "an", "un", "una"}
)

#: How many words may sit between the article and the thing it sizes. Two, the
#: same bound ``_COUNT_AGENT_RE`` allows between a count and its CLI: enough for
#: "ein weiteres neues Terminal", too few to reach across into another clause.
_ARTICLE_FILLER_WORDS = 2

#: What an article must be sizing to count as a number: panes, or a coding CLI
#: standing in for them ("mach noch einen Codex auf"). Anchored at the article
#: and bounded by the filler above, so the evidence has to be adjacent — an
#: article whose noun is a Deep Dive sizes nothing here.
_ARTICLE_SIZES_RE = re.compile(
    rf"\s*(?:[^\W\d_]+\s+){{0,{_ARTICLE_FILLER_WORDS}}}"
    rf"(?:{_PANE_NOUN_RE.pattern}|{_AGENT_RE.pattern})",
    re.IGNORECASE,
)


def _article_sizes_something(text: str, end: int) -> bool:
    """Whether the article ending at ``end`` is sizing panes rather than prose."""
    return _ARTICLE_SIZES_RE.match(text, end) is not None


def _count_of(raw: str) -> int | None:
    """The number a token means, or None when it is not a number at all."""
    token = raw.casefold()
    if token.isdigit():
        return int(token)
    cleaned = "".join(ch for ch in token if ch.isalpha())
    return _NUMBER_WORDS.get(cleaned)


def _count_at(text: str, match: re.Match[str]) -> int | None:
    """``_count_of`` with the sentence around the token taken into account.

    The one place that knows an article is only a number when it sizes
    something. Every reader of a count goes through it, so the group parser and
    the fallback cannot drift into two different opinions about whether "ein"
    was a one.
    """
    value = _count_of(match.group(0))
    if value is None:
        return None
    cleaned = "".join(ch for ch in match.group(0).casefold() if ch.isalpha())
    if cleaned in _ARTICLE_ONES and not _article_sizes_something(text, match.end()):
        return None
    return value


def _count_tokens(text: str) -> list[tuple[int, int, int]]:
    """Every number in ``text`` as ``(start, end, value)``, spoken forms joined.

    "A hundred" is one number, not a one followed by a hundred — and read as two
    it cost a group: the hundred went to the CLI that followed it and the stray
    "a" was left to claim the pane noun as a second group of its own. Adjacent
    number words are therefore combined the two ways speech actually combines
    them, a multiplier behind a smaller number ("two hundred") and a unit behind
    a ten ("twenty five"), and left alone otherwise.
    """
    raw = [
        (match.start(), match.end(), value)
        for match in _COUNT_TOKEN_RE.finditer(text)
        if (value := _count_at(text, match)) is not None
    ]
    joined: list[tuple[int, int, int]] = []
    for start, end, value in raw:
        if joined:
            prev_start, prev_end, prev_value = joined[-1]
            adjacent = text[prev_end:start].strip() == ""
            if adjacent and value >= 100 and prev_value < value:
                joined[-1] = (prev_start, end, prev_value * value)
                continue
            if adjacent and 20 <= prev_value < 100 and value < 10:
                joined[-1] = (prev_start, end, prev_value + value)
                continue
        joined.append((start, end, value))
    return joined


def _spoken_groups(text: str) -> tuple[SpawnGroup, ...]:
    """The fleet the utterance describes, group by group.

    Read as a SEQUENCE of counts and CLI names rather than by matching a fixed
    "number, filler, name" shape. The shape was the bug: the filler was a
    whitelist of the words people were expected to say, so one word nobody had
    listed took its whole group down with it and the request was executed
    partially, in silence. Live on 2026-07-28, speech recognition turned "two
    new Claude Code terminals and one Codex terminal" into "two NASA Cloud Code
    terminals and one Codex terminal" — "NASA" broke the first pair, the second
    pair alone was then read as the plain single-agent case, and the Codex pane
    was never opened or mentioned.

    Reading positions instead has no such failure mode: whatever sits between a
    count and the name after it, the pair still stands. Three shapes are
    recognised, and between them they cover everything a fleet request can say:

    * ``"two Claude"`` — a count in front of a name.
    * ``"and a Codex"`` — a name with no count of its own, which is one pane.
    * ``"three more terminals"`` — a count in front of the pane noun with no
      name at all, which is one group inheriting the workspace's CLI.

    A count is spent at most once, so "two Codex terminals" is two panes and not
    two plus two. Groups naming the SAME CLI are merged: "two Codex and two more
    Codex" is four Codex panes, and opening them in two batches would only slow
    the workspace down.
    """
    counts = _count_tokens(text)
    spent = set()

    def _count_before(start: int) -> int:
        """The nearest unspent count in front of ``start``, or 1."""
        for index in range(len(counts) - 1, -1, -1):
            begin, end, value = counts[index]
            if end > start or index in spent:
                continue
            if start - end > _COUNT_AGENT_MAX_GAP:
                break
            spent.add(index)
            return max(1, value)
        return 1

    found: list[tuple[int, SpawnGroup]] = []
    # Both kinds of product name in ONE pass, in speech order: the CLIs this
    # workspace has, and the ones it does not. The second kind opens nothing,
    # but it still SPENDS its count — otherwise "one Gemini Code terminal"
    # would be refused by name and opened anyway as an unnamed pane, which is
    # two answers to one request.
    mentions = sorted(
        [(m.start(), True, m) for m in _AGENT_RE.finditer(text)]
        + [(m.start(), False, m) for m in _UNSUPPORTED_CLI_RE.finditer(text)],
        key=lambda item: item[0],
    )
    for start, supported, match in mentions:
        if not supported:
            _count_before(start)
            continue
        agent = _canonical_agent(match.group("agent"))
        if agent is None:
            continue
        found.append((start, SpawnGroup(count=_count_before(start), agent=agent)))

    # A count that no CLI name claimed, sitting in front of the pane noun, is a
    # group of its own: "two terminals and one Codex" is three panes, and the
    # two that were not given a name inherit one.
    #
    # Unless the sentence is TALKING ABOUT the panes rather than ordering more
    # of them. Live 2026-08-12 17:40: "open two new cloud code terminals and
    # the two terminals should hunt bugs, one terminal takes macOS and one
    # terminal takes Linux" — the restated count and the enumeration items
    # were each read as more fleet, and a request for two opened FOUR panes,
    # every one of them billed. Two shapes are task-side and are dropped, but
    # only for a group that is NOT the first of its clause (the first is the
    # request itself, however it is phrased — "zwei Terminals sollen
    # aufgehen" stays a request) and never for one carrying an additive
    # ("noch ein Terminal" extends the fleet, full stop):
    #
    # * the noun is directly followed by its own predicate — a modal or work
    #   verb ("the two terminals SHOULD …", "ein Terminal SOLL …") or the
    #   elided-verb complement ("ein Terminal FÜR Linux"). A fleet request
    #   puts the panes at the END of its clause; a restatement gives them a
    #   job to do in the same breath;
    # * two or more value-one groups in one clause ("one terminal … and one
    #   terminal …") — spoken enumeration counts items off one at a time,
    #   while a fleet request states its size once ("two terminals").
    candidates: list[tuple[int, int, re.Match[str]]] = []
    for match in _pane_nouns(text):
        index = next(
            (
                i
                for i in range(len(counts) - 1, -1, -1)
                if counts[i][1] <= match.start()
                and i not in spent
                and match.start() - counts[i][1] <= _COUNT_AGENT_MAX_GAP
            ),
            None,
        )
        if index is None:
            continue
        spent.add(index)
        candidates.append((counts[index][0], index, match))

    first_at = min(
        [at for at, _g in found] + [at for at, _i, _m in candidates],
        default=0,
    )
    ones = sum(1 for _at, i, _m in candidates if counts[i][2] == 1)
    for at, index, noun in candidates:
        value = max(1, counts[index][2])
        additive_near = (
            _ADDITIVE_RE.search(text[max(0, counts[index][0] - _ADDITIVE_NEAR_GAP) : noun.start()])
            is not None
        )
        if at > first_at and not additive_near:
            after_noun = text[noun.end() : noun.end() + 18]
            predicated = bool(
                _DISTRIBUTIVE_PREDICATE_RE.search(after_noun)
                or _INSTRUCTION_VERB_RE.search(after_noun)
            )
            if predicated or (value == 1 and ones >= 2):
                continue
        found.append((at, SpawnGroup(count=value, agent=None)))

    if not found:
        return ()

    # Back into the order the user said them: the panes are opened group by
    # group, and a fleet that comes up in a different order than it was asked
    # for is a fleet the user has to re-read before they can address it.
    found.sort(key=lambda item: item[0])
    return _merge_groups([group for _at, group in found])


def _merge_groups(found: list[SpawnGroup]) -> tuple[SpawnGroup, ...]:
    """``found`` folded by CLI and clamped to the workspace total.

    Groups naming the SAME CLI are merged: "two Codex and two more Codex" is
    four Codex panes, and opening them in two batches would only slow the
    workspace down. One function rather than a merge per caller, because a
    fleet can now be described across SEVERAL clauses (see ``_spawn_regions``)
    and two merge rules would be free to disagree about the same sentence.

    The clamp takes the TOTAL, not each group: the workspace maximum is a
    property of the workspace. Trimming from the back keeps the groups the
    user named first intact rather than shrinking all of them into
    uselessness.
    """
    from .session import MAX_TERMINALS

    merged: dict[str, int] = {}
    for group in found:
        key = group.agent or ""
        merged[key] = merged.get(key, 0) + group.count

    out: list[SpawnGroup] = []
    remaining = MAX_TERMINALS
    for agent, count in merged.items():
        if remaining <= 0:
            break
        take = min(count, remaining)
        remaining -= take
        out.append(SpawnGroup(count=take, agent=agent or None))
    return tuple(out)


#: The plural coding-agent noun, when the user does not say "terminal".
_AGENT_NOUN_RE = re.compile(
    r"\b(?:agents|agenten|agentes)\b",  # i18n-allow: input vocab
    re.IGNORECASE,
)

#: Wording that unambiguously asks for a BACKGROUND worker. Checked first and
#: absolute: the whole reason the terminal noun is mandatory is that stealing a
#: genuine mission request is invisible to the user.
_BACKGROUND_RE = re.compile(
    r"\b(?:hintergrund|background|worker\w*|mission\w*|"  # i18n-allow: input vocab
    r"sub-?agent\w*|subagent\w*|delegier\w*|delegate\w*|"  # i18n-allow: input vocab
    r"segundo\s+plano|trabajador\w*)\b",  # i18n-allow: input vocab
    re.IGNORECASE,
)

# Keep terminal opening grammar inside the clause that actually contains the
# pane noun. The live 2026-07-27 failure contained "ten new terminals" as a
# reference to work already done and "start 50 sub-agents" much later. Global
# noun/verb/number searches joined those unrelated fragments into a fresh
# 50-terminal request.
_CLAUSE_RE = re.compile(r"[^.!?;\n]+")
_SPAWN_PAIR_MAX_GAP = 80


@dataclass(frozen=True, slots=True)
class _SpawnSpan:
    start: int
    end: int
    parse_text: str
    truncated: bool = False
    """A briefing verb cut the fleet parse short: everything after this span
    is the new panes' WORK, and no later clause may be read as more panes."""


def _span_gap(left: re.Match[str], right: re.Match[str]) -> int:
    if left.end() < right.start():
        return right.start() - left.end()
    if right.end() < left.start():
        return left.start() - right.end()
    return 0


_FLEET_BRIEF_RE = re.compile(
    r"\b(?:jede\w*\s+(?:davon|dieser)|each\s+(?:one|of\s+them)|"
    r"prompt\w*|brief\w*|instruct\w*|assign\w*|tell\w*|"
    # "sag ihnen, …" / "diles que …" — the plain-speech handover verbs. They
    # were missing while "tell" was not, so a German fleet order's task half
    # was parsed as MORE FLEET: the counts inside it opened extra panes
    # (live 2026-08-12 17:40, four billed panes for a spoken "two").
    r"sag\w*|diles?|d[ií]gale|"  # i18n-allow: input vocab
    r"anweis\w*|beauftrag\w*|gib\w*\s+.*\baufgabe\w*)\b",  # i18n-allow: input vocab
    re.IGNORECASE,
)


#: Coding CLIs that exist in the world but not as a terminal kind here.
#:
#: Matching DATA, not a wish list: every one of these is a product a user may
#: reasonably name in a fleet request, and the point is to ANSWER for it rather
#: than let it disappear. Spellings by ear are included for the same reason the
#: supported CLIs carry them — a transcript writes what it hears ("Giming Code"
#: for Gemini, maintainer report 2026-07-28).
#:
#: Deliberately short and unambiguous. A word that is also ordinary English
#: ("cursor", "continue") is left out: refusing a request over a word the user
#: did not mean as a product would be worse than the silence this replaces.
_UNSUPPORTED_CLI_SPELLINGS: dict[str, str] = {
    "gemini": "Gemini",
    "gemeni": "Gemini",
    "gimini": "Gemini",
    "giming": "Gemini",
    "jemini": "Gemini",
    "antigravity": "Antigravity",
    "aider": "Aider",
    "windsurf": "Windsurf",
    "copilot": "Copilot",
}

_UNSUPPORTED_CLI_RE = re.compile(
    r"\b(?P<name>" + "|".join(sorted(_UNSUPPORTED_CLI_SPELLINGS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def _unsupported_clis(text: str) -> tuple[str, ...]:
    """Products named in ``text`` that this workspace has no terminal kind for.

    Only COUNTED mentions, on the same reasoning as
    ``_counted_agent_mentions``: "does Gemini support this?" names a product
    without asking for a pane of it, and answering that with a refusal would be
    a new way of getting the request wrong.
    """
    counts = _count_tokens(text)
    out: list[str] = []
    for match in _UNSUPPORTED_CLI_RE.finditer(text):
        counted = any(
            end <= match.start() and match.start() - end <= _COUNT_AGENT_MAX_GAP
            for _start, end, _value in counts
        )
        if not counted:
            continue
        label = _UNSUPPORTED_CLI_SPELLINGS[match.group("name").casefold()]
        if label not in out:
            out.append(label)
    return tuple(out)


#: How close a word must come to a CLI's name to be worth asking about.
#:
#: The same floor the pane-name question uses (``names._NEAR_MISS_FLOOR``), and
#: deliberately the same NUMBER rather than a second opinion about closeness:
#: two thresholds for one notion is how a word ends up uncertain to one path and
#: certain to another, which is the gap that produced turns with neither an
#: action nor a question.
_CLI_NEAR_MISS_FLOOR = 0.55

#: Words that stand between a count and a CLI name without being either.
#:
#: They occupy the same position a garbled product name would, so without this
#: every one of them would be held up as "did you mean Claude Code?".
_CLI_POSITION_NOISE = frozenset(
    {
        "neue",  # i18n-allow: input vocab
        "neuen",  # i18n-allow: input vocab
        "weitere",  # i18n-allow: input vocab
        "weiteren",  # i18n-allow: input vocab
        "zusätzliche",  # i18n-allow: input vocab
        "zusaetzliche",  # i18n-allow: input vocab
        "new",
        "more",
        "extra",
        "additional",
        "other",
        "another",
        "otros",
        "otras",
        "más",
        "mas",
        "nuevos",
        "nuevas",  # i18n-allow: input vocab
        "de",
        "del",
        "von",
        "vom",
        "of",
        "the",
        "a",
        "an",  # i18n-allow: input vocab
        "und",
        "and",
        "y",  # i18n-allow: input vocab
        "bitte",
        "please",
        "por",
        "favor",
        "mir",
        "me",
        "uns",
        "us",  # i18n-allow: input vocab
    }
)


def _cli_display_names() -> dict[str, str]:
    """CLI key → the name a question should say out loud."""
    try:
        from jarvis.workspace import agents as workspace_agents

        return {agent.name: agent.display_name for agent in workspace_agents.coding_agents()}
    except Exception:  # noqa: BLE001 - a question must never break a turn
        return {}


def _closest_clis(word: str) -> tuple[tuple[str, float], ...]:
    """The CLIs ``word`` almost names, best first, or ``()``.

    Scored against every accepted SPELLING of every CLI and reduced to the best
    per CLI, because the spellings are what speech actually produces — "Klaudi"
    is nowhere near the string "claude code" but sits right next to "klaude".
    """
    from .names import similarity

    best: dict[str, float] = {}
    for agent, spellings in _agent_spellings().items():
        for spelling in spellings:
            score = similarity(word, spelling)
            if score > best.get(agent, 0.0):
                best[agent] = score
    scored = [(agent, score) for agent, score in best.items() if score >= _CLI_NEAR_MISS_FLOOR]
    return tuple(sorted(scored, key=lambda item: (-item[1], item[0])))


#: Punctuation or coordination that ends one fleet group and starts the next.
_GROUP_BOUNDARY_RE = re.compile(
    r"[,;]|\b(?:und|and|y)\b",  # i18n-allow: input vocab
    re.IGNORECASE,
)


def _group_boundary_between(gap: str) -> bool:
    """Whether ``gap`` separates two fleet groups rather than two of one name.

    A comma, a coordination, or a fresh count each start a group of their own,
    so a CLI name on the far side of one cannot vouch for the word on the near
    side.
    """
    if _GROUP_BOUNDARY_RE.search(gap):
        return True
    return any(_count_of(token) is not None for token in _COUNT_TOKEN_RE.findall(gap))


def _uncertain_clis(text: str) -> tuple[UncertainCli, ...]:
    """Words standing where a CLI name belongs that name none of them clearly.

    Two questions have to be answered before a word qualifies, and both are
    narrow on purpose — a question the user did not need is still a cost:

    1. **Is it in a CLI's position?** Directly behind a count ("two Klaudi") or
       directly in front of the pane noun ("Klaudi terminals"). A word anywhere
       else in the sentence is not a garbled product name, it is a word.
    2. **Does it come close to one?** Scored on the resolver's own scale. A word
       that resembles nothing — "two NASA Cloud Code terminals", where NASA is
       simply debris between the count and the real name — scores below the
       floor against every CLI and is left alone.

    A word that resolves EXACTLY is never here: that is certainty, and the
    acting path owns it.
    """
    counts = _count_tokens(text)
    pane_nouns = [(m.start(), m.end()) for m in _pane_nouns(text)]
    known = [
        (m.start(), m.end())
        for m in _AGENT_RE.finditer(text)
        if _canonical_agent(m.group("agent")) is not None
    ]
    display = _cli_display_names()

    out: list[UncertainCli] = []
    seen: set[str] = set()
    for match in re.finditer(r"\b[^\W\d_]{3,}\b", text, re.UNICODE):
        word = match.group(0)
        folded = word.casefold()
        if folded in _CLI_POSITION_NOISE or folded in seen:
            continue
        if _count_of(word) is not None or _canonical_agent(word) is not None:
            continue
        if _PANE_NOUN_RE.fullmatch(word) or _UNSUPPORTED_CLI_RE.fullmatch(word):
            continue
        # Inside, or overlapping, a name that WAS recognised: not a candidate.
        if any(start <= match.start() < end for start, end in known):
            continue
        behind_count = any(
            end <= match.start() and match.start() - end <= 3 for _s, end, _v in counts
        )
        before_pane = any(
            match.end() <= start and start - match.end() <= 3 for start, _e in pane_nouns
        )
        if not behind_count and not before_pane:
            continue
        # The real name may follow it ("two NASA Cloud Code terminals"): the
        # group is not in doubt, so neither is this word. But only WITHIN the
        # group — a comma, a coordination or a fresh count between them means
        # the name belongs to the NEXT group, and vouches for nothing here:
        # "two closed, one codex" garbles "Claudes" into "closed", and the
        # codex behind the comma was read as this group's real name, so two
        # panes of whatever kind happened to be inherited opened in silence
        # instead of the question the garble deserves (maintainer report
        # 2026-08-12).
        if any(
            0 <= start - match.end() <= 12
            and not _group_boundary_between(text[match.end() : start])
            for start, _end in known
        ):
            continue
        candidates = _closest_clis(word)
        if not candidates:
            continue
        seen.add(folded)
        out.append(
            UncertainCli(
                spoken=word,
                candidates=tuple(display.get(agent, agent) for agent, _score in candidates[:3]),
            )
        )
    return tuple(out)


#: The verb that means a BACKGROUND worker, not a pane.
#:
#: "Spawn" is the word this app uses for dispatching a Jarvis-Agent, and a
#: request built on it stays one even when it names a coding CLI: "spawne 5
#: Claude Codes" is five workers. That distinction is the safety margin of the
#: whole workspace feature — stealing a genuine mission request is invisible to
#: the user — so the product-name-as-pane-noun rule below stands down for it and
#: only ever applies to a plain OPEN.
_SPAWN_VERB_RE = re.compile(r"\bspawn\w*\b", re.IGNORECASE)


def _counted_agent_mentions(clause: str) -> list[re.Match[str]]:
    """CLI names that are being COUNTED, and therefore name panes.

    "Kannst du zwei Claudes aufmachen, einen Codex und ein GLM Code" is a
    perfectly ordinary way to ask for four terminals, and it never once says
    "terminal" — so a parser that insists on the pane noun answers nothing at
    all, which is what it did (maintainer report 2026-07-28). A counted product
    name is the same request wearing the product's name instead of the noun.

    Two conditions keep this narrow, and both are needed:

    * **Counted.** "Open the file in Codex" names a CLI without asking for one,
      and has no number or article in front of it; "two Claudes", "einen
      Codex", "a GLM" all have one.
    * **Opened, not spawned.** See ``_SPAWN_VERB_RE``.
    """
    if _open_verbs(clause) == [] or _SPAWN_VERB_RE.search(clause) is not None:
        return []
    out: list[re.Match[str]] = []
    for match in _AGENT_RE.finditer(clause):
        if _canonical_agent(match.group("agent")) is None:
            continue
        before = clause[max(0, match.start() - 24) : match.start()]
        tokens = [t for t in _COUNT_TOKEN_RE.findall(before) if t]
        if tokens and _count_of(tokens[-1]) is not None:
            out.append(match)
    return out


def _spawn_span(text: str) -> _SpawnSpan | None:
    """The bounded clause that genuinely asks for panes to be opened."""
    for clause_match in _CLAUSE_RE.finditer(text):
        clause = clause_match.group(0)
        panes = _pane_nouns(clause) + _counted_agent_mentions(clause)
        actors = _open_verbs(clause) + list(_ADDITIVE_RE.finditer(clause))
        if not panes or not actors:
            continue
        pairs = [
            (pane, actor)
            for pane in panes
            for actor in actors
            if _span_gap(pane, actor) <= _SPAWN_PAIR_MAX_GAP
            and _FLEET_BRIEF_RE.search(
                clause,
                min(pane.end(), actor.end()),
                max(pane.start(), actor.start()),
            )
            is None
        ]
        if not pairs:
            continue
        pane, actor = min(pairs, key=lambda pair: _span_gap(*pair))
        anchor_end = max(pane.end(), actor.end())

        # Mixed fleets may continue after the first pane noun ("three terminals
        # of Codex and two of Claude"). Parse the rest of this one clause, but
        # stop when it begins telling the fleet what to do; repeated counts in
        # that task are not additional pane groups. The handover shows up two
        # ways: a briefing verb ("and prompt them …"), or the panes returning
        # as the SUBJECT of their own work ("and the two terminals should …",
        # "one terminal takes macOS …") — see ``_task_subject_cut``.
        parse_end = len(clause)
        briefing = _FLEET_BRIEF_RE.search(clause, anchor_end)
        if briefing is not None:
            parse_end = briefing.start()
        subject = _task_subject_cut(clause, anchor_end)
        if subject is not None:
            parse_end = min(parse_end, subject)
        return _SpawnSpan(
            start=clause_match.start(),
            end=clause_match.start() + anchor_end,
            parse_text=clause[:parse_end],
            truncated=parse_end < len(clause),
        )
    return None


def _task_subject_cut(clause: str, from_pos: int) -> int | None:
    """Where the clause stops describing the fleet and starts its WORK, or None.

    The verbless handover: after the fleet is asked for, the panes come back
    as the SUBJECT of a job — "… and the two terminals should do a deep
    dive", "… one terminal takes macOS and one terminal takes Linux". No
    briefing verb marks the turn, so ``_FLEET_BRIEF_RE`` sails past it and
    every count in that job was read as MORE fleet: a spoken "two" opened
    four billed panes (live 2026-08-12 17:40).

    A pane noun past the anchor whose next words are its own predicate — a
    modal, a work verb, or the elided-verb complement ("ein Terminal FÜR
    Linux") — is that turn. The cut lands in front of the count/article that
    introduces it, so the whole subject phrase becomes the remainder that
    ``spawn_instruction`` hands to the new panes.

    Two exemptions keep real fleet extensions whole, the same ones the group
    parser applies: an additive in front of the noun ("noch ein Terminal")
    extends the fleet, and a CLI name directly in front of the noun ("… und 3
    Claude Code Terminals auf") is a named group whose trailing word — the
    German separable particle, usually — must not be read as a predicate.
    """
    counts = _count_tokens(clause)
    named = [m.end() for m in _AGENT_RE.finditer(clause)]
    for noun in _pane_nouns(clause):
        if noun.start() <= from_pos:
            continue
        if _ADDITIVE_RE.search(clause[max(0, noun.start() - _COUNT_AGENT_MAX_GAP) : noun.start()]):
            continue
        if any(0 <= noun.start() - end <= 2 for end in named):
            continue
        window = clause[noun.end() : noun.end() + 18]
        if not (_DISTRIBUTIVE_PREDICATE_RE.search(window) or _INSTRUCTION_VERB_RE.search(window)):
            continue
        cut = noun.start()
        for c_start, c_end, _value in counts:
            if c_end <= cut and cut - c_end <= 12:
                cut = min(cut, c_start)
        return cut
    return None


def _spawn_regions(text: str) -> list[_SpawnSpan]:
    """Every clause of ``text`` that asks for panes, in speech order.

    ``_spawn_span`` deliberately reads ONE clause — that bound is what keeps
    unrelated fragments of a long sentence from being joined into a fleet
    request (the 2026-07-27 "ten new terminals … start 50 sub-agents"
    failure). But a fleet is regularly described across SEVERAL clauses, each
    carrying its own full evidence: "Öffne zwei Terminals. Mach bitte noch
    drei Codex Terminals auf" is five panes, and reading only the first clause
    executed the request PARTIALLY while the second clause was handed to the
    two fresh panes as their coding task — agents briefed to open terminals,
    which is nobody's intention ever (live 2026-08-12 11:56, see
    ``_spoken_correction_suffix`` for the transcript).

    Collection stops at the first BRIEFING boundary, in either place it can
    appear: a briefing verb inside a span's own clause (``truncated``), or one
    between two spans ("… und prompte sie: erstelle drei Config-Tabs" — the
    tabs there are the fleet's work, not more fleet). Past that point every
    count belongs to the task, and the 50-subagent guard above keeps holding.
    """  # i18n-allow: quoted spoken input
    regions: list[_SpawnSpan] = []
    offset = 0
    while offset < len(text):
        span = _spawn_span(text[offset:])
        if span is None:
            break
        absolute = _SpawnSpan(
            start=offset + span.start,
            end=offset + span.end,
            parse_text=span.parse_text,
            truncated=span.truncated,
        )
        if regions:
            previous_end = regions[-1].start + len(regions[-1].parse_text)
            if _FLEET_BRIEF_RE.search(text, previous_end, absolute.end) is not None:
                break
        regions.append(absolute)
        if absolute.truncated:
            break
        offset = absolute.start + len(absolute.parse_text)
    return regions


#: A spawn clause that is a CONDITION attached to work described earlier in the
#: sentence, rather than the request itself: "let Lee do a deep dive on this and
#: fix it — if there is no terminal by that name, spawn a new one and prompt it
#: right in there". The work is then in FRONT of the spawn clause, and reading
#: only what FOLLOWS it (which is just the fallback wording) opened a blank pane
#: and announced it as ready while the task was silently dropped — the live
#: failure of 2026-07-27, where the user's answer was "you did nothing".
#:
#: The conditional marker is what separates that shape from ordinary talk in
#: front of a plain request ("the tests are green. Open two more terminals"),
#: where the leading sentence is emphatically NOT a brief for the new panes.
_SPAWN_CONDITION_RE = re.compile(
    r"\b(?:wenn|falls|sofern|ansonsten|sonst|"  # i18n-allow: input vocab
    r"if|in\s+case|unless|otherwise|"
    r"si|en\s+caso|sino)\b",  # i18n-allow: input vocab
    re.IGNORECASE,
)


def _leading_task(text: str, span: _SpawnSpan) -> str:
    """Work described BEFORE a conditional spawn clause, or ``""``.

    Two conditions, and each one removes a way of being wrong:

    1. the spawn clause has to be CONDITIONAL — "spawn one *if* none is called
       that" is a fallback for work stated elsewhere, while a bare "open two
       terminals" is the whole request and anything before it is conversation;
    2. the leading text has to read as an instruction, or there is no task to
       hand over in the first place.

    A pane the user ADDRESSED never reaches here: ``detect_spawn`` stands down
    for an addressed call-sign before this is consulted, so the leading text
    belongs to nobody but the panes about to be opened.
    """
    if _SPAWN_CONDITION_RE.search(text[span.start : span.end]) is None:
        return ""
    lead = text[: span.start].strip(" ,:-")
    if not lead or not _looks_like_instruction(lead):
        return ""
    cleaned = _FILLER_PREFIX_RE.sub("", lead).strip()
    return cleaned if len(cleaned) >= _MIN_INSTRUCTION_CHARS else lead


# Spoken self-corrections. Speech does not edit, it RETRACTS: the words below
# are how a person takes back what they just said and replaces it, mid-turn.
# Matching *input vocabulary* across the supported locales, not prose.
#
# Live failure this pins (voice session 2026-08-12 11:56, gemini-live):
# "Kannst du bitte zwei neue Terminals öffnen? fünf neue  # i18n-allow: transcript
# Nee, nee, wir machen noch zwei neue Codex Terminals und  # i18n-allow: transcript
# fünf neue Code Terminals." — the retracted opening half  # i18n-allow: transcript
# was executed (two plain panes, T4/T5), and the CORRECTION was read as those
# panes' coding task. Both fresh agents were then briefed to "open seven new
# terminal sessions", which no user has ever meant.
#
# Deliberately narrow. Bare "nicht"/"not"/"no" are ordinary negation, bare
# "ne" is the German tag question ("…, ne?"), and reading either as a
# retraction would throw away fleet groups the user still wants. And a cue
# alone never cuts anything — see ``_spoken_correction_suffix`` for the second
# condition that makes acting on one safe.
_RETRACTION_RE = re.compile(
    r"\b(?:"
    r"nee+|nö+|nein|quatsch|doch\s+nicht|stattdessen|"  # i18n-allow: input vocab
    r"vergiss\s+(?:das|es)|ich\s+mein(?:e|te)|"  # i18n-allow: input vocab
    r"no+[,\s]+wait|wait[,\s]+no+|no[,\s]+no|scratch\s+that|"
    r"forget\s+(?:that|it)|i\s+meant?|actually[,\s]+no|instead|"
    r"mejor\s+dicho|digo|olvida\s+eso|espera[,\s]+no"  # i18n-allow: input vocab
    r")\b",
    re.IGNORECASE,
)


def _spoken_correction_suffix(text: str) -> str:
    """The utterance after its last self-correction, or ``text`` unchanged.

    Two conditions must hold before anything is cut, and each removes a way of
    being wrong:

    1. a RETRACTION cue stands in the utterance (``_RETRACTION_RE``) — the
       spoken marker that what came before it no longer counts;
    2. what follows the cue is a COMPLETE pane request of its own
       (``_spawn_span``) — a correction that replaces a fleet has to describe
       the replacement fleet. "öffne zwei Claude, äh nee" retracts without
       replacing, and cutting there would leave nothing to parse; it stays
       untouched and is read as it always was.

    The LAST qualifying cue wins, because a stutter repairs itself repeatedly
    ("Nee, nee, wir machen …") and each repair supersedes the one before it.

    What this deliberately does NOT do: subtract a single named group ("doch
    nicht Claude, Codex") — a retraction without a verb names no complete
    request, fails condition 2, and keeps today's reading. The question path
    (``uncertain_cli``) remains the honest answer for those.
    """  # i18n-allow: quoted spoken input
    best = text
    for match in _RETRACTION_RE.finditer(text):
        suffix = text[match.end() :].lstrip(" \t,.:;!?-–—")
        if len(suffix) < 6:
            continue
        if _spawn_span(suffix) is not None:
            best = suffix
    return best


def spawn_includes_task(user_text: str) -> bool:
    """Whether a pane-spawn request also tells the new fleet to do work."""
    text = _spoken_correction_suffix((user_text or "").strip())
    regions = _spawn_regions(text)
    if not regions:
        # The terminal-noun-free agent-fleet form can still carry a task.
        request = detect_spawn(text)
        return request is not None and bool(_INSTRUCTION_VERB_RE.search(text))
    # Work can only ever FOLLOW the whole fleet description. Measuring from
    # the first clause's anchor read a later "… und mach noch drei Codex
    # Terminals auf" clause as the new panes' coding task — fresh agents
    # briefed to open terminals (live 2026-08-12, see ``_spawn_regions``).
    last = regions[-1]
    remainder = text[last.start + len(last.parse_text) :]
    if _FLEET_BRIEF_RE.search(remainder) or _INSTRUCTION_VERB_RE.search(remainder):
        return True
    return bool(_leading_task(text, regions[0]))


_TASK_AFTER_BRIEF_RE = re.compile(
    r"\b(?:prompt\w*|brief\w*|instruct\w*|assign\w*|tell\w*|"
    r"sag\w*|diles?|d[ií]gale|"  # i18n-allow: input vocab
    r"anweis\w*|beauftrag\w*)\b\s*"
    r"(?:,?\s*(?:dass|damit|that|to|que)\s*)?"  # i18n-allow: input vocab
    # The pronoun standing for the fleet. "them" was missing, so "prompt
    # them, one fixes …" handed fresh agents a task that OPENED with the
    # word "them," — a fragment of the address baked into their work.
    r"(?:(?:sie|er|es|ihnen|ihm|they|it|them(?:\s+all)?|those|"  # i18n-allow: input vocab
    r"each(?:\s+one|\s+of\s+them)?|all(?:\s+of\s+them)?|everyone|"
    r"beiden?|allen?|cada\s+uno|todos|todas|ellos|ellas)"  # i18n-allow: input vocab
    r"[\s,:]+)?"
    # Spoken filler introducing the task itself: "prompt them LIKE one fixes
    # …", "prompte sie SO, DASS einer …". Part of the address, and the word
    # in front of the enumerator is exactly what kept the distributive
    # detector from seeing a clause-initial "one".
    r"(?:(?:like|so)\b[\s,:]*(?:(?:dass|damit|that|to|que)\b\s*)?)?",  # i18n-allow: input vocab
    re.IGNORECASE,
)


def spawn_instruction(user_text: str) -> str:
    """Return the work for new panes without the instruction to open them."""
    text = _spoken_correction_suffix((user_text or "").strip())
    regions = _spawn_regions(text)
    span = regions[0] if regions else None
    if span is None:
        nouns = list(_AGENT_NOUN_RE.finditer(text))
        actors = _open_verbs(text)
        if nouns and actors:
            noun, actor = min(
                ((noun, actor) for noun in nouns for actor in actors),
                key=lambda pair: _span_gap(*pair),
            )
            remainder = text[max(noun.end(), actor.end()) :].strip(" ,:-")
            remainder = re.sub(
                r"^(?:which|that|who|die|der|das|welche\w*|que)\s+",  # i18n-allow: input vocab
                "",
                remainder,
                flags=re.IGNORECASE,
            )
            if remainder:
                return remainder
        return text
    # The work starts after the LAST fleet clause — everything up to there is
    # the fleet description itself (see ``spawn_includes_task``).
    last = regions[-1]
    remainder = text[last.start + len(last.parse_text) :].strip(" ,:-")
    briefing = _TASK_AFTER_BRIEF_RE.search(remainder)
    if briefing is not None:
        task = remainder[briefing.end() :].strip(" ,:-")
        task = re.sub(
            r"^(?:to|that|dass|damit|que)\s+",  # i18n-allow: input vocab
            "",
            task,
            flags=re.IGNORECASE,
        )
        # "…open a new one and prompt it THERE" — the briefing verb is about
        # WHERE the work goes, and what follows it is a fragment, not the work.
        # Handing that fragment to a fresh agent as its task is how a pane got
        # briefed with the single word "there".
        if len(task) >= _MIN_INSTRUCTION_CHARS:
            return task
    if _INSTRUCTION_VERB_RE.search(remainder) is None:
        # Nothing to do AFTER the spawn clause: the work may be in front of it,
        # with the spawn as its fallback ("… let it do X; if no pane is called
        # that, open one"). Handing the new pane the fallback wording instead
        # is how a pane came up blank and was reported ready.
        lead = _leading_task(text, span)
        if lead:
            return lead
    return remainder or text


# --------------------------------------------------------------------------- #
# "prompt the five Claudes to X, the two Codexes to Y, the OpenCode to Z"      #
# --------------------------------------------------------------------------- #
# A mixed fleet is regularly briefed BY KIND in the same breath that asks for
# it — the counts and the product names are how the user tells the groups
# apart, so they are also how the work is handed out. ``spawn_instruction``
# cannot represent that: it returns ONE string, so every pane of an 8-pane
# fleet received the entire enumeration ("the five claudes to fix the login
# bug, prompt the two codexes to write tests …") as its own task, and each
# agent then picked whatever slice it liked (maintainer report 2026-08-12).
#
# Deterministic on purpose, like the fleet parse above: the user has already
# SAID which group gets which work, so there is nothing for a model to decide —
# routing it is string work, and string work must not cost a provider call.

#: The verbs that hand a GROUP its work. Broader than ``_BRIEFING_VERB_RE`` —
#: "tell"/"sag" and "ask"/"frag" are unambiguous here because this vocabulary
#: is only ever consulted in the remainder BEHIND a fleet request, where a
#: status-question reading does not exist.
_GROUP_BRIEF_VERB_RE = re.compile(
    r"\b(?:prompt\w*|anprompt\w*|instruct\w*|instruy\w*|anweis\w*|"  # i18n-allow: input vocab
    r"beauftrag\w*|brief\w*|assign\w*|encarga\w*|tell\w*|sag\w*|"  # i18n-allow: input vocab
    r"dile|d[ií]gale|frag\w*|ask\w*|pregunt\w*)\b",  # i18n-allow: input vocab
    re.IGNORECASE,
)

#: The modal directly behind a kind's name — the verbless way German (and
#: English) hands a group its work: "die Claudes SOLLEN die Tests fixen".
#: Commas are excluded from the gap so a name that merely opens a list cannot
#: borrow a modal from the next clause.
_GROUP_BRIEF_MODAL_RE = re.compile(
    r"[^.!?;,]{0,12}?\b(?:soll(?:en|te|ten)?|should|shall|must|"  # i18n-allow: input vocab
    r"m[uü]ss(?:en|t)?|deben?|deber[ií]an?)\b",  # i18n-allow: input vocab
    re.IGNORECASE,
)

#: What may stand between a briefing verb's addressee and its work: an optional
#: subordinator ("to" / "dass") and the pronoun re-stating the group. Part of
#: the ADDRESS, never of the task.
_GROUP_BRIEF_CONNECTIVE_RE = re.compile(
    r"\s*[,:]?\s*(?:(?:to|that|dass|damit|que)\b\s*)?"  # i18n-allow: input vocab
    r"(?:(?:sie|er|es|they|it|them)\b\s*)?",  # i18n-allow: input vocab
    re.IGNORECASE,
)

#: Trailing debris once the next group's clause is cut away: the conjunction
#: and the article that belonged to the NEXT addressee ("… fixen und der ").
_GROUP_TASK_TRIM_RE = re.compile(
    r"[\s,;.]*(?:\b(?:und|and|y|sowie|plus)\b)?"  # i18n-allow: input vocab
    r"[\s,;.]*(?:\b(?:the|die|der|das|den|dem|el|la|los|las)\b)?"  # i18n-allow: input vocab
    r"[\s,;.]*$",
    re.IGNORECASE,
)

#: Words allowed in front of the FIRST group brief without disqualifying the
#: per-group reading. Anything more substantial before it is a brief of its own
#: ("prompt each of them to do X and tell the codex …"), and the per-group map
#: would then silently withhold X from every pane the group clauses do not
#: name — so the whole map stands down instead.
_GROUP_BRIEF_LEAD_RE = re.compile(
    r"[\s.,:;!?—–-]*(?:\b(?:und|and|y|dann|then|also|bitte|"  # i18n-allow: input vocab
    r"please|jetzt|now|"
    r"the|die|der|das|den|los|las|el|la)\b[\s,]*)*",  # i18n-allow: input vocab
    re.IGNORECASE,
)

#: A group's task shorter than this is a fragment, not work ("do it" survives,
#: a stray article does not). Deliberately below ``_MIN_INSTRUCTION_CHARS``:
#: here there is no better fallback to prefer, and "write tests" is an order.
_MIN_GROUP_TASK_CHARS = 6


def _group_brief_anchors(text: str) -> list[tuple[int, int, str]]:
    """Every per-kind brief in ``text`` as ``(cut_start, task_start, cli)``.

    ``cut_start`` is where the PREVIOUS group's task ends (the verb for
    verb-anchored briefs, the name for modal-anchored ones); ``task_start`` is
    where this group's own task begins.

    A verb may govern only the FIRST name after it: in "prompt the claudes to
    fix codex bugs" the word "codex" stands inside the claudes' task, and the
    guard that keeps it there is the previous mention's position — a verb in
    front of an earlier addressee is spent.
    """
    out: list[tuple[int, int, str]] = []
    previous_end = -1
    for match in _AGENT_RE.finditer(text):
        agent = _canonical_agent(match.group("agent"))
        if agent is None:
            continue
        verb: re.Match[str] | None = None
        for candidate in _GROUP_BRIEF_VERB_RE.finditer(text, 0, match.start()):
            if candidate.start() <= previous_end:
                continue
            between = text[candidate.end() : match.start()]
            if len(between) <= _COUNT_AGENT_MAX_GAP and not re.search(r"[.!?;:]", between):
                verb = candidate
        previous_end = match.end()
        if verb is not None:
            lead = _GROUP_BRIEF_CONNECTIVE_RE.match(text, match.end())
            out.append((verb.start(), lead.end() if lead else match.end(), agent))
            continue
        modal = _GROUP_BRIEF_MODAL_RE.match(text, match.end())
        if modal is not None:
            out.append((match.start(), modal.end(), agent))
    return out


def spawn_group_tasks(user_text: str) -> dict[str, str]:
    """Each spawned kind's OWN task, or ``{}`` when the brief is shared.

    Reads the same remainder ``spawn_instruction`` reads — everything behind
    the last fleet clause — and splits it at the briefs that name a CLI. The
    caller maps each kind to the panes it just opened of that kind; a kind the
    brief does not name gets NO task, because "open two claudes and a codex,
    prompt the claudes to fix the tests" deliberately leaves the codex blank.

    ``{}`` — the shared-brief reading — whenever the remainder briefs nobody by
    kind, or carries work in front of the first kind-brief (that work belongs
    to everyone, and a partial map would silently withhold it).
    """
    text = _spoken_correction_suffix((user_text or "").strip())
    if len(text) < 6:
        return {}
    regions = _spawn_regions(text)
    if not regions:
        return {}
    last = regions[-1]
    remainder = text[last.start + len(last.parse_text) :]
    if not remainder.strip():
        return {}
    anchors = _group_brief_anchors(remainder)
    if not anchors:
        return {}
    lead = _GROUP_BRIEF_LEAD_RE.match(remainder)
    if lead is None or lead.end() < anchors[0][0]:
        return {}
    out: dict[str, str] = {}
    for index, (_cut, task_start, agent) in enumerate(anchors):
        stop = anchors[index + 1][0] if index + 1 < len(anchors) else len(remainder)
        task = remainder[task_start:stop].strip(" \t,:;-")
        task = " ".join(_GROUP_TASK_TRIM_RE.sub("", task).split())
        if len(task) < _MIN_GROUP_TASK_CHARS:
            continue
        out[agent] = f"{out[agent]}; {task}" if agent in out else task
    return out


_RECENT_FLEET_RE = re.compile(
    r"\b(?:jede[nr]?\s+(?:davon|dieser)|alle\s+davon|diese\w*|"
    r"each\s+(?:one|of\s+them)|all\s+of\s+them|those\s+(?:agents|terminals)|"
    r"cada\s+uno\s+de\s+ellos|todos\s+ellos)\b",  # i18n-allow: input vocab
    re.IGNORECASE,
)


def references_recent_fleet(user_text: str) -> bool:
    """Whether a follow-up points back to the panes opened in the prior turn."""
    return bool(_RECENT_FLEET_RE.search(user_text or ""))


# The user telling us a delivery did not happen, or asking for it again. These
# sentences name no pane and carry no instruction — they are only meaningful
# against the turn before them, which is exactly why every detector in this
# module used to return nothing for them.
#
# Live failure 2026-07-29 17:04 (BUG-121): after a briefing was stolen by
# another gate, "Du hast es gar nicht gepromptet" produced no routing signal at
# all, the realtime model answered alone, and it apologised and promised to do
# it "now" without anything happening. The next turn was identical and only
# worked because the model happened to call the action tool by itself. Two
# wasted turns and an apology are not a recovery path.
#
# Deliberately narrow: a complaint must be NEGATED or an explicit retry. "Hast
# du T7 gepromptet?" is a question and must keep reaching the report path.
# Matching *input vocabulary* across the supported locales, not prose.
_UNDELIVERED_COMPLAINT_RE = re.compile(
    r"(?:"
    # "du hast es (gar) nicht gepromptet" / "das war noch nicht gepromptet"
    r"\b(?:hast|hat|habt|war|wars|ist|wurde|wurden)\b[^.!?]{0,40}?"  # i18n-allow: input vocab
    r"\bnicht\b[^.!?]{0,30}?"  # i18n-allow: input vocab
    r"\b(?:geprompt\w*|gemacht|gesendet|geschickt|angekommen|"  # i18n-allow: input vocab
    r"beauftragt|informiert|gebrieft)\b"  # i18n-allow: input vocab
    r"|"
    # "da ist nichts passiert" / "es kam nichts an"
    r"\bnichts\b[^.!?]{0,30}?\b(?:passiert|angekommen|gekommen|"  # i18n-allow: input vocab
    r"gesendet|da)\b"  # i18n-allow: input vocab
    r"|"
    # "das hat nicht geklappt" / "hat nicht funktioniert"
    r"\bhat\b[^.!?]{0,20}?\bnicht\b[^.!?]{0,20}?"  # i18n-allow: input vocab
    r"\b(?:geklappt|funktioniert|geklappt)\b"  # i18n-allow: input vocab
    r"|"
    # Explicit retry: "mach das nochmal", "versuch es nochmal", "nochmal"
    r"\b(?:nochmal|noch\s+mal|erneut|wiederhol\w*)\b"  # i18n-allow: input vocab
    r"|"
    r"\byou\b[^.!?]{0,30}?\b(?:did\s*n[o']?t|have\s*n[o']?t|never)\b"
    r"[^.!?]{0,30}?\b(?:prompt\w*|send|sent|tell|told|brief\w*)\b"
    r"|"
    r"\b(?:that|it|this)\b[^.!?]{0,20}?\b(?:was|were|is)?\s*n[o']?t\b"
    r"[^.!?]{0,20}?\b(?:prompt\w*|sent|delivered|done)\b"
    r"|"
    r"\bnothing\b[^.!?]{0,25}?\b(?:happened|arrived|came\s+through|sent)\b"
    r"|"
    r"\b(?:try\s+again|do\s+it\s+again|once\s+more|again\s+please)\b"
    r"|"
    r"\bno\s+(?:lo\s+)?(?:has|ha)\b[^.!?]{0,30}?"  # i18n-allow: input vocab
    r"\b(?:enviado|hecho|mandado|prompteado)\b"  # i18n-allow: input vocab
    r"|"
    r"\bno\s+(?:pas[oó]|lleg[oó])\s+nada\b"  # i18n-allow: input vocab
    r"|"
    r"\b(?:int[eé]ntalo\s+de\s+nuevo|otra\s+vez|de\s+nuevo)\b"  # i18n-allow: input vocab
    r")",
    re.IGNORECASE,
)


def reports_undelivered(user_text: str) -> bool:
    """Whether the user is saying a pane briefing did not happen, or to retry.

    Answers only the shape of the sentence. Whether there IS a briefing to
    repeat, and whether it actually failed, is the caller's question — the
    workspace holds that evidence (``Terminal.last_prompt_at``), and answering
    it here would mean this module reaching into the registry.
    """
    return bool(_UNDELIVERED_COMPLAINT_RE.search(user_text or ""))


_CLOSE_VERB_RE = re.compile(
    # ``c(?:e|ie)rr`` carries the Spanish stem change: the imperative people
    # actually say is "CIERRA todos los terminales", and a ``cerr``-only stem
    # matched the infinitive while missing every spoken command.
    r"\b(?:schlie(?:ß|ss)\w*|beend\w*|stopp?\w*|zumach\w*|close\w*|"  # i18n-allow: input vocab
    r"quit\w*|"
    r"kill\w*|c(?:e|ie)rr\w*|det[eé]n\w*)\b",  # i18n-allow: input vocab
    re.IGNORECASE,
)

#: German closes things with a separable verb, so the particle lands at the end
#: of the clause: "mach alle Terminals ZU". Both halves are needed — the bare
#: "mach" is the OPEN verb, which is why this exact sentence used to open panes
#: instead of closing them, the worst kind of miss because the user watches the
#: opposite of what they asked for happen.
_CLOSE_PARTICLE_RE = re.compile(
    r"\b(?:mach|macht|mache|machen)\b[^.!?]{0,60}?\b(?:zu|dicht)\b",  # i18n-allow: input vocab
    re.IGNORECASE,
)
_ALL_PANES_RE = re.compile(
    r"\b(?:alle[nr]?|s[aä]mtliche[nr]?|jede[nr]?|all|every|"  # i18n-allow: input vocab
    r"each|todos?|todas?)\b",
    re.IGNORECASE,
)
_CLOSE_STATE_QUESTION_RE = re.compile(
    r"^(?:are|is|sind|ist|est[aá]n?)\b[^?]*\b(?:closed|geschlossen|cerrad\w*)\b",
    re.IGNORECASE,
)

# How far the quantifier and the pane noun may sit apart and still be ONE noun
# phrase ("all the open terminals"), rather than two things the sentence merely
# mentions on its way past ("all the failing tests in the terminals"). Roomy
# enough for an adjective and a CLI name, far below the reach of an unrelated
# object.
_CLOSE_QUANTIFIER_MAX_GAP = 20
# How far the closing verb may sit from the panes it closes. Both word orders
# are a character or two in practice — "close all terminals" and "alle
# Terminals schließen" — so the allowance covers an interposed adverb, never a
# second clause.
_CLOSE_VERB_MAX_GAP = 30

# The utterance is HANDING WORK to the panes rather than closing them.
#
# This is the entire difference between "close all terminals" and "tell all the
# terminals to stop the dev server", and without the distinction the second one
# killed every agent in the workspace: the detector asked only whether a pane
# noun, an "all" word and a stop-verb appeared ANYWHERE in the sentence, which
# every fleet instruction carrying the word "stop" satisfies. Measured on real
# phrasings, eight of nine ordinary fleet instructions were read as a request to
# destroy the workspace.
#
# A briefing verb only counts in FRONT of the pane noun, because that is where
# it governs: "tell all terminals to stop" briefs them, while "close all
# terminals and tell me when you are done" really does close them.
_CLOSE_BRIEFING_RE = re.compile(
    r"\b(?:tell\w*|ask\w*|have|let|instruct\w*|assign\w*|prompt\w*|brief\w*|"
    r"sag\w*|frag\w*|lass\w*|l[aä]sst|anweis\w*|beauftrag\w*|schick\w*|"  # i18n-allow: input vocab
    r"dile|d[ií]gale|pregunt\w*|pide|pida|manda|env[ií]a|encarga\w*)\b",  # i18n-allow: input vocab
    re.IGNORECASE,
)


def _closes_the_fleet(clause: str) -> bool:
    """Does this clause ask for the PANES THEMSELVES to be closed?

    Three things have to line up, and each one removes a way of being wrong:

    1. the quantifier belongs to the panes ("all the terminals") rather than to
       something running inside them ("all the node processes in the
       terminals"),
    2. the closing verb sits close enough to govern that phrase instead of
       merely sharing a sentence with it,
    3. nothing in front of the pane noun turns the sentence into an instruction
       FOR the panes.

    The asymmetry is deliberate. A missed close costs one click on a button
    that is right there; a false one kills every coding agent in the workspace
    with no confirmation and no undo — so every ambiguous shape stands down.
    """
    for pane in _pane_nouns(clause):
        if _CLOSE_BRIEFING_RE.search(clause, 0, pane.start()) is not None:
            continue
        # The German separable verb wraps AROUND the panes it closes ("mach
        # alle Terminals zu"), so containment is the test, not distance.
        for bracket in _CLOSE_PARTICLE_RE.finditer(clause):
            if bracket.start() <= pane.start() and pane.end() <= bracket.end():
                if _ALL_PANES_RE.search(clause, bracket.start(), pane.start()):
                    return True
        for quantifier in _ALL_PANES_RE.finditer(clause, 0, pane.start()):
            if pane.start() - quantifier.end() > _CLOSE_QUANTIFIER_MAX_GAP:
                continue
            for verb in _CLOSE_VERB_RE.finditer(clause):
                # Either word order: the verb ahead of the phrase ("close all
                # terminals") or behind it ("alle Terminals schließen").
                ahead = quantifier.start() - verb.end()
                behind = verb.start() - pane.end()
                if 0 <= ahead <= _CLOSE_VERB_MAX_GAP or 0 <= behind <= _CLOSE_VERB_MAX_GAP:
                    return True
    return False


def detect_close_fleet(user_text: str) -> CloseTerminalsRequest | None:
    """A request to close all matching panes in the open workspace.

    Deliberately hard to trigger, and read one CLAUSE at a time for the same
    reason ``_spawn_span`` is: a sentence that mentions terminals early and
    stopping something late is two thoughts, not a close request. What the
    clause has to actually contain lives in ``_closes_the_fleet``.
    """
    text = (user_text or "").strip()
    if len(text) < 6 or _QUESTION_OPENER_RE.search(text) or _CLOSE_STATE_QUESTION_RE.search(text):
        return None
    for clause_match in _CLAUSE_RE.finditer(text):
        clause = clause_match.group(0)
        if not _closes_the_fleet(clause):
            continue
        # Read from the clause that actually closes, so a CLI named elsewhere in
        # the sentence cannot narrow (or widen) which panes are stopped.
        agent_match = _AGENT_RE.search(clause)
        agent = None
        if agent_match is not None:
            agent = _canonical_agent(agent_match.group("agent"))
        return CloseTerminalsRequest(agent=agent, utterance=text)
    return None


def _is_agent_fleet(text: str, names: list[str] | None) -> bool:
    """True for a fleet request that says "agents" instead of "terminals".

    The mandatory pane noun is what makes claiming a turn safe, and it stays
    mandatory for everything but this one narrow shape. The maintainer's own
    phrasing for a fleet does not contain it (2026-07-26): "can you spawn five
    deep-dive agents that analyse X, and divide it across different areas" —
    which opened nothing at all, because the sentence names no terminal and the
    background path does not divide work across panes either.

    All four conditions must hold, and each one removes a way of being wrong:

    1. a workspace is OPEN — with nowhere to put them, a fleet request really is
       the background path's;
    2. the wording is not explicitly about a background worker;
    3. SEVERAL agents are asked for — one agent on one job is exactly what a
       mission worker is;
    4. the sentence carries fleet semantics: divide the work between them, or
       name the coding CLI to run. A background mission is never described as
       "split across five agents by area".
    """
    running = _running_names() if names is None else list(names)
    if not running:
        return False
    if _BACKGROUND_RE.search(text):
        return False
    if _AGENT_NOUN_RE.search(text) is None:
        return False
    if _spoken_count(text) < 2:
        return False
    return wants_split(text) or _AGENT_RE.search(text) is not None


#: How close an additive must hug a number to vouch for it as a fleet size
#: when the pane noun is elided ("noch drei", "three more"). Much tighter
#: than ``_COUNT_AGENT_MAX_GAP`` on purpose: an additive a whole clause away
#: is ordinary conversation, and reading it as spawn evidence would hand the
#: veto below back the very over-reach it exists to stop.
_ADDITIVE_NEAR_GAP = 16


def _briefed_position_only(parse_text: str) -> bool:
    """Whether the clause briefs a pane named BY POSITION, not sizes a fleet.

    The live 2026-08-12 failure this pins: "prompt … terminal tft zwei, dass
    es ein deep dive machen soll" — speech recognition mangled the call-sign
    "T2" into "tft zwei", the addressing detector found no pane, and the
    number that was the pane's NAME was then re-read here as a fleet size:
    two fresh panes opened and were briefed while the addressed one sat idle.

    Word order is what separates the two readings. A fleet size stands in
    FRONT of the pane noun ("öffne zwei Terminals"); a position stands BEHIND
    it ("Terminal zwei"), and ``spoken_positions`` is the one authority on
    that shape. So when the clause carries a briefing verb and the number the
    spawn path would read as its count — the FIRST one, which is
    ``_spoken_count``'s contract — is part of a spoken position, it is a
    brief for an existing pane, never a spawn, even when no pane answers to
    that position: falling through to the ordinary brain, which can say
    "there is no terminal two", beats opening a fleet the user never asked
    for.

    One exception keeps mixed sentences honest: a number OUTSIDE every
    position that still reads as a fleet size of its own — fronting a pane
    noun or a CLI name ("… und öffne noch drei Terminals"), or hugging an
    additive when the noun is elided ("… öffne noch drei", "open three
    more") — keeps the spawn half of such a clause alive. The additive is
    deliberately the ONLY elision evidence: open-verb stems double as task
    vocabulary ("mach einen Deep-Dive-Report" briefs, it does not open), so
    a verb near the number proves nothing. A stray task word that merely
    doubles as a number ("dass es EIN deep dive machen soll") shows none of
    those shapes and vetoes nothing.
    """  # i18n-allow: quoted spoken input
    if _FLEET_BRIEF_RE.search(parse_text) is None:
        return False
    positions = spoken_positions(parse_text)
    if not positions:
        return False
    counts = _count_tokens(parse_text)
    if not counts:
        return False

    def _covered(c_start: int, c_end: int) -> bool:
        return any(start <= c_start and c_end <= end for start, end, _n in positions)

    if not _covered(counts[0][0], counts[0][1]):
        return False
    for c_start, c_end, _value in counts:
        if _covered(c_start, c_end):
            continue
        ahead = parse_text[c_end : c_end + _COUNT_AGENT_MAX_GAP]
        if _PANE_NOUN_RE.search(ahead) or _AGENT_RE.search(ahead):
            return False
        near_before = parse_text[max(0, c_start - _ADDITIVE_NEAR_GAP) : c_start]
        near_after = parse_text[c_end : c_end + _ADDITIVE_NEAR_GAP]
        if _ADDITIVE_RE.search(near_before) or _ADDITIVE_RE.search(near_after):
            return False
    return True


def _mask_spoken_positions(text: str) -> str:
    """``text`` with every spoken pane position blanked to spaces.

    The count fallback in ``detect_spawn`` reads the FIRST number of the
    clause, and a position carries a number that is a pane's NAME, not a
    size: "öffne Terminal 2" asks for one pane, spoken while looking at the
    grid — the open-verb sibling of the briefed shape above, and the same
    live 2026-08-12 misreading one verb over. Blanking rather than removing
    keeps every offset stable for the shapes parsed around it.
    """  # i18n-allow: quoted spoken input
    out = text
    for start, end, _number in spoken_positions(text):
        out = f"{out[:start]}{' ' * (end - start)}{out[end:]}"
    return out


def detect_spawn(user_text: str, *, names: list[str] | None = None) -> SpawnTerminalsRequest | None:
    """A request to open more terminals, or ``None``.

    ``names`` is the live call-signs (injectable for tests). They are needed for
    one decision only: an utterance that ADDRESSES a pane is that pane's prompt,
    even when it mentions terminals: telling a named pane to open a terminal is
    work for THAT pane, not a request for another one. Addressing therefore wins,
    and it is checked here so both callers inherit the same order.
    """
    original = (user_text or "").strip()
    if len(original) < 6:
        return None
    # A spoken self-correction RETRACTS everything in front of it, so the
    # corrected request is parsed and the withdrawn half never opens a pane.
    # ``utterance`` keeps the user's full words either way — the readback and
    # any follow-up question must be able to quote what was actually said.
    text = _spoken_correction_suffix(original)
    regions = _spawn_regions(text)
    span = regions[0] if regions else None
    agent_fleet = span is None and _is_agent_fleet(text, names)
    if span is None and not agent_fleet:
        return None
    # A fast follow-up can be recombined with the previous completed turn by
    # the voice continuation window. Judge the clause that actually requests
    # panes, not an unrelated earlier question (live 2026-08-09: "Was geht
    # ab? ... bitte 5 Cloth-Code-Terminal spawnen" was sent to a background
    # worker). A real information question still starts inside ``parse_text``
    # and remains vetoed.
    question_scope = span.parse_text if span is not None else text
    question_scope = _QUESTION_DISCOURSE_PREFIX_RE.sub("", question_scope)
    if _QUESTION_OPENER_RE.search(question_scope) is not None:
        return None
    addressed_text = span.parse_text if span is not None else text
    addressed_before_spawn = (
        span is not None and span.start > 0 and detect(text[: span.start], names=names) is not None
    )
    if addressed_before_spawn or detect(addressed_text, names=names) is not None:
        return None

    # One scope per fleet clause: "Öffne zwei Terminals. Mach noch drei Codex
    # Terminals auf" is ONE request said in two sentences, and reading only
    # the first opened it partially — with the second sentence handed to the
    # fresh panes as their coding task (live 2026-08-12, ``_spawn_regions``).
    scopes = [region.parse_text for region in regions] or [text]

    if _briefed_position_only(scopes[0]):
        return None

    unsupported: list[str] = []
    uncertain_cli: list[UncertainCli] = []
    collected: list[SpawnGroup] = []
    for scope in scopes:
        for label in _unsupported_clis(scope):
            if label not in unsupported:
                unsupported.append(label)
        for item in _uncertain_clis(scope):
            if all(item.spoken != seen.spoken for seen in uncertain_cli):
                uncertain_cli.append(item)
        collected.extend(_spoken_groups(scope))

    groups = _merge_groups(collected)
    if groups:
        return SpawnTerminalsRequest(
            count=sum(g.count for g in groups),
            agent=groups[0].agent,
            utterance=original,
            groups=groups,
            unsupported=tuple(unsupported),
            uncertain_cli=tuple(uncertain_cli),
        )

    if unsupported and not groups:
        # Everything that was counted was counted for a CLI this workspace does
        # not have. Falling through would read the number again and open that
        # many panes of whatever CLI happened to be inherited — refusing the
        # request by name and then granting it anyway, which is two answers to
        # one question. No groups means no panes; the caller says why.
        return SpawnTerminalsRequest(
            count=0,
            agent=None,
            utterance=original,
            groups=(),
            unsupported=tuple(unsupported),
            uncertain_cli=tuple(uncertain_cli),
        )

    agent_match = _AGENT_RE.search(scopes[0])
    agent: str | None = None
    if agent_match is not None:
        agent = _canonical_agent(agent_match.group("agent"))
    # A number inside a spoken position is the pane's NAME and never feeds
    # the fallback: "öffne Terminal 2" opens ONE pane.  # i18n-allow: quoted spoken input
    count = _spoken_count(_mask_spoken_positions(scopes[0]))
    return SpawnTerminalsRequest(
        count=count,
        agent=agent,
        utterance=original,
        groups=(SpawnGroup(count=count, agent=agent),),
        unsupported=tuple(unsupported),
        uncertain_cli=tuple(uncertain_cli),
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
    return bool(_DIVIDE_RECIPIENT_RE.search(text) or _DIVIDE_AREA_RE.search(text))


# --------------------------------------------------------------------------- #
# "one fixes the macOS bug, one fixes the Linux bug"                           #
# --------------------------------------------------------------------------- #
# The user enumerating the division THEMSELVES. ``wants_split`` catches the
# request to divide ("teilt es unter euch auf"); this catches a division
# already made. Both must end in the same place — the split planner — because
# the panes need DIFFERENT briefs either way: delivering the whole enumeration
# to every pane is how two fresh agents both fixed the macOS bug while the
# Linux bug sat unclaimed (maintainer report 2026-08-12).
#
# The trap: "one"/"einer" is among the most ordinary words there are, and the
# fleet description itself counts panes with it ("one Codex"). Two conditions
# keep it honest — and the callers only ever pass the TASK text (the remainder
# behind every fleet clause), never the raw utterance:
#
# 1. the enumerator must OPEN its clause: start of text, or directly behind
#    a comma/semicolon/colon or a coordination ("and"/"und"/"y"). "bug one"
#    and "Terminal eins" never stand there;
# 2. it must carry its own predicate inside its clause — an instruction verb,
#    a modal, or the elided-verb complement ("und einer auf Linux", where the
#    preposition does the pointing). "The other"/"der andere" is exempt from
#    the predicate: that word pair has no counting reading at all, so it is
#    distributive on its own ("one takes the backend, the other the
#    frontend").
#
# Two qualifying enumerators are the evidence; one is an ordinary sentence.

_DISTRIBUTIVE_PREFIX = r"(?:^|[,;:]|\b(?:und|and|y)\b)\s*"  # i18n-allow: input vocab

_DISTRIBUTIVE_ONE_RE = re.compile(
    _DISTRIBUTIVE_PREFIX
    + r"(?:ein(?:e[rs]?|s)?|one(?:\s+of\s+(?:them|you))?|un[oa]|"  # i18n-allow: input vocab
    r"(?:(?:der|die|das|the|el|la)\s+)?"  # i18n-allow: input vocab
    r"(?:erste[rs]?|zweite[rs]?|dritte[rs]?|vierte[rs]?|"  # i18n-allow: input vocab
    r"first|second|third|fourth|"
    r"primer[oa]?|segund[oa]?|tercer[oa]?))\b",
    re.IGNORECASE,
)

_DISTRIBUTIVE_OTHER_RE = re.compile(
    _DISTRIBUTIVE_PREFIX
    + r"(?:(?:der|die|das)\s+andere[nrs]?|the\s+other(?:\s+one)?|"  # i18n-allow: input vocab
    r"(?:el\s+|la\s+)?otr[oa])\b",
    re.IGNORECASE,
)

#: What counts as an enumerator's own predicate when it is not a full
#: instruction verb: a modal handing the slice over, or the preposition of an
#: elided-verb complement.
_DISTRIBUTIVE_PREDICATE_RE = re.compile(
    r"\b(?:soll\w*|m[uü]ss\w*|kann|k[oö]nn\w*|darf\w*|"  # i18n-allow: input vocab
    r"should|shall|must|can|could|will|would|takes?|gets?|does|"
    r"deber?[ií]?an?|pueden?|podr[ií]an?|"
    r"auf|f[uü]r|on|for|en|an|mit|with|con)\b",  # i18n-allow: input vocab
    re.IGNORECASE,
)

#: How far an enumerator's predicate may sit behind it, within its clause.
_DISTRIBUTIVE_PREDICATE_REACH = 60


def _distributive_clause(text: str, start: int) -> str:
    window = text[start : start + _DISTRIBUTIVE_PREDICATE_REACH]
    return re.split(r"[.!?;]", window, maxsplit=1)[0]


def distributes_tasks(task_text: str) -> bool:
    """True when the task hands each pane a DIFFERENT job by enumeration.

    Consulted on the extracted TASK text of a fleet brief (never the raw
    utterance — the fleet description counts panes with the same words). A
    True here routes the brief through the split planner, exactly like
    ``wants_split``, so every pane receives its own slice instead of the whole
    enumeration.
    """
    text = (task_text or "").strip()
    if len(text) < 12:
        return False
    heads = 0
    for match in _DISTRIBUTIVE_ONE_RE.finditer(text):
        clause = _distributive_clause(text, match.end())
        if _INSTRUCTION_VERB_RE.search(clause) or _DISTRIBUTIVE_PREDICATE_RE.search(clause):
            heads += 1
            if heads >= 2:
                return True
    heads += sum(1 for _ in _DISTRIBUTIVE_OTHER_RE.finditer(text))
    return heads >= 2


#: "both of you", "you two", "all three of them", "die zwei Terminals". What
#: these have in common is a COUNT the utterance states out loud, which is the
#: only evidence that survives a call-sign speech recognition mangled beyond
#: recognition. Matching *input vocabulary*, not prose.
_PLURAL_ADDRESS_RE = re.compile(
    r"\b(?:"
    # explicit "both" / "all of you", in every supported language
    r"beide[nsr]?|alle\s+beide|ihr\s+(?:beide[nr]?|zwei|drei|vier)|euch\s+beide[nr]?|"
    r"both(?:\s+of\s+(?:you|them))?|all\s+(?:of\s+)?(?:you|them)|you\s+(?:two|three|four)|"
    r"ambos|ambas|los\s+dos|las\s+dos|ustedes\s+dos|"
    # a spoken count next to what is being counted
    r"(?:zwei|drei|vier|f[üu]nf|two|three|four|five|dos|tres|cuatro|cinco|[2-9])\s+"
    r"(?:der\s+|die\s+|von\s+den\s+|of\s+the\s+|de\s+los\s+)?"
    r"(?:terminals?|terminales|panes?|agent(?:s|en)?|instanz\w*|instances?)"
    r")\b",
    re.IGNORECASE,
)


def expects_several(user_text: str) -> bool:
    """True when the utterance says out loud that MORE THAN ONE pane is meant.

    The one signal that outlives a garbled call-sign. Live 2026-07-27 19:07:
    "Alexa and Dave should both do a deep dive" — "Alexa" was recoverable as
    Alex, "Dave" matched no pane at any threshold, and the turn quietly briefed
    one agent while the user watched for two. Nothing in the name resolver can
    fix that; the word "both" can, because it says the count independently of
    whether either name survived transcription.

    Deliberately narrow: an explicit "both/all of you/you two", or a spoken
    number standing next to the thing being counted. A bare plural noun does
    NOT qualify — "look at the terminals" states no count.
    """
    text = (user_text or "").strip()
    if len(text) < 6:
        return False
    return bool(_PLURAL_ADDRESS_RE.search(text))


def _spawn_words_describe_pane_work(text: str, candidates: list[str]) -> bool:
    """True when the spawn vocabulary describes what a PANE should do.

    The tie-breaker for the one utterance shape the vehicle stand-down below
    cannot read: telling a coding agent to fan out. "Sub-agent" is not Jarvis
    vocabulary in this workspace — it is what every agentic coding CLI calls its
    own parallel helpers, and asking for them is among the most ordinary things
    a user says to a pane. Live failure 2026-07-27 (voice session 20:00): "let
    Alex and Ellis do a deep dive … and they should spawn swarms of sub-agents"
    named two running panes, was detected as an instruction to both — and was
    then handed to a background mission worker anyway, because the sentence
    contained "sub-agents" and "spawn". Both terminals sat idle.

    The signal is WORD ORDER, and it is the same one a listener uses:

    * "spawn an agent that helps Kai" — the vehicle word OPENS the request, the
      call-sign is what the new agent is for. An order to Jarvis;
    * "Alex should spawn sub-agents" — the call-sign comes first and the vehicle
      word sits inside the work being described. An order to Alex.

    So every vehicle word must stand BEHIND the first named pane. One in front
    is enough to hand the turn back to the spawn path, which keeps the mirror
    bug — a workspace swallowing a genuine delegation request — shut.

    The name has to be CERTAIN (exact, or a spelling folding to the same sound):
    this branch decides against a background mission the user may well have
    wanted, and a merely similar word is not evidence enough to do that.
    """
    if not candidates:
        return False
    from jarvis.brain.spawn_gate import spawn_vehicle_spans

    # Both positions must be measured in the SAME string: canonicalisation
    # rewrites a garbled call-sign ("Elis" → "Ellis") and shifts every offset
    # behind it.
    working = _canonical_text(text, candidates)
    mentions = _mentions(working, candidates, fuzzy=False)
    if not mentions:
        return False
    spans = spawn_vehicle_spans(working)
    if not spans:
        return False
    first_name_at = mentions[0][0]
    return all(start > first_name_at for start, _ in spans)


def spawn_vehicle_outranks_workspace(user_text: str, *, names: list[str] | None = None) -> bool:
    """True when explicit spawn vocabulary should beat an addressed pane.

    The vehicle stand-down, as ONE function, because it now has two exceptions
    and three callers (``owns_turn`` for both routing gates, and the router's
    Agentic-IDE fast path, which carries its own candidate roster). A caller
    that re-implemented it would strand precisely the exception turns: the spawn
    gate refuses the mission, the fast path refuses to type, and the user is
    left with silence — the failure this whole area exists to end.
    """
    from jarvis.brain.spawn_gate import names_spawn_vehicle

    text = (user_text or "").strip()
    if not names_spawn_vehicle(text):
        return False
    candidates = _running_names() if names is None else list(names)
    return not _spawn_words_describe_pane_work(text, candidates)


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
       the bug this module fixes. That holds INSIDE coding mode too: the mode
       is not a reason to refuse a delegation the user asked for. The one
       exception is an utterance whose spawn words describe a PANE's work; see
       ``_spawn_words_describe_pane_work``.
    """
    text = (user_text or "").strip()
    if not text:
        return False
    if detect_close_fleet(text) is not None:
        return True
    if detect_spawn(text, names=names) is not None:
        return True
    if spawn_vehicle_outranks_workspace(text, names=names):
        return False
    if detect(text, names=names) is not None:
        return True
    # Only the process-wide form can honestly consult the active UI surface.
    # Injected candidate lists are used by pure detector tests and must not
    # acquire hidden global state.
    return names is None and detect_visible(text) is not None


__all__ = [
    "KIND_PROMPT",
    "KIND_REPORT",
    "CloseTerminalsRequest",
    "SpawnGroup",
    "SpawnTerminalsRequest",
    "TerminalIntent",
    "detect",
    "detect_all",
    "detect_visible",
    "detect_close_fleet",
    "detect_spawn",
    "distributes_tasks",
    "expects_several",
    "owns_turn",
    "references_recent_fleet",
    "reports_undelivered",
    "spawn_group_tasks",
    "spawn_includes_task",
    "spawn_instruction",
    "spawn_vehicle_outranks_workspace",
    "wants_split",
]
