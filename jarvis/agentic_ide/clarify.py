"""Ask which terminal was meant, instead of guessing or going silent.

A call-sign arrives through speech recognition, and the resolver has to draw a
line somewhere: above it a word IS a pane's name, below it the word is ordinary
speech. Both sides of that line have now failed in production, on the same day,
in opposite directions:

* **Silent miss** (2026-07-27 16:18) — a pane called "Ellis" came back as
  "Ilies", scoring 0.667 against a 0.72 floor. The addressed-terminal path stood
  down without a word, no prompt ever reached the pane, and the live model —
  which never learns any of this — told the user an agent was working on it.
* **Silent false hit** — ordinary words of the spoken language reach the pool
  from below: "allen" scores 0.750 against "Alex", i.e. ABOVE the same floor.
  A sentence about the outside world ("what is Elon Musk up to?") must never
  become an instruction typed into somebody's coding agent.

No threshold fixes both, because the two failures overlap on the score axis —
the maintainer's directive (2026-07-27) is therefore to decide by CONTEXT and,
when context says "this is about the panes" but the name is uncertain, to ASK:

    "Did you mean Ellis?"

That is what this module decides. It answers one question — "is this turn
addressing the workspace, and if so, which pane did the user probably mean?" —
and, like ``intent``, it is a detector, not a policy: the caller decides what to
do with the answer.

The asymmetry that shapes every rule below: a needless question costs the user
one word ("yes"), while a wrong guess types a stranger's task into an agent that
then works for minutes on it. So the gate is deliberately hard to pass, and
whatever it is unsure about becomes a question rather than an action.

Cost: pure regex over the utterance, no IO and no LLM (AP-9 / AP-11), so it is
safe on the voice hot path.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .names import CALL_SIGN_WORD_RE, canonical_positions, near_miss, resolve

# How long a pending question stays answerable. Voice turns arrive within
# seconds; two minutes covers a slow "uh... yes, Ellis" without letting a
# forgotten question deliver work much later in the session. Same bound as the
# delegation offer window, for the same reason.
_WINDOW_TTL_S = 120.0

# A short answer is an ANSWER to the question ("yes", "Ellis", "the second
# one"); a long sentence is the user moving on and must never deliver the old
# task. Same bound the spawn gate uses for its confirmation.
_ANSWER_MAX_WORDS = 6


# --------------------------------------------------------------------------- #
# Is this turn about the workspace at all?                                     #
# --------------------------------------------------------------------------- #
# The gate that separates "prompt Max" from "what is Elon Musk doing". Evidence
# has to be POSITIVE and explicit: the default answer is no, because that is the
# answer that cannot type a stranger's sentence into a running coding agent.

#: Words that only ever mean the coding workspace. Naming a pane, or the act of
#: handing one work, is the strongest evidence there is.
_WORKSPACE_NOUN_RE = re.compile(
    r"\b(?:terminals?|terminales|panes?|workspaces?|"
    r"arbeitsbereich\w*|instanz\w*|instances?|"  # i18n-allow: input vocab
    r"prompte?\w*|prompting|anprompt\w*|"  # i18n-allow: input vocab
    r"beauftrag\w*|briefe?\w*|briefing)\b",  # i18n-allow: input vocab
    re.IGNORECASE,
)

#: The work coding agents actually do. A sentence carrying this vocabulary is
#: about the user's own codebase, which is what the panes are for — as opposed
#: to a question about the world, which never contains it.
_CODING_WORK_RE = re.compile(
    r"\b(?:"
    # investigation / analysis
    r"deep[\s-]?dive|root[\s-]?cause|debug\w*|analysier\w*|analyz\w*|analis\w*|"
    r"untersuch\w*|investigate\w*|diagnos\w*|"  # i18n-allow: input vocab
    # change
    # i18n-allow: input vocab
    r"fix\w*|behebe?\w*|repariere?\w*|refactor\w*|implementier\w*|implement\w*|"
    r"commit\w*|merge\w*|rebase\w*|deploy\w*|"
    # artifacts
    r"bugs?|fehler\w*|tests?|teste\w*|repo\w*|branch\w*|"  # i18n-allow: input vocab
    r"code\w*|datei\w*|files?|funktion\w*|functions?|"  # i18n-allow: input vocab
    r"pull[\s-]?request|pr\b|"
    # Spanish work vocabulary, kept level with the other two locales
    r"arregl\w*|revis\w*|prueba\w*|archivo\w*|rama\w*"
    r")\b",
    re.IGNORECASE,
)

#: Handing work to somebody — "sag X …", "lass X …", "tell X to …". Reused from
#: the same vocabulary ``intent`` addresses panes with, kept here as a flat verb
#: scan because at this point the NAME is exactly what we are unsure about, so a
#: name-anchored template cannot be built yet.
_HANDOVER_VERB_RE = re.compile(
    # Stems, not fixed forms: the maintainer's own phrasing is "… machen
    # LASSEN", and a `lass\b` alternative silently misses every separable and
    # infinitive form German actually hands work over in.
    r"\b(?:sag\w*|schick\w*|gib|geb\w*|frag\w*|lass\w*|l[aä]sst|"  # i18n-allow: input vocab
    r"beauftrag\w*|[uü]bergib|[uü]bergeb\w*|weiterleit\w*|"  # i18n-allow: input vocab
    r"tell|send|give|ask|hand|forward|assign|let|have|"
    r"dile|d[ií]gale|env[ií]a|manda|preg[uú]nta|pasa|asigna|encarga)\b",  # i18n-allow: input vocab
    re.IGNORECASE,
)


#: "X SOLL das fixen" — an order aimed at a third party, carried by the modal
#: rather than by a handover verb. This is the other half of how work is
#: assigned in speech, and ``intent`` matches it name-anchored; here the name is
#: precisely what is uncertain, so the modal is scanned on its own and paired
#: with coding work to keep it honest.
_DIRECTIVE_MODAL_RE = re.compile(
    r"\b(?:soll|sollen|sollte|sollten|m[uü]ssen|muss|"  # i18n-allow: input vocab
    r"should|must|shall|"
    r"deber[ií]an?|debe|deben)\b",  # i18n-allow: input vocab
    re.IGNORECASE,
)


#: "What has X done?" / "How far is X?" — asking a NAMED worker for a status
#: report. The shape carries no coding vocabulary at all, which is why it
#: needed its own rule: "was hat Dana gemacht" names no repo, no test, no bug
#: and no branch, so the coding-work pairings below never saw it.
#:
#: Each locale gets both word orders, and the German alternation carries the
#: optional perfect prefix ("gemacht", "getan", "gearbeitet") — the tense
#: spoken German actually uses to ask about work that has already happened, and
#: the exact form the live 2026-07-27 turn was lost on.
_REPORT_QUESTION_RE = re.compile(
    # A progress opener is a status question on its own — "wie weit ist Ellis?"
    # carries no verb of doing at all, and nothing else is ever asked with it.
    r"\b(?:wie\s+weit|wie\s+l[aä]uft|wie\s+steht|how\s+far|"  # i18n-allow: input vocab
    r"c[oó]mo\s+va)\b|"
    r"\bwas\b[^.!?]{0,40}?"  # i18n-allow: input vocab
    r"\b(?:ge)?(?:macht|machen|tut|tun|tan|treibt|treiben|arbeitet|"  # i18n-allow: input vocab
    r"arbeiten|schafft|geschafft|erledigt|rausgefunden|"  # i18n-allow: input vocab
    r"herausgefunden|gebaut|geschrieben|ge[aä]ndert)\b|"  # i18n-allow: input vocab
    r"\b(?:ist|sind)\b[^.!?]{0,30}?\b(?:fertig|durch|soweit)\b|"  # i18n-allow: input vocab
    r"\b(?:what|how)\b[^.!?]{0,40}?"
    r"\b(?:doing|done|do|did|up\s+to|going|built|found|changed|"
    r"working\s+on|progress\w*)\b|"
    r"\b(?:is|are)\b[^.!?]{0,30}?\b(?:done|finished|ready|stuck)\b|"
    r"\b(?:qu[eé]|c[oó]mo)\b[^.!?]{0,40}?"
    r"\b(?:hacen?|haciendo|hecho|hizo|hicieron|va|van|"
    r"encontr\w*|cambi\w*)\b|"
    r"\b(?:est[aá]|est[aá]n)\b[^.!?]{0,30}?\b(?:listo\w*|terminad\w*)\b|"
    r"\b(?:status|fortschritt|estado|progreso)\b",  # i18n-allow: input vocab
    re.IGNORECASE,
)


def addresses_workspace(text: str) -> bool:
    """Whether this turn is plausibly about the open coding workspace.

    Four independent kinds of evidence, any one of which is enough:

    1. the utterance NAMES the workspace or the act of prompting a pane
       ("prompt Max", "beauftrage Max") — unambiguous on its own;
    2. it hands work over AND that work is coding work ("tell Max to fix the
       tests");
    3. it gives a third-party order AND that order is coding work ("Ellis soll
       einen Deep Dive machen") — the shape that carries no handover verb;
    4. it asks a named worker for a STATUS REPORT ("was hat Ellis gemacht?").

    In 2 and 3 the pairing is what does the work. A handover verb alone
    deliberately does NOT qualify: "tell me what Elon Musk is doing" is a
    handover verb plus a name and nothing else, and that sentence must never
    reach a coding agent.

    4 stands alone without a coding-work pairing, and it may, because of what
    it can and cannot cause. This module only ever produces a QUESTION — "did
    you mean Ellis?" — and only for a turn where no pane was certainly named
    and exactly one word came close. A status question about somebody the user
    really did mean as a person therefore costs one "no"; without the rule, a
    call-sign that speech recognition garbled inside the single most common
    workspace question there is went silently nowhere, which is the failure
    this whole module exists to end.
    """
    body = str(text or "")
    if _WORKSPACE_NOUN_RE.search(body):
        return True
    # A status question about a FULL personal name is about that person: "sag
    # mir was Elon Musk gerade macht" has the identical shape to "sag mir was
    # Ellis gerade macht" and the opposite meaning, and the surname is the only
    # thing that tells them apart. Same rule the collective address uses, so
    # the two cannot drift into disagreeing about one sentence.
    if _REPORT_QUESTION_RE.search(body) and not is_outside_world_talk(body):
        return True
    if not _CODING_WORK_RE.search(body):
        return False
    return bool(
        _HANDOVER_VERB_RE.search(body) or _DIRECTIVE_MODAL_RE.search(body)
    )


# --------------------------------------------------------------------------- #
# Is this word a call-sign at all?                                             #
# --------------------------------------------------------------------------- #

#: The unit the resolver scores — letters plus an optional digit tail, so a
#: positional call-sign ("T1") arrives whole. Shared with ``intent`` through
#: ``names`` so the two cannot disagree about where a name starts and ends.
_WORD_RE = CALL_SIGN_WORD_RE

_SENTENCE_START_RE = re.compile(r"(?:^|[.!?:;]\s*|\n\s*)$")

#: Function words that are never a call-sign, however close they score. This is
#: matching *input vocabulary*, not prose.
#:
#: Needed because similarity alone cannot tell them apart from a garbled name —
#: measured against the shipping pool, "kannst" reaches "Casey" at 0.600,
#: "macht" reaches "Max" at 0.571, and "allen" reaches "Alex" at 0.750, which is
#: ABOVE the acting threshold. That last one is the maintainer's own
#: counter-example (2026-07-27): a sentence about the outside world must never
#: become a prompt typed into every open pane.
#:
#: Deliberately only the closed class (pronouns, articles, modals, question
#: words, common auxiliaries). Content words are left out: they are what a
#: person's name competes with legitimately, and the capitalization signal plus
#: the workspace gate already cover them.
#: Kept as one tuple per language rather than one merged literal: the locales
#: overlap ("no", "me", "es", "a" are each two languages' function words), and a
#: single set literal would both hide that and read as a mistake.
_STOPWORDS_DE: tuple[str, ...] = (  # i18n-allow: input vocabulary
    "alle", "allen", "aller", "alles", "als", "am", "an", "auch", "auf",
    "aus", "bei", "bin", "bis", "bist", "bitte", "da", "dann", "das",
    "dass", "dein", "deine", "dem", "den", "denn", "der", "des", "die",
    "dir", "doch", "du", "ein", "eine", "einen", "einer", "eines", "er",
    "es", "etwas", "euch", "für", "gerade", "gib", "hab", "habe", "haben",
    "hast", "hat", "hier", "ich", "ihm", "ihn", "ihr", "im", "in", "ist",
    "ja", "kann", "kannst", "können", "könnte", "lass", "lassen", "mach",
    "machen", "macht", "mal", "man", "mein", "meine", "mir", "mit", "muss",
    "müssen", "nach", "nein", "nicht", "noch", "nur", "ob", "oder", "sag",
    "sage", "schon", "sein", "seine", "sich", "sie", "sind", "so", "soll",
    "sollen", "über", "um", "und", "uns", "unser", "von", "vor", "war",
    "was", "wenn", "wer", "werde", "werden", "wie", "wieso", "will",
    "wir", "wird", "wo", "zu", "zum", "zur",
)
_STOPWORDS_EN: tuple[str, ...] = (
    "a", "all", "am", "an", "and", "are", "as", "ask", "at", "be", "been",
    "but", "by", "can", "could", "did", "do", "does", "for", "from", "get",
    "give", "had", "has", "have", "he", "her", "him", "his", "how", "i",
    "if", "in", "into", "is", "it", "its", "just", "let", "make", "makes",
    "me", "my", "no", "not", "now", "of", "on", "one", "or", "our", "out",
    "please", "put", "say", "she", "should", "so", "some", "tell", "that",
    "the", "their", "them", "then", "there", "these", "they", "this",
    "to", "up", "us", "was", "we", "were", "what", "when", "where",
    "which", "who", "why", "will", "with", "would", "yes", "you", "your",
)
_STOPWORDS_ES: tuple[str, ...] = (  # i18n-allow: input vocabulary
    "a", "al", "algo", "aqui", "así", "como", "con", "cual", "cuando",
    "de", "del", "dile", "donde", "el", "ella", "ellos", "en", "era",
    "es", "esa", "ese", "eso", "esta", "este", "esto", "está", "están",
    "haz", "la", "las", "le", "les", "lo", "los", "mas", "me", "mi",
    "no", "nos", "o", "para", "pero", "por", "que", "qué", "quien",
    "se", "ser", "si", "sí", "sobre", "solo", "son", "su", "sus", "te",
    "tu", "un", "una", "uno", "y", "ya", "yo",
)
_STOPWORDS: frozenset[str] = frozenset(
    _STOPWORDS_DE + _STOPWORDS_EN + _STOPWORDS_ES
)


def _capitalization_is_informative(text: str) -> bool:
    """Whether this transcript capitalizes at all, i.e. whether case can rule.

    An all-lowercase transcript is a provider quirk, not evidence that the
    utterance contains no names.
    """
    body = str(text or "")
    return any(ch.isupper() for ch in body)


def _is_proper_name_position(text: str, start: int, end: int) -> bool:
    """Whether the word at ``[start:end)`` reads as a proper name.

    Two filters, in this order, because they fail differently:

    1. **a function word is never a name** — decisive in every transcript,
       including one with no capitals at all;
    2. **otherwise capitalization decides**, when the transcript capitalizes at
       all. Position is deliberately NOT considered: a call-sign opening the
       sentence is the most natural way to address a pane ("Ellis, schau dir
       das an"), and excluding sentence-initial words would blind the check to
       exactly that shape.
    """
    word = text[start:end]
    if word.casefold() in _STOPWORDS:
        return False
    if _is_vocabulary_word(word):
        return False
    if not _capitalization_is_informative(text):
        return True
    return word[:1].isupper()


#: What somebody ASKS FOR about a pane, as a noun: "Alex Status?", "Dana
#: Fortschritt", "Casey progress". German capitalizes every noun, so without
#: this these read as surnames and the pane is disowned as a person out in the
#: world — which is how "Alex Status?", a textbook status question, matched
#: nothing at all.
_STATUS_NOUN_RE = re.compile(
    r"\b(?:status|fortschritt\w*|stand|update\w*|progress|"  # i18n-allow: input vocab
    r"bericht\w*|report\w*|estado|progreso|informe\w*)\b",  # i18n-allow: input vocab
    re.IGNORECASE,
)


def _is_vocabulary_word(word: str) -> bool:
    """Whether the word is one this module already reads as something else.

    A word that names the WORK cannot also name the worker. Without this, an
    all-lowercase transcript — where capitalization cannot rule anything out —
    let "fixen" stand in for the pane "Finn" (0.667) and ask about a sentence
    that names no pane at all.

    Anchored so only a WHOLE word counts: the vocabulary patterns are written
    to find their stems anywhere in a sentence, and reused unanchored they
    would reject any call-sign that merely contains one.
    """
    for pattern in (
        _WORKSPACE_NOUN_RE,
        _CODING_WORK_RE,
        _HANDOVER_VERB_RE,
        _DIRECTIVE_MODAL_RE,
        _STATUS_NOUN_RE,
    ):
        match = pattern.match(word)
        if match is not None and match.end() == len(word):
            return True
    return False


#: A second capitalized word directly after the candidate — "Elon MUSK", "Max
#: MUSTERMANN". Panes carry exactly one given name (see ``names.NAME_POOL``), so
#: a surname is positive evidence that a PERSON is meant, not a terminal. This
#: is the maintainer's own criterion (2026-07-27): "when I only say a first
#: name" is the case that should ask.
_SURNAME_FOLLOWS_RE = re.compile(r"^[\s,]*[A-ZÄÖÜ][^\W\d_]+")
_SURNAME_PRECEDES_RE = re.compile(r"[A-ZÄÖÜ][^\W\d_]+[\s,]*$")


def is_part_of_full_name(text: str, start: int, end: int) -> bool:
    """Whether the candidate is part of a longer proper name ("Elon Musk").

    Public because ``intent`` needs exactly this answer too: a call-sign is a
    single given name, so a first name carrying a surname is a PERSON out in
    the world and never the pane. Two copies of that rule would be two chances
    to disagree about one sentence, which is how "what has Dana Schmidt done"
    ends up read as a question about a terminal.
    """
    if not _capitalization_is_informative(text):
        return False
    following = _SURNAME_FOLLOWS_RE.match(text[end:])
    if following is not None:
        # German capitalizes every noun, so the word after a call-sign is
        # capitalized whether it is a surname or the thing being asked about.
        # A word this module already reads as work, a workspace or a status is
        # therefore never the surname: "Alex Status?" is a question about the
        # pane, and reading "Status" as a family name struck the pane out of
        # its own status question. Same reasoning for "sag Dana Bescheid".
        if not _is_vocabulary_word(following.group(0).strip(" ,")):
            return True
    preceding = text[:start]
    leading = _SURNAME_PRECEDES_RE.search(preceding)
    if leading is None:
        return False
    if _is_vocabulary_word(leading.group(0).strip(" ,")):
        return False
    # A capitalized word in FRONT only makes this a surname when that word is
    # not itself the one opening the sentence. The sentence-start test has to
    # be aimed at THAT word, not at the candidate: aimed at the candidate it
    # read "Ist Blake fertig?" as a person called Ist Blake, because "Ist" is
    # capitalized for grammar and stands at the very beginning — which silently
    # withdrew the pane from every question opening with a verb.
    return not _SENTENCE_START_RE.search(preceding[: leading.start()])


#: Two capitalized words in a row, the second not opening a sentence — a person
#: or organisation out in the world ("Elon Musk", "Barack Obama"). Panes carry a
#: single given name, so this shape never describes one.
_MULTIWORD_NAME_RE = re.compile(
    r"(?<![.!?:;\n])\s[A-ZÄÖÜ][^\W\d_]+\s+[A-ZÄÖÜ][^\W\d_]+"
)


def is_outside_world_talk(text: str) -> bool:
    """Whether this turn is about the world rather than the user's codebase.

    Guards the COLLECTIVE address ("sag allen …"), which reaches every open
    pane at once and is therefore the most expensive thing a misheard word can
    trigger. The maintainer's counter-example (2026-07-27): a question about a
    public figure that speech recognition turns into "sag allen …" must not
    become an instruction typed into every running coding agent.

    Deliberately narrow — it answers yes only for a full personal name with no
    coding work anywhere in the sentence. "Tell everyone to stop" carries no
    such name and stays a perfectly good collective instruction.
    """
    body = str(text or "")
    if not _capitalization_is_informative(body):
        return False
    return bool(
        _MULTIWORD_NAME_RE.search(body) and not _CODING_WORK_RE.search(body)
    )


# --------------------------------------------------------------------------- #
# The detector                                                                 #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class UncertainName:
    """One word that came close to a call-sign, and the panes it might be."""

    spoken: str
    """The word as the transcript spelled it ("Ilies")."""

    candidates: tuple[str, ...]
    """Panes it could mean, best first. Never empty."""


@dataclass(frozen=True, slots=True)
class ClarificationNeeded:
    """Everything one turn addressed that could not simply be acted on.

    A turn addresses a FLEET, not a pane — "Alex und Blaike, macht beide einen
    Deep Dive" is one instruction for two agents — so this carries a list on
    both axes. The single-name shape it started as could not represent that
    turn at all, and the resulting behaviour was the worst of the three
    possible ones: Alex was briefed, Blaike was dropped without a word, and the
    user watched one agent work believing two were (live 2026-07-27 19:07).
    """

    uncertain: tuple[UncertainName, ...]
    """The words that need a question. Never empty."""

    utterance: str
    """The original turn, kept verbatim so the confirmed pane gets the REAL
    task and not a paraphrase assembled from fragments."""

    certain: tuple[str, ...] = ()
    """Panes the SAME turn named beyond doubt. They are not in question and
    must be briefed whatever the answer is; they travel with the question only
    so the caller can tell "one pane is uncertain" from "everything is"."""

    @property
    def spoken(self) -> str:
        """The first uncertain word — the one a single-name question names."""
        return self.uncertain[0].spoken

    @property
    def candidates(self) -> tuple[str, ...]:
        """Panes the first uncertain word could mean."""
        return self.uncertain[0].candidates

    @property
    def offered(self) -> tuple[str, ...]:
        """Every pane any of the uncertain words could mean, in order."""
        seen: list[str] = []
        for item in self.uncertain:
            for name in item.candidates:
                if name not in seen:
                    seen.append(name)
        return tuple(seen)


#: What may stand between two names that are being listed together. Only
#: enumeration punctuation and the supported languages' "and" — anything else
#: means the two words are in different clauses and say nothing about each
#: other. Matching *input vocabulary*, not prose.
_ENUMERATION_GAP_RE = re.compile(
    r"^[\s,;&]*"
    # i18n-allow: input vocabulary — the supported languages' "and"
    r"(?:und|and|y|e|sowie|plus|as\s+well\s+as|together\s+with|junto\s+con)?"
    r"[\s,;&]*$",
    re.IGNORECASE,
)


def _is_listed_beside(text: str, span: tuple[int, int], others: list[tuple[int, int]]) -> bool:
    """Whether the word at ``span`` is enumerated next to one of ``others``.

    "Alex und Blaike" lists two call-signs; "Alex, look at what Blaike wrote"
    does not, and the difference is exactly what sits between them. Used only
    where a word would otherwise be too weak to ask about on its own, so it
    buys reach without widening what a lone stray capital can trigger.
    """
    start, end = span
    for other_start, other_end in others:
        if other_end <= start and _ENUMERATION_GAP_RE.match(text[other_end:start]):
            return True
        if end <= other_start and _ENUMERATION_GAP_RE.match(text[end:other_start]):
            return True
    return False


def detect_clarification(
    user_text: str, *, names: list[str]
) -> ClarificationNeeded | None:
    """What this turn addressed but did not clearly name, or ``None``.

    Every condition below removes one way of being wrong:

    1. **the turn addresses the workspace** — otherwise a question about the
       world could put words into a coding agent;
    2. **the word reads as a proper name** — capitalized, not just first in
       its sentence;
    3. **it is not part of a longer proper name** — "Elon Musk" is a person;
    4. **a word that stands alone in its uncertainty** may only ask when it is
       the turn's ONLY unclear name and no pane was certainly named. Beyond
       that single case the word has to be ENUMERATED beside another name
       ("Alex und Blaike") — that is what separates a garbled member of a list
       from a stray capital in a sentence that already works.

    A certainly-named pane no longer ENDS the search, which is the change that
    matters. It used to: any certain name returned ``None``, so "Alex und
    Blaike" produced a question about nothing, Alex was briefed alone, and the
    second agent the user had just addressed was dropped in silence. The
    certain panes now travel with the answer instead (``certain``) — the caller
    briefs them immediately and asks only about the rest.
    """
    text = str(user_text or "").strip()
    if len(text) < 3 or not names:
        return None
    # The workspace gate reads the ORIGINAL wording, because the pane noun is
    # part of its evidence and the rewrite below consumes it: "prompte Terminal
    # eins" becomes "prompte T1", and asked in that order the gate would have
    # lost the word that proves the turn is about the workspace at all.
    if not addresses_workspace(text):
        return None
    # Everything after it reads the canonical wording, so a pane named by
    # position is as CERTAIN here as one named by its call-sign — otherwise
    # "prompte Terminal eins und Blaike" would report the garbled second name
    # while forgetting that the first one was clearly addressed.
    working = canonical_positions(text, names)

    certain: list[str] = []
    certain_spans: list[tuple[int, int]] = []
    uncertain: list[tuple[tuple[int, int], UncertainName]] = []
    for match in _WORD_RE.finditer(working):
        word = match.group(0)
        span = (match.start(), match.end())
        # Certain means the exact name or the same sound — deliberately the
        # same bar ``near_miss`` uses, so no utterance can fall between the two
        # paths and produce neither an action nor a question.
        sure = resolve(word, names, fuzzy=False)
        if sure is not None:
            if sure not in certain:
                certain.append(sure)
            certain_spans.append(span)
            continue
        candidates = near_miss(word, names)
        if not candidates:
            continue
        if not _is_proper_name_position(working, *span):
            continue
        if is_part_of_full_name(working, *span):
            continue
        uncertain.append(
            (span, UncertainName(spoken=word, candidates=tuple(n for n, _ in candidates)))
        )

    if not uncertain:
        return None

    if len(uncertain) > 1 or certain:
        # More than one unclear word, or one standing beside a certain name:
        # only the ones being LISTED with another name survive. A single stray
        # near miss elsewhere in the sentence is noise, and asking about it
        # would tax every turn that already works.
        neighbours = certain_spans + [span for span, _ in uncertain]
        kept = [
            (span, item)
            for span, item in uncertain
            if _is_listed_beside(working, span, [s for s in neighbours if s != span])
        ]
        if not kept:
            return None
        uncertain = kept

    return ClarificationNeeded(
        uncertain=tuple(item for _, item in uncertain),
        utterance=text,
        certain=tuple(certain),
    )


# --------------------------------------------------------------------------- #
# The answer window                                                            #
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Pending:
    uncertain: tuple[UncertainName, ...]
    utterance: str
    asked_at: float
    question: str = ""

    @property
    def candidates(self) -> tuple[str, ...]:
        """Every pane on offer, in the order they were spoken."""
        seen: list[str] = []
        for item in self.uncertain:
            for name in item.candidates:
                if name not in seen:
                    seen.append(name)
        return tuple(seen)


@dataclass(slots=True)
class ClarificationWindow:
    """A question waiting for its answer.

    Without this the question would be theatre: "did you mean Ellis?" followed
    by "yes" has to actually deliver the ORIGINAL task, or the user has simply
    been made to repeat themselves — which is worse than the silent miss it
    replaces, because it also cost them a turn.
    """

    _pending: _Pending | None = field(default=None)

    def arm(
        self, need: ClarificationNeeded, *, question: str = "", now: float | None = None
    ) -> None:
        self._pending = _Pending(
            uncertain=need.uncertain,
            utterance=need.utterance,
            asked_at=time.monotonic() if now is None else now,
            question=question,
        )

    def disarm(self) -> None:
        self._pending = None

    @property
    def armed(self) -> bool:
        return self._pending is not None

    def resolve_answer(
        self, answer: str, *, language: str = "de", now: float | None = None
    ) -> tuple[tuple[str, ...], str] | None:
        """``(panes, original_utterance)`` for a clear answer, else ``None``.

        A tuple of panes rather than one, because the question can be about a
        fleet: "did you mean Alex and Blake?" is answered by one "yes" and both
        agents have to be briefed by it. Answering it with one pane was how the
        second agent of an addressed pair got lost.

        Consumed exactly once either way — a question that has been answered,
        declined or ignored must never deliver work on a later turn.

        Three shapes count as an answer, in falling directness:

        1. the user NAMES offered panes ("Ellis", "Alex and Blake") — decisive
           even when several were offered;
        2. a short affirmative — it confirms every uncertain word that has only
           ONE candidate, because "yes" cannot choose between two;
        3. anything else: the window closes and the turn proceeds normally.
        """
        pending = self._pending
        if pending is None:
            return None
        current = time.monotonic() if now is None else now
        if (current - pending.asked_at) > _WINDOW_TTL_S:
            self._pending = None
            return None

        text = str(answer or "").strip()
        if not text:
            return None

        offered = list(pending.candidates)
        named = [
            hit
            for hit in (
                resolve(match.group(0), offered, fuzzy=False)
                for match in _WORD_RE.finditer(text)
            )
            if hit is not None
        ]
        if not named:
            # The whole answer as one token, which is how a one-word reply and
            # a two-word call-sign both arrive.
            single = resolve(text, offered)
            if single is not None:
                named = [single]
        if named:
            self._pending = None
            return tuple(dict.fromkeys(named)), pending.utterance

        # Only a SHORT turn is an answer at all; a full sentence is the user
        # moving on, and delivering the old task then would be a surprise. The
        # bound grows with the number of names on offer: confirming a fleet
        # costs more words than confirming one pane.
        if len(text.split()) > _ANSWER_MAX_WORDS + len(pending.uncertain) - 1:
            self._pending = None
            return None

        verdict = _classify(text, language)
        self._pending = None
        if verdict != "confirm":
            return None
        decided = tuple(
            dict.fromkeys(
                item.candidates[0] for item in pending.uncertain if len(item.candidates) == 1
            )
        )
        return (decided, pending.utterance) if decided else None


def classify_short_answer(text: str, language: str = "de") -> str:
    """``"confirm"`` / ``"veto"`` / ``"unknown"`` for a short spoken answer.

    Public because more than one question is asked of the workspace now — the
    pane-name one here, and the coding-CLI one in the spawn path — and both
    have to read a "yes" the same way. A second classifier would drift, and the
    drift would show up as one question honouring an answer the other ignores.
    """
    return _classify(text, language)


def _classify(text: str, language: str) -> str:
    """Yes/no verdict for a short answer, across every supported language.

    The per-turn language tag cannot be trusted (STT mislabels are a known
    class), so the answer is classified under all supported languages and a
    veto keeps its safety priority — exactly what ``spawn_gate`` does.
    """
    try:
        from jarvis.voice.echo_confirmation import classify_response  # noqa: PLC0415
    except Exception:  # noqa: BLE001 — a classifier fault is simply "no answer"
        return "unknown"
    verdicts = {
        classify_response(text, language=lang)
        for lang in {"de", "en", "es", str(language or "de")}
    }
    if "veto" in verdicts:
        return "veto"
    return "confirm" if "confirm" in verdicts else "unknown"


#: ONE conversation per process (desktop app / headless session), so ONE shared
#: window — the same reasoning as ``spawn_gate.OFFER_WINDOW``. Tests reset it
#: via ``disarm()``.
WINDOW = ClarificationWindow()


__all__ = [
    "WINDOW",
    "ClarificationNeeded",
    "ClarificationWindow",
    "UncertainName",
    "addresses_workspace",
    "classify_short_answer",
    "detect_clarification",
    "is_outside_world_talk",
    "is_part_of_full_name",
]
