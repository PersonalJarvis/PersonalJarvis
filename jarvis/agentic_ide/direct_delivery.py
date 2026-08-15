"""Decide whether an order already IS a prompt, or still needs one written.

The composer's premise is that dictated speech is not a prompt: "kannst du mal
schnell, also ähm, das Wake-Ding angucken ob da was kaputt ist" reaches a coding
agent as a shrug, and turning it into a briefed task with `@file` references is
what makes the Agentic IDE worth having. That premise is right, and this module
does not weaken it.

What it does is notice that the premise does not hold for EVERY sentence. Some
orders arrive already workable — "T4, run pytest on tests/unit/agentic_ide",
"rename ``_fuse_ranked`` to ``_merge_ranked``", "commit and push". There is
nothing for a writer to add to those: the verb says what to do, the object says
what to do it to, and a model asked to improve them can only pad. Yet they took
the same path as the vague ones, and the path costs a measured 19-25 s of
quality-tier model time before a single character reaches the pane.

**The measurement that motivated this** (three desktop logs, 772 real
``claude-cli`` turns, 2026-08-11 → 2026-08-13). A print-mode turn that produces
almost nothing — under 300 characters — still takes a MEDIAN of 8.2 s: process
start, sign-in, model warm-up, first token. A real brief adds its own length on
top at roughly 200-250 characters per second, so an ordinary 2600-character
brief measures 19-21 s. End to end, from the moment the turn was dispatched to
the moment the text sat in the pane, 58 real deliveries measured 21-48 s with a
median near 29 s. The deterministic renderer that already ships
(``prompt_blueprint.render_fallback``, fed by the same file index) produces its
prompt in 15-172 ms.

So the question this module answers is narrow and worth answering: *did the user
already say what to do, in words a coding agent can act on?* When the answer is
provably yes, the order goes out as it stands. When it is anything short of
provably yes — which is most of the time, by construction — nothing changes and
the brief gets written exactly as before.

## Why the bar is set where it is

The maintainer decided on 2026-07-25 that an accurate prompt beats a quick
handoff, and that decision is not being revisited here. It is being made
per-turn instead of once for all turns, and the tie-breaks all fall the same
way: an instruction admitted to the fast lane by mistake costs the agent a
rougher prompt, while one held back by mistake costs only the seconds it was
always going to cost. The first mistake is the expensive one, so admission
requires positive evidence and every doubt keeps the brief.

Concretely, an order is admitted only when it carries BOTH halves of an
executable instruction:

* an **executional verb** — ``run``, ``rename``, ``delete``, ``commit`` — a verb
  whose object, once named, leaves nothing to work out; and
* a **concrete target** — a path, a filename, a backticked literal, a known
  command, or an identifier written the way code is written.

or, for the handful of operations that genuinely take no object, a
**self-sufficient operation** in a short sentence: "commit and push", "run the
tests", "continue".

## What is deliberately NOT an executional verb

``look``, ``check``, ``see``, ``investigate``, ``debug``, ``review``, ``fix``,
``improve``, ``refactor``, ``clean up`` — and their German and Spanish
equivalents. Every one of them describes a goal whose ROUTE is the open
question, and the route is what the brief supplies by reading the code first.
"Fix the wake word thing" names no defect, no file and no acceptance criterion;
the composer's whole value is that it turns exactly that into a task with
``jarvis/plugins/wake/vosk_kws_provider.py`` attached and a ``## Done when``
that quotes the repository's own test command. Admitting ``fix`` would have
handed the agent the shrug.

This split is the one judgement in the module, and it is the reason the fast
lane cannot quietly grow: a verb joins the executional list only if naming its
object finishes the thought.

## Matching is regex-only, on purpose

No model call, no IO, no config read per turn (AP-9 / AP-11). This runs inside a
voice turn, before the composer has spent anything, and a decision that cost a
provider call would defeat what it exists to save. The German and Spanish stems
are speech-recognition INPUT VOCABULARY — the words a person actually says when
handing work to an agent — and are matching data rather than prose (CLAUDE.md
§1, closed-list item 3); each such line carries an inline marker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The most words an order may carry and still be taken at face value. A spoken
#: instruction that runs longer than this is not a command any more, it is a
#: description — and a description is what the brief exists to structure. Set
#: generously: the admission rules below are the real gate, and this only stops
#: a rambling sentence that happens to contain a verb and a filename from
#: passing on those two tokens alone.
MAX_DIRECT_WORDS = 40

#: The most words a SELF-SUFFICIENT operation may carry. "Commit and push" is an
#: order; "commit and push, but first have a look at whether the tests still
#: make sense after yesterday" only starts like one. Tight, because this rule
#: admits without requiring any target at all.
MAX_OPERATION_WORDS = 12


@dataclass(frozen=True, slots=True)
class Verdict:
    """Whether this order goes out as it stands, and why."""

    direct: bool
    """True when the instruction is already workable and needs no brief."""

    reason: str
    """One short English clause, for the progress line and the log. Says what
    was found on the fast path, or what held the order back on the slow one."""


# --------------------------------------------------------------- vocabulary

# Verbs whose object, once named, leaves nothing to work out. See the module
# docstring for why the exploratory verbs are absent and must stay absent.
_EXECUTIONAL_EN = (
    r"run|execute|rerun|re-run|rename|move|delete|remove|drop|add|append"
    r"|create|write|replace|swap|update|upgrade|bump|install|uninstall"
    r"|revert|undo|merge|rebase|cherry-pick|apply|extract|split|convert"
    r"|migrate|port|open|read|print|show|list|generate|set|enable|disable"
    r"|comment\s+out|uncomment|export|import|copy|duplicate|stage|unstage"
)
# German separable verbs are matched on the STEM alone. "Führ die Tests in
# tests/unit aus" and "benenne _fuse_ranked in _merge_ranked um" put the whole
# object between the two halves, so a pattern that expects the particle right
# after the verb ("führ aus") matches none of the sentences people actually
# speak. The stem on its own is loose enough to catch a noun ("Führung"), which
# is why the rule below still demands a concrete target beside it.
_EXECUTIONAL_DE = (
    r"|f[uü]hr\w*|starte?|benenn\w*"  # i18n-allow: input vocab
    r"|verschieb\w*|l[oö]sch\w*|entfern\w*"  # i18n-allow: input vocab
    r"|f[uü]g\w*|erstell\w*|schreib\w*"  # i18n-allow: input vocab
    r"|ersetz\w*|aktualisier\w*|installier\w*"  # i18n-allow: input vocab
    r"|deinstallier\w*|[oö]ffne?|lies|zeig\w*|setz\w*"  # i18n-allow: input vocab
    r"|aktivier\w*|deaktivier\w*|kopier\w*|exportier\w*"  # i18n-allow: input vocab
    r"|kommentier\w*"  # i18n-allow: input vocab
)
_EXECUTIONAL_ES = (
    r"|ejecuta\w*|renombra\w*|mueve|borra\w*|elimina\w*"  # i18n-allow: input vocab
    # "crear" is enumerated instead of stemmed: this regex runs language-BLIND,
    # and the open stem ``crea\w*`` swallowed the ENGLISH content words
    # "creative"/"creation" — live 2026-08-15 09:15, "he should do it creative"
    # was read as an executional order and T7 got the raw transcript.
    r"|a[ñn]ade?\w*|agrega\w*|crea(?:r|n|ndo|d[oa]s?|mos)?"  # i18n-allow: input vocab
    r"|escribe?|reemplaza\w*"  # i18n-allow: input vocab
    r"|actualiza\w*|instala\w*|abre|lee|muestra|activa|desactiva"  # i18n-allow: input vocab
)

# Operations that are complete without an object at all — and that is the whole
# subtlety. "Build" is one of these only while nothing follows it: "build and
# lint" is an order, "build a retry into the uploader" is the verb in a
# different sense entirely, and admitting the second on the strength of the
# first would hand an agent a feature request stripped of its brief. (Caught by
# ``test_composer_progress.py`` on the first run of this rule, which is exactly
# the false positive the module docstring calls the expensive one.)
#
# So an operation admits only when it accounts for the WHOLE instruction — the
# verbs plus connecting words and nothing else. Anything with an object falls
# through to the verb-and-target rule below, where ``build`` is deliberately
# absent from the executional list.
_OPERATION_EN = (
    r"commit|push|pull|continue|carry\s+on|keep\s+going|proceed|go\s+ahead"
    r"|stop|halt|abort|cancel|build|compile|lint|format|deploy|restart"
    r"|retry|sync|rebase|run\s+the\s+tests?|run\s+tests?"
)
_OPERATION_DE = (
    r"|committe?\w*|pushe?\w*|mach\w*\s+weiter|weitermachen"  # i18n-allow: input vocab
    r"|weiter|stopp\w*|h[oö]r\w*\s+auf|brich\w*\s+ab"  # i18n-allow: input vocab
    r"|bau\w*|kompilier\w*|formatier\w*|starte?\s+neu"  # i18n-allow: input vocab
    r"|f[uü]hr\w*\s+die\s+tests?\s+aus|lass\w*\s+die\s+tests?\s+laufen"  # i18n-allow: input vocab
)
_OPERATION_ES = (
    r"|haz\s+commit|sube|contin[uú]a|sigue|para|detente"  # i18n-allow: input vocab
    r"|compila|formatea|despliega|reinicia"  # i18n-allow: input vocab
)

# Verbs that literally MEAN "write this pane its prompt". A user who says
# "prompt the terminal to ..." has ordered the handover itself: they could have
# typed their sentence into the pane themselves, and asking Jarvis to prompt it
# is asking for the brief the composer writes (maintainer mandate 2026-08-15,
# after T7 received the raw transcript of exactly such an order). The veto has
# to read the whole UTTERANCE, not the extracted instruction — the spawn and
# addressing parsers strip this very verb out as part of the addressing, which
# is how "prompt it to do a deep dive ..." reached `decide` as "do a deep
# dive ..." and looked like a finished command.
#
# Same stems as ``intent._BRIEFING_VERB_RE`` (kept in step by
# ``test_direct_delivery.py``): none of these has a reading that is not a
# handover, which is what makes the veto safe. "T4, run pytest" carries no such
# verb and keeps its fast lane.
_HANDOVER_RE = re.compile(
    r"\b(?:prompt\w*|anprompt\w*|instruct\w*|instruy\w*|anweis\w*|"  # i18n-allow: input vocab
    r"beauftrag\w*|briefing|briefe\w*|assign\w*|encarga\w*)\b",  # i18n-allow: input vocab
    re.IGNORECASE,
)

# Pointers into something the agent will never see. The positive rules below
# already refuse an order with no concrete target, so this list only has to
# catch the sentences that DO name a target and still lean on the conversation
# for what to do with it ("apply the second option to config.py"). Kept to
# unambiguous multi-word phrases: a bare German "das" is an article far more
# often than a pointer, and vetoing on it would close the fast lane for every
# German sentence rather than for the ones that actually point outward.
_BACK_REFERENCE = re.compile(
    r"\b(?:"
    r"points?\s+(?:one|two|three|four|\d)"
    r"|the\s+(?:first|second|third|last|previous|other)\s+(?:one|option|approach|point|suggestion)"
    r"|what\s+you\s+(?:just\s+)?(?:said|suggested|wrote|proposed)"
    r"|as\s+(?:above|discussed|mentioned)|same\s+as\s+(?:before|above)"
    r"|punkt\s+(?:eins|zwei|drei|vier|\d)"  # i18n-allow: input vocab
    r"|die\s+(?:erste|zweite|dritte|letzte)\s+"  # i18n-allow: input vocab
    r"(?:option|variante|m[oö]glichkeit)"  # i18n-allow: input vocab
    r"|was\s+du\s+(?:gerade\s+)?"  # i18n-allow: input vocab
    r"(?:gesagt|vorgeschlagen|geschrieben)\s+hast"  # i18n-allow: input vocab
    r"|wie\s+(?:oben|vorhin|besprochen)|das\s+gleiche\s+wie"  # i18n-allow: input vocab
    r"|el\s+(?:primer|segundo|[uú]ltimo)\s+(?:punto|opci[oó]n)"  # i18n-allow: input vocab
    r"|lo\s+que\s+(?:dijiste|sugeriste)|como\s+(?:arriba|antes)"  # i18n-allow: input vocab
    r")\b",
    re.IGNORECASE,
)

# A target concrete enough that the agent can start on it without searching.
_PATHISH = re.compile(r"[\w.-]+/[\w./*-]+|\b[\w-]+\.(?:"
                      r"py|pyi|ts|tsx|js|jsx|mjs|cjs|md|toml|json|ya?ml|txt|cfg|ini"
                      r"|rs|go|java|kt|rb|php|c|h|cpp|hpp|cs|swift|sh|bash|bat|ps1"
                      r"|css|scss|html|sql|lock|env"
                      r")\b")
# A quote only OPENS where a quote can open. Spoken transcripts are full of
# apostrophes glued to words ("Java's", "doesn't"), and the naive '...' pattern
# read everything between two contractions as one quoted literal — live
# 2026-08-15 09:15, "Java's marketplace ... it should't" produced a 140-character
# "target" that admitted a pure goal description to the fast lane.
_LITERAL = re.compile(r"`[^`]+`|\"[^\"]{2,}\"|(?<!\w)'[^']{2,}'(?!\w)")
# Written the way code is written, not the way speech is: an underscore, a call
# suffix, or a dotted/CamelCase name. A single capitalised word is deliberately
# not enough — every sentence starts with one.
_IDENTIFIER = re.compile(r"\b\w+_\w+\b|\b\w+\(\)|\b\w+\.\w+\b|\b[a-z]+[A-Z]\w*\b")
_COMMAND = re.compile(
    r"\b(?:pytest|npm|npx|pnpm|yarn|pip|uv|git|ruff|mypy|black|eslint|prettier"
    r"|cargo|make|docker|kubectl|node|python3?|poetry|tox|gradle|mvn|dotnet"
    r"|terraform|ansible|jarvis)\b",
    re.IGNORECASE,
)


# Words that may stand between two operations without turning the sentence into
# something else: conjunctions, an article, a bare pronoun, a politeness word.
# Anything outside this set is an OBJECT, and an object means the sentence is no
# longer a plain operation.
_CONNECTOR = (
    r"and|then|also|please|now|again|the|it|all|everything|once|more|time"
    r"|und|dann|auch|bitte|jetzt|nochmal|nochmals|alles|das|es|mal"  # i18n-allow: input vocab
    r"|y|luego|tambi[eé]n|ahora|otra|vez|todo|por|favor"  # i18n-allow: input vocab
)

#: A leading vocative the caller did not strip ("T4, commit and push"). The
#: voice path hands over an instruction with the call-sign already removed, but
#: the REST prompt bar and the CLI pass whatever the user typed.
_VOCATIVE = re.compile(r"^\s*[\w-]{1,20}\s*[,:]\s*")


def _compile(*parts: str) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "".join(parts) + r")\b", re.IGNORECASE)


_EXECUTIONAL = _compile(_EXECUTIONAL_EN, _EXECUTIONAL_DE, _EXECUTIONAL_ES)
_OPERATION = _compile(_OPERATION_EN, _OPERATION_DE, _OPERATION_ES)

#: The whole instruction, made only of operations and connectors.
_OPERATION_ONLY = re.compile(
    r"^(?:(?:"
    + _OPERATION_EN
    + _OPERATION_DE
    + _OPERATION_ES
    + r"|"
    + _CONNECTOR
    + r")(?:\s+|\s*[,&]\s*|$))+$",
    re.IGNORECASE,
)


def has_concrete_target(instruction: str) -> bool:
    """True when the order names something the agent can open or run.

    Public because it is the half of the rule most likely to need widening
    later (a new language's file extensions, a new build tool), and a caller
    testing it directly is how that stays honest.
    """
    text = instruction or ""
    return bool(
        _PATHISH.search(text)
        or _LITERAL.search(text)
        or _COMMAND.search(text)
        or _IDENTIFIER.search(text)
    )


def named_paths(instruction: str) -> list[str]:
    """The repo-relative paths the order names ITSELF, in order of appearance.

    The `@file` references on a brief exist to save the agent its opening round
    of blind searching. An order admitted to the fast lane has already named
    its target, so that saving is zero — and the index's other four suggestions
    stop being help and start being noise: "run pytest on tests/unit" came back
    with five arbitrary test files from that directory under a heading reading
    "Start with these files", which is a worse instruction than the sentence on
    its own.

    So the caller attaches what the user pointed at and nothing else. An order
    that names no path at all (``rename _fuse_ranked to _merge_ranked``) gets an
    empty list here, and the caller may then fall back to the index — that is
    the case where a suggestion is genuinely the agent's shortcut rather than a
    distraction.

    Paths are returned as written, minus trailing sentence punctuation and with
    backslashes normalised. Existence is NOT checked: only the caller knows the
    workspace root, and a dead reference must be dropped rather than repaired.
    """
    seen: list[str] = []
    for match in _PATHISH.finditer(instruction or ""):
        rel = match.group(0).replace("\\", "/").rstrip(".,;:!?")
        if rel and rel not in seen:
            seen.append(rel)
    return seen


def decide(
    instruction: str,
    *,
    has_attachments: bool = False,
    utterance: str = "",
) -> Verdict:
    """Whether ``instruction`` can be sent as it stands.

    ``has_attachments`` vetoes on its own. A dropped screenshot arrives with a
    sentence that points AT it ("fix the spacing here"), and the described image
    only becomes usable once something folds the description into the task — so
    the one case where the user gave the most context is the one where the
    sentence alone says the least.

    ``utterance`` is the user's whole spoken sentence, BEFORE the addressing was
    stripped out of it. It exists for one veto: a briefing verb anywhere in it
    ("prompt the terminal to ...") is the user ordering a written prompt, and
    that order outranks every admission rule below — the parsers remove exactly
    that verb while extracting ``instruction``, so the instruction alone cannot
    carry the evidence.

    Never raises and never needs IO: the answer is a handful of regex passes
    over one sentence, which is what makes it safe to ask on the voice hot path
    before any provider has been spent (AP-9).
    """
    text = " ".join((instruction or "").split())
    if not text:
        return Verdict(False, "there is nothing to send")
    if has_attachments:
        return Verdict(False, "a dropped file needs folding into the task")
    if _HANDOVER_RE.search(utterance or "") or _HANDOVER_RE.search(text):
        return Verdict(False, "the user asked for a prompt to be written")
    if _BACK_REFERENCE.search(text):
        return Verdict(False, "it points back at the conversation")

    words = text.split()
    if len(words) > MAX_DIRECT_WORDS:
        return Verdict(False, "it describes the work rather than naming it")

    # A plain operation has to BE the sentence, not merely start it — see the
    # comment above ``_OPERATION_EN``. The trailing punctuation and a leading
    # "T4," go first so the whole-string match is against the order itself.
    bare = _VOCATIVE.sub("", text).strip(" .!?").strip()
    operation = _OPERATION.search(bare)
    if (
        operation is not None
        and len(bare.split()) <= MAX_OPERATION_WORDS
        and _OPERATION_ONLY.match(bare) is not None
    ):
        return Verdict(True, f"it is a plain '{operation.group(0).lower()}' order")

    executional = _EXECUTIONAL.search(text)
    if executional is None:
        return Verdict(False, "it names a goal rather than an operation")
    if not has_concrete_target(text):
        return Verdict(False, "it names no file, command or symbol to act on")
    return Verdict(True, f"it says '{executional.group(0).lower()}' and what to act on")


__all__ = [
    "MAX_DIRECT_WORDS",
    "MAX_OPERATION_WORDS",
    "Verdict",
    "decide",
    "has_concrete_target",
    "named_paths",
]
