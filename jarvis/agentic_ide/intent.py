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
    """
    if not candidates:
        return text

    def _replace(match: re.Match[str]) -> str:
        word = match.group(0)
        hit = resolve(word, candidates, fuzzy=False)
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
    return {
        name for _, _, name in _mentions(text, candidates, fuzzy=False)
    } - _people_of_the_world(text, candidates)


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
        name
        for name, pattern in _compile(_REPORT_TEMPLATES, candidates)
        if pattern.search(working)
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

    # The collective address reaches EVERY open pane, so it is the costliest
    # thing a misheard word can trigger — a question about a public figure that
    # speech recognition turned into "sag allen …" would brief the whole
    # workspace with it. Checked here rather than inside the templates because
    # it is a property of the sentence, not of any one addressing shape.
    if any(pattern.search(working) for pattern in _EVERYONE_TEMPLATES) and not (
        _is_outside_world_talk(working)
    ):
        kind = (
            KIND_REPORT
            if report_anchors and not (prompt_anchors or weak_prompt_anchors)
            else KIND_PROMPT
        )
        return [
            TerminalIntent(
                terminal=name,
                kind=kind,
                instruction="" if kind == KIND_REPORT
                else _useful_instruction(working, name),
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
    elif weak_prompt_anchors:
        # Above the question branch on purpose: politeness and the German
        # sentence bracket both produce sentences that END in a question mark
        # while HANDING WORK OVER, and reading those as status requests is the
        # exact failure this whole area keeps coming back to. Below the report
        # shapes, so "ist Alex fertig?" is untouched.
        kind = KIND_PROMPT
        anchors = weak_prompt_anchors
    elif (asked_about := _questioned_panes(working, candidates)):
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
    elif mentions and (
        _looks_like_instruction(working) or _BRIEFING_VERB_RE.search(working)
    ):
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
        # live 2026-07-26 session had "keine" reaching "Kai" and briefing a  # i18n-allow: quoted transcript tokens
        # second agent nobody had named. So here — and only here — the name has
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

    addressed = _coordinated_group(mentions, working, anchors)
    ordered = [name for _, _, name in mentions if name in addressed]
    # An anchor matched by a template but never located as a word (a garbled
    # transcript the templates still caught) keeps its place at the end.
    ordered += [name for name in anchors if name not in ordered]

    shared = "" if kind == KIND_REPORT else _useful_instruction(working, *ordered)
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
    r"investigate|look|read|find|search|explain|document|"
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
    # Both ASCII spellings of every umlaut word: a transcript may drop the
    # umlaut ("funf") or transliterate it ("fuenf"), and only the first was
    # covered — so "starte fünf Agenten" fell back to a count of one whenever
    # the provider wrote it the common way.
    "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "funf": 5, "fuenf": 5,  # i18n-allow: number words
    "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11,  # i18n-allow: numbers
    "zwölf": 12, "zwolf": 12, "zwoelf": 12,  # i18n-allow: number words
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
_AGENT_SPELLINGS: dict[str, tuple[str, ...]] = {
    "claude": ("claude", "cloude", "claud", "clode", "klaude", "kloude"),
    "codex": ("codex", "kodex", "codecs", "codeks"),
}

# Spellings that are ordinary words in their own right, so on their own they
# mean nothing here — "in the cloud" is not a request for a pane. They count
# only with the product's second word behind them: "cloud code" is unmistakable.
_AGENT_SPELLINGS_REQUIRING_CODE: dict[str, tuple[str, ...]] = {
    "claude": ("cloud", "clawed", "clod", "loud"),
}

_AGENT_BY_SPELLING: dict[str, str] = {
    spelling: canonical
    for table in (_AGENT_SPELLINGS, _AGENT_SPELLINGS_REQUIRING_CODE)
    for canonical, spellings in table.items()
    for spelling in spellings
}


def _agent_alternation() -> str:
    """The regex fragment matching every accepted spelling of a CLI's name.

    Longest first so a spelling that is the prefix of another (``claud`` of
    ``claude``) can never shadow it.
    """
    parts = [
        rf"{re.escape(spelling)}(?:\s+code)?"
        for table in (_AGENT_SPELLINGS,)
        for spellings in table.values()
        for spelling in spellings
    ]
    parts += [
        rf"{re.escape(spelling)}\s+code"
        for spellings in _AGENT_SPELLINGS_REQUIRING_CODE.values()
        for spelling in spellings
    ]
    return "|".join(sorted(parts, key=len, reverse=True))


_AGENT_ALTERNATION = _agent_alternation()

_AGENT_RE = re.compile(rf"\b(?P<agent>{_AGENT_ALTERNATION})\b", re.IGNORECASE)


def _canonical_agent(raw: str) -> str | None:
    """The CLI a matched name means, or ``None`` for one this parser cannot place.

    ``None`` is unreachable through the pattern above — every alternative it
    offers is a key of the table — and callers still handle it, because a
    spelling added to one and not the other must degrade to "no CLI named"
    rather than to a wrong one.
    """
    name = " ".join(str(raw or "").casefold().split())
    if name.endswith(" code"):
        name = name[: -len(" code")].strip()
    return _AGENT_BY_SPELLING.get(name)


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
    for match in re.finditer(r"\b(?:\d{1,3}|[^\W\d_]+)\b", text, re.UNICODE):
        raw = match.group(0).casefold()
        if raw.isdigit():
            return max(1, min(int(raw), MAX_TERMINALS))
        cleaned = "".join(ch for ch in raw if ch.isalpha())
        if cleaned in _NUMBER_WORDS:
            return max(1, min(_NUMBER_WORDS[cleaned], MAX_TERMINALS))
    return 1


#: A count followed by an agent name — "5 Codex", "drei neue Claude Code",
#: "tres terminales de Codex". The filler between them covers the words people
#: actually put there: the pane noun itself (Spanish and English both say
#: "three terminals of Codex" as often as "three Codex terminals") plus the
#: usual qualifiers. Bounded at two words so the count and the agent cannot
#: drift into different clauses of the sentence.
_COUNT_AGENT_RE = re.compile(
    r"\b(?P<count>\d{1,3}|[a-zäöüñ]+)\s+"  # i18n-allow: input vocab
    r"(?:(?:neue|weitere|zus[aä]tzliche|more|new|extra|additional|de|del|"  # i18n-allow: input vocab
    r"otros|otras|m[aá]s|terminals?|terminales|panes?|tabs?)\s+){0,2}"
    rf"(?P<agent>{_AGENT_ALTERNATION})\b",
    re.IGNORECASE,
)


def _spoken_groups(text: str) -> tuple[SpawnGroup, ...]:
    """The fleet the utterance describes, group by group.

    Returns an empty tuple when no count/agent pair is spelled out, which is the
    common single-group case ("three more terminals") and stays with the flat
    count/agent fields.

    Groups naming the SAME agent are merged: "two Codex and two more Codex" is
    four Codex panes, and two groups would open them in two batches for no
    reason.
    """
    from .session import MAX_TERMINALS

    found: list[SpawnGroup] = []
    for match in _COUNT_AGENT_RE.finditer(text):
        raw = match.group("count").casefold()
        if raw.isdigit():
            count = int(raw)
        else:
            cleaned = "".join(ch for ch in raw if ch.isalpha())
            if cleaned not in _NUMBER_WORDS:
                continue
            count = _NUMBER_WORDS[cleaned]
        agent = _canonical_agent(match.group("agent"))
        if agent is None:
            continue
        found.append(SpawnGroup(count=max(1, count), agent=agent))

    if len(found) < 2:
        # One pair is the plain single-agent request; the flat fields already
        # describe it and going through groups would only duplicate the parse.
        return ()

    merged: dict[str, int] = {}
    for group in found:
        key = group.agent or ""
        merged[key] = merged.get(key, 0) + group.count

    # Clamp the TOTAL, not each group: the workspace maximum is a property of
    # the workspace. Trimming from the back keeps the groups the user named
    # first intact rather than shrinking all of them into uselessness.
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


def _span_gap(left: re.Match[str], right: re.Match[str]) -> int:
    if left.end() < right.start():
        return right.start() - left.end()
    if right.end() < left.start():
        return left.start() - right.end()
    return 0


_FLEET_BRIEF_RE = re.compile(
    r"\b(?:jede\w*\s+(?:davon|dieser)|each\s+(?:one|of\s+them)|"
    r"prompt\w*|brief\w*|instruct\w*|assign\w*|tell\w*|"
    r"anweis\w*|beauftrag\w*|gib\w*\s+.*\baufgabe\w*)\b",  # i18n-allow: input vocab
    re.IGNORECASE,
)


def _spawn_span(text: str) -> _SpawnSpan | None:
    """The bounded clause that genuinely asks for panes to be opened."""
    for clause_match in _CLAUSE_RE.finditer(text):
        clause = clause_match.group(0)
        panes = list(_PANE_NOUN_RE.finditer(clause))
        actors = list(_OPEN_VERB_RE.finditer(clause)) + list(_ADDITIVE_RE.finditer(clause))
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
        # that task are not additional pane groups.
        parse_end = len(clause)
        briefing = _FLEET_BRIEF_RE.search(clause, anchor_end)
        if briefing is not None:
            parse_end = briefing.start()
        return _SpawnSpan(
            start=clause_match.start(),
            end=clause_match.start() + anchor_end,
            parse_text=clause[:parse_end],
        )
    return None


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


def spawn_includes_task(user_text: str) -> bool:
    """Whether a pane-spawn request also tells the new fleet to do work."""
    text = (user_text or "").strip()
    span = _spawn_span(text)
    if span is None:
        # The terminal-noun-free agent-fleet form can still carry a task.
        request = detect_spawn(text)
        return request is not None and bool(_INSTRUCTION_VERB_RE.search(text))
    remainder = text[span.end :]
    if _FLEET_BRIEF_RE.search(remainder) or _INSTRUCTION_VERB_RE.search(remainder):
        return True
    return bool(_leading_task(text, span))


_TASK_AFTER_BRIEF_RE = re.compile(
    r"\b(?:prompt\w*|brief\w*|instruct\w*|assign\w*|tell\w*|"
    r"anweis\w*|beauftrag\w*)\b\s*"
    r"(?:,?\s*(?:dass|damit|that|to|que)\s*)?"
    r"(?:(?:sie|er|es|they|it|each(?:\s+one)?|cada\s+uno)\s+)?",
    re.IGNORECASE,
)


def spawn_instruction(user_text: str) -> str:
    """Return the work for new panes without the instruction to open them."""
    text = (user_text or "").strip()
    span = _spawn_span(text)
    if span is None:
        nouns = list(_AGENT_NOUN_RE.finditer(text))
        actors = list(_OPEN_VERB_RE.finditer(text))
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
    remainder = text[span.end :].strip(" ,:-")
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


_RECENT_FLEET_RE = re.compile(
    r"\b(?:jede[nr]?\s+(?:davon|dieser)|alle\s+davon|diese\w*|"
    r"each\s+(?:one|of\s+them)|all\s+of\s+them|those\s+(?:agents|terminals)|"
    r"cada\s+uno\s+de\s+ellos|todos\s+ellos)\b",  # i18n-allow: input vocab
    re.IGNORECASE,
)


def references_recent_fleet(user_text: str) -> bool:
    """Whether a follow-up points back to the panes opened in the prior turn."""
    return bool(_RECENT_FLEET_RE.search(user_text or ""))


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
    r"\b(?:alle[nr]?|s[aä]mtliche[nr]?|jede[nr]?|all|every|each|todos?|todas?)\b",  # i18n-allow: input vocab
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
    for pane in _PANE_NOUN_RE.finditer(clause):
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
                if (
                    0 <= ahead <= _CLOSE_VERB_MAX_GAP
                    or 0 <= behind <= _CLOSE_VERB_MAX_GAP
                ):
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
    if (
        len(text) < 6
        or _QUESTION_OPENER_RE.search(text)
        or _CLOSE_STATE_QUESTION_RE.search(text)
    ):
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
    span = _spawn_span(text)
    agent_fleet = span is None and _is_agent_fleet(text, names)
    if span is None and not agent_fleet:
        return None
    if _QUESTION_OPENER_RE.search(text) is not None:
        return None
    addressed_text = span.parse_text if span is not None else text
    addressed_before_spawn = (
        span is not None
        and span.start > 0
        and detect(text[: span.start], names=names) is not None
    )
    if addressed_before_spawn or detect(addressed_text, names=names) is not None:
        return None

    parse_text = addressed_text

    groups = _spoken_groups(parse_text)
    if groups:
        return SpawnTerminalsRequest(
            count=sum(g.count for g in groups),
            agent=groups[0].agent,
            utterance=text,
            groups=groups,
        )

    agent_match = _AGENT_RE.search(parse_text)
    agent: str | None = None
    if agent_match is not None:
        agent = _canonical_agent(agent_match.group("agent"))
    count = _spoken_count(parse_text)
    return SpawnTerminalsRequest(
        count=count,
        agent=agent,
        utterance=text,
        groups=(SpawnGroup(count=count, agent=agent),),
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
    if detect_close_fleet(text) is not None:
        return True
    if detect_spawn(text, names=names) is not None:
        return True
    if names_spawn_vehicle(text):
        return False
    return detect(text, names=names) is not None


__all__ = [
    "KIND_PROMPT",
    "KIND_REPORT",
    "CloseTerminalsRequest",
    "SpawnGroup",
    "SpawnTerminalsRequest",
    "TerminalIntent",
    "detect",
    "detect_all",
    "detect_close_fleet",
    "detect_spawn",
    "expects_several",
    "owns_turn",
    "references_recent_fleet",
    "spawn_instruction",
    "spawn_includes_task",
    "wants_split",
]
