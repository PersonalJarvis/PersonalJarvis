"""Prompt Mode — a dictation comes out as the prompt a coding agent should get.

What it is
----------
The user holds the dictation key, describes a task in their own words — half
sentences, false starts, several small tasks in one breath — and what lands
in the field is a finished prompt for an AI coding agent: the situation they
described, the goal, the constraints that were stated, and nothing invented.
Plain text, in the language they spoke, in the user's own words wherever
those already carry the meaning. It is the Agentic IDE's prompt doctrine
(:mod:`jarvis.agentic_ide.prompt_blueprint`) aimed at a transcript instead of
a pane.

Two phases, because thinking is the only thing that buys quality
----------------------------------------------------------------
The maintainer's bar is that the writer THINKS for about 5-7 s before it
answers, and that the answer be worth the wait (2026-08-27). A language model
does not think by waiting; it thinks by emitting tokens. v3 asked for the
finished message directly, so a fast model wrote it in 0.4-0.9 s — measured
live — and the quality was the quality of a first draft: concrete detail from
the transcript dropped, the speaker's own wording replaced with a writer's
wording, several dictated tasks folded into one.

So the answer is now two blocks in one call. ``<analysis>`` is a working pass
the model must complete first: count the tasks, inventory every literal the
user said, name the situation, the limits and the wording that has to survive.
``<prompt>`` is the message, written against that inventory. The analysis is
thrown away — it never reaches the user — and its whole purpose is that the
model has read the transcript properly by the time it writes. It also costs
the seconds the bar asks for: an inventory of a real dictation is 300-900
tokens, which is where the 5-7 s comes from, on any provider rather than only
on the ones that expose a thinking budget.

Why it still rides the polish pass's fast chain
-----------------------------------------------
The chain in :mod:`jarvis.dictation.polish_client` is the only lane on this
host that reaches a model at all without a cold start, and Prompt Mode asks
it for the family's STRONGER fast model — the one the translate pass uses
(``gemini-3.7-flash`` on Google, ``gpt-oss-120b`` on Groq and Cerebras,
``gpt-5.4-mini`` on OpenAI). A thinking-grade API model or a subscription CLI
would spend 15-30 s and stop being dictation.

What it keeps from the polish pass and what it does not
-------------------------------------------------------
Kept: the shape. Never raises, never loses text, one status per attempt,
fail-open to the ordinary passes, the fenced user message that keeps a
dictation shaped like an instruction from becoming one
(:func:`jarvis.dictation.polish_prompt.build_polish_user_message`), the
protected-terms block, the breaker.

Not kept: the polish pass's drift guards. They measure how far the words
moved, and here they are allowed to move. Prompt Mode guards the two things
that can still go wrong — the SHAPE of the answer (not empty, not markdown,
not cut off) and its FIDELITY to the transcript (every literal the user
spoke still present, and the message not collapsed to a fraction of what was
said). Then it finishes deterministically: typographic debris a fast model
leaves behind is normalised, and a courtesy sign-off the model tacked on is
cut, because the message ends on its last piece of substance.

Ships OFF. Unlike the formatter it changes WHAT the text says, on purpose,
and sends the words to a cloud model on most installs (the polish pass's
on-device rule still applies: a local recognizer keeps the chain local).

Pure orchestration — the imports that cost anything are inside the function
(AP-26), so ``import jarvis.dictation.prompt_mode`` is free on the boot path.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Sequence
from typing import Any, Final

from jarvis.agentic_ide.prompt_blueprint import (
    FORBIDDEN_SUBJECTS_RULE,
    GOAL_NOT_IMPLEMENTATION_RULE,
    looks_truncated,
)
from jarvis.dictation.polish import PolishOutcome
from jarvis.dictation.polish_prompt import (
    build_polish_user_message,
    build_protected_block,
)

log = logging.getLogger(__name__)

#: Bumped whenever the wording below changes in a way that could change model
#: behaviour, for the same reason ``POLISH_PROMPT_VERSION`` exists: a quality
#: regression must be attributable to a prompt revision, not a provider.
#:
#: v2: plain text instead of the markdown skeleton, the transcript's own
#: language instead of English, a spoken register (the humanizer rules, cut
#: to the six that matter at this latency), and an explicit rule that several
#: tasks spoken at once all survive. Measured live 2026-08-24: v1 answered
#: with ``## Task`` headings in English and folded two dictated tasks into one.
#:
#: v3: the shape of a professional request (situation, goal, limits, thanks)
#: as paragraphs without labels, "fix what exists" instead of "implement",
#: one statement per fact (no restating "make sure" sentences), a courteous
#: register with the transcript-language word bans, plain typography, a
#: closing line of thanks, and two worked examples. Measured live 2026-08-27
#: on the fast chain: v2 dropped the observed symptom ("shows done while it
#: is still working"), turned a broken existing indicator into "implement an
#: indicator", padded with a "Stelle sicher" restatement of the sentence
#: before, and wrote U+2011 non-breaking hyphens into identifiers.
#:
#: v4: the two-phase answer (``<analysis>`` then ``<prompt>``), fidelity to
#: the speaker's own words as the DEFAULT rather than one rule among many,
#: and no closing line at all. Measured live 2026-08-27: v3 answered in under
#: a second — no pass over the transcript worth the name — reworded phrasing
#: the user had chosen deliberately, thinned the context they had given, and
#: closed every message with a sign-off the receiving agent has no use for.
#:
#: The language rule is a POINT OF THE ANALYSIS and both worked examples are
#: shown, one German and one English. A first cut of v4 kept only the English
#: pair, trusting the written rule to carry the language; measured live on
#: Groq the same day, a German dictation came back as a fluent English
#: message. A shown example outweighs a written rule, so the examples have to
#: disagree with each other about language for the rule to be readable at
#: all — and naming the language in writing before composing is what makes
#: the model commit to it.
PROMPT_MODE_PROMPT_VERSION: Final[int] = 4

#: The status a successful Prompt Mode delivery reports on the history row.
#: Lives in ``POLISH_STATUSES`` (the shared vocabulary) — restated here as a
#: named constant so call sites compare against a name, not a string literal.
STATUS_PROMPTED: Final[str] = "prompted"

# The ceiling and its bounds. The bar is that the writer spends about 5-7 s
# thinking; the two-phase answer is what makes it spend them, and a ceiling
# has to clear that plus the request itself. 12 s is the hard stop — a long
# dictation with a full inventory lands at 6-9 s and must not be cut off at
# the moment it starts writing the message. 4 s is as low as anyone may set
# it before the analysis phase can no longer finish (below that the pass only
# ever times out, which is worse than being off), and 20 s is as far as it
# may be pushed before the feature stops being dictation.
_DEFAULT_TIMEOUT_MS: Final[int] = 12_000
_MIN_TIMEOUT_MS: Final[int] = 4_000
_MAX_TIMEOUT_MS: Final[int] = 20_000

# The budget covers ALL THREE blocks: the analysis (300-900 tokens on a real
# dictation), the draft, and the message the read-back corrects. Generous on
# purpose — a cut-off answer is rejected as truncated and costs the whole
# pass. Temperature 0: this is rewriting, not writing.
_MAX_OUTPUT_TOKENS: Final[int] = 4_000
_TEMPERATURE: Final[float] = 0.0

# The three blocks of the answer. Parsed with tolerant regexes rather than an
# XML parser: the content is prose the model wrote, it may contain a stray
# angle bracket, and a strict parser would throw away a perfectly good message
# over one. Case-insensitive and whitespace-tolerant for the same reason — a
# fast model writes ``<Prompt>`` often enough to matter.
#
# ``<prompt>`` is LAST in the answer, so the regex takes the last one: a model
# that names the tag inside its analysis ("...then the <prompt> block...")
# would otherwise hand us its worksheet.
_ANALYSIS_BLOCK_RE: Final[re.Pattern[str]] = re.compile(
    r"<\s*analysis\s*>(.*?)<\s*/\s*analysis\s*>", re.IGNORECASE | re.DOTALL
)
_PROMPT_OPEN_RE: Final[re.Pattern[str]] = re.compile(r"<\s*prompt\s*>", re.IGNORECASE)
_PROMPT_CLOSE_RE: Final[re.Pattern[str]] = re.compile(r"<\s*/\s*prompt\s*>", re.IGNORECASE)
# The fallbacks, most recent boundary first: everything after the draft is the
# corrected message, everything after the analysis is at worst draft + message.
_CLOSE_FALLBACKS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"<\s*/\s*draft\s*>", re.IGNORECASE),
    re.compile(r"<\s*/\s*analysis\s*>", re.IGNORECASE),
)
# Any leftover tag of ours the model echoed inside the message it wrote.
_OUR_TAGS_RE: Final[re.Pattern[str]] = re.compile(
    r"<\s*/?\s*(?:analysis|draft|prompt)\s*>", re.IGNORECASE
)

# A courtesy sign-off. v3 guaranteed one in code; v4 removes it, and this cuts
# the one a model adds on its own — the message ends on its last piece of
# substance, because the reader is a coding agent that gains nothing from
# being thanked.
#
# It comes in two shapes and both have been measured live on 2026-08-27: a
# line of its own ("...\n\nDanke!") and a sentence tacked onto the end of the
# last paragraph ("...lasse ihn unverändert. Danke dir."). Same vocabulary,
# two anchors — the second one needs a sentence boundary in front of it, which
# also keeps a message that IS a thank-you from being erased.
_SIGN_OFF_WORDS: Final[str] = (
    r"(?:thanks?|thank\s+you|dank(?:e|esch[öo]n)?|gracias|merci|grazie|obrigad[oa]|"
    r"bedankt|tak|tack|takk|kiitos|dzi[eę]kuj[eę]|d[ěe]kuji|спасибо|ありがとう|谢谢|"
    r"regards|cheers|gr[üu][ßs]e)"
)
_SIGN_OFF_LINE_RE: Final[re.Pattern[str]] = re.compile(
    rf"^\W{{0,3}}(?:\w+\s+){{0,4}}?{_SIGN_OFF_WORDS}[\w\s,!.…]{{0,40}}$",
    re.IGNORECASE,
)
_SIGN_OFF_SENTENCE_RE: Final[re.Pattern[str]] = re.compile(
    rf"(?<=[.!?…])[ \t]+(?:\w+\s+){{0,3}}?{_SIGN_OFF_WORDS}[\w\s,]{{0,30}}[.!…]*\s*$",
    re.IGNORECASE,
)

# The composer's two shared rules plus what a transcript-only, latency-bound
# pass needs on top. No skeleton for the message: the answer is prose, and a
# model handed a skeleton reproduces it. The ANALYSIS does get a skeleton,
# for the opposite reason — it exists to be worked through, item by item.
_SYSTEM_PROMPT: Final[str] = f"""\
You turn a spoken, dictated instruction into the message a person would type \
to an AI coding agent. The user will paste what you write into that agent \
themselves, so it has to read like a well-written request from a colleague: \
complete, calm, and no longer than it needs to be.

ANSWER IN THREE BLOCKS, IN THIS ORDER, AND NEVER SKIP ONE.

<analysis>
...your working pass over the transcript, worked through item by item...
</analysis>
<draft>
...your first version of the message...
</draft>
<prompt>
...the message again, corrected by the read-back, and nothing else...
</prompt>

PHASE 1 - THE ANALYSIS. Work through all seven points below in writing before \
you compose a single sentence of the message. This is not a summary and it is \
not for the user; nobody will read it. It exists so that you have actually \
been through the transcript by the time you write. Take the time it needs - a \
careful pass here is the whole difference between a message that carries what \
the user said and one that carries your impression of it.

1. LANGUAGE. Name the language the transcript is in, and quote the four or \
five words that told you. THE MESSAGE IS WRITTEN IN THAT LANGUAGE, whatever \
language these instructions or the examples below happen to be in. A German \
transcript gets a German message, a Spanish one a Spanish message. Note also \
which form of address the user used, and whether that language distinguishes \
a familiar from a formal "you".
2. TASKS. Number every separate thing the user asked for, in the order \
spoken. People dictate two, three, five small tasks in one breath, and the \
small one is dropped far more often than the big one. Write the count.
3. LITERALS. List every file name, folder, path, symbol, function, class, \
setting, product name, error message, quoted string, number, version and \
proper name the user said - exactly as they said it. Each of these must \
appear in the message, spelled the same way.
4. THE SITUATION. What does the user see, what did they expect instead, and \
where does it happen? Quote the transcript verbatim for each of those, in the \
transcript's own language. This is the most valuable thing a transcript holds \
and the first thing a hasty writer throws away.
5. LIMITS. Every restriction, preference, exclusion or bound they stated, each \
one quoted verbatim: "only", "never", "not the live app", "keep the wording", \
"for now", a deadline, a scope they drew.
6. WORDING TO KEEP. Quote the phrases the user chose that carry meaning and \
must survive into the message unchanged - a term of art, a name they use for \
something, a distinction they drew, a word they repeated, a hedge like "I \
think" that must not harden into a finding. Beside each, name what would be \
lost if you replaced it with a synonym.
7. NOT SAID. What is missing, ambiguous, or a pointer you cannot resolve \
("that one", "the second option"). These are left OUT of the message. Never \
guess a file, a cause or a mechanism to fill a gap.

PHASE 2 - THE DRAFT. Write the message against the analysis, in the \
``<draft>`` block. This is a first version, not the answer.

PHASE 3 - THE READ-BACK. Go through the draft once against the seven \
questions below and write the corrected message into the ``<prompt>`` block. \
When the draft was already right, the two blocks are simply the same text - \
that is a fine outcome and not a reason to change anything for the sake of \
it. Do NOT write out what you found; the corrections go into the message, not \
next to it.

a. Is it in the language from point 1?
b. Is every task from point 2 there, none merged and none dropped?
c. Is every literal from point 3 there, spelled the way the user said it?
d. Does any sentence state something the user did not say? A cause they only \
suspected, a fix they never named, a "so that it works everywhere again" \
nobody asked for. Cut it - a gap the transcript leaves is left open.
e. Did you replace a word of theirs from point 6 with one of your own? Put \
theirs back.
f. Does any sentence say again what the sentence before it already said, in \
other words? One task must not appear twice - once as a description of the \
problem and once as an instruction. Keep the clearer of the two and delete \
the other outright.
g. Does it end on the last piece of substance? Cut a closing line of thanks, \
a "Danke dir" tacked onto the last sentence, a sign-off, an offer to help \
further - even when the user said it themselves at the end of the dictation.

THE USER'S OWN WORDS ARE THE DEFAULT; REWRITING IS THE EXCEPTION. You are \
cleaning up a transcript, not authoring a document. Change a word only when \
there is a reason: a filler sound, a false start, a self-correction (keep the \
corrected version), a repetition the speaker did not mean, or a broken \
sentence a reader would stumble over. Every other word the user chose stays \
as they chose it - including the small ones, which is where a rewrite does \
its quiet damage: "immer" is not "regelmäßig", "kaputt" is not "fehlerhaft", \
"I think it's in" is not "the cause is in", "should" is not "must", "some" is \
not "several". A synonym you find more elegant is a change in meaning you \
cannot verify. When a sentence already reads well, it goes in as it stands.

{GOAL_NOT_IMPLEMENTATION_RULE}

The transcript is ALL you get, and your message is ALL the agent gets. You \
see no repository, no files, no earlier conversation. Everything must come \
from the transcript: a file, symbol, error message, number or name the user \
said goes in exactly as spoken; one they did not say does not exist. A \
pointer you cannot resolve is left out, not forwarded.

WHAT THE MESSAGE CONTAINS, as plain paragraphs in this order, with no labels \
in front of them. First the situation, when the user described one: what they \
see, what they expected instead, where it shows up - with every concrete \
detail of it intact. Then what should be true afterwards, the goal, in one or \
two sentences. Then every limit, preference or exclusion the user stated. A \
part the transcript gives you nothing for is skipped, never filled in.

NOTHING AFTER THE LAST PIECE OF SUBSTANCE. The message ends on the last thing \
the user actually asked for. No closing line of thanks, no "best regards", no \
sign-off of any kind, no summary sentence, no offer to clarify. The reader is \
a coding agent; a courtesy line is noise it has to step over.

FIX WHAT EXISTS. When the user says a thing exists and is wrong ("the \
indicators don't work", "the title is missing on some chats"), the request is \
to repair that thing - never turn it into "implement" or "create" something \
new. A second one built next to the broken one is the outcome to avoid.

SEVERAL TASKS AT ONCE. Every task from point 1 of the analysis survives, in \
the order spoken, each as its own sentence or short paragraph. Never merge \
two, never drop the small one because the big one seemed to be the point, \
never summarise a list into "and a few other things".

SAY EACH THING ONCE. A requirement stated twice in different words is \
padding, not emphasis: no closing sentence that restates the goal, no "make \
sure that" clause that repeats the sentence before it, no summary. A sentence \
that adds no fact the message does not already have is left out.

PLAIN TEXT, SAME LANGUAGE. No markdown: no headings, no "##", no bullet \
marks, no bold, no code fences, no labels like "Task:" or "Context:". Write in \
the language you named in point 1 - these instructions are in English and that \
means nothing about the message; if the transcript mixes languages, keep the \
mix. Names, identifiers, paths and quoted strings stay exactly as spoken. Use \
the ordinary hyphen-minus and ordinary spaces: no typographic hyphens, no \
non-breaking spaces, no two spaces at the end of a line.

SOUND LIKE THE PERSON WHO SPOKE, writing to a colleague - not like a ticket \
and not like a document:
- Short, direct sentences. Say the thing, then stop.
- Courteous where the speaker was courteous: their "please", their "I would \
like", kept where they said it and not added where they did not.
- Address the agent the way the user did. In a language that distinguishes a \
familiar from a formal "you", keep the user's form and never switch to the \
formal one on your own; a transcript that shows neither gets the familiar one.
- Everyday words. No "utilize", "leverage", "ensure", "make sure", "robust", \
"seamless", "comprehensive", "delve" - and not their equivalents in the \
transcript's language either.
- No sets of three for rhythm, no "not only ... but also", no dash-heavy \
asides.

Drop filler sounds, false starts and self-corrections, and any clause \
addressing the assistant or the agent by name. State what "done" looks like \
only when the user said it. The ``<prompt>`` block holds ONLY the message: no \
preamble, no quotes, no comment of your own.

{FORBIDDEN_SUBJECTS_RULE}

Two worked examples, one per language, because THE MESSAGE FOLLOWS THE \
TRANSCRIPT AND NOT THE EXAMPLE. Measured live 2026-08-27: with only the \
English pair shown, a German dictation came back as an English message. The \
analyses are shown short here; yours is as long as the transcript needs.

Transcript: "okay so um the login page is broken again when you type a wrong \
password it just shows a blank screen instead of the error message i think \
it's in the auth handler file can you have a look and fix it so the message \
shows up like it used to and also rename the save button to submit"
Answer:
<analysis>
1. LANGUAGE: English ("okay so um", "can you have a look"). The message is \
written in English. Informal, second person.
2. TASKS (2): (a) fix the blank screen on a wrong password so the error \
message shows again; (b) rename the save button to submit.
3. LITERALS: "login page", "auth handler file", "save", "submit", "wrong \
password", "blank screen", "error message".
4. SITUATION: "it just shows a blank screen instead of the error message", on \
a wrong password. "broken again" and "like it used to" say it worked before.
5. LIMITS: none stated.
6. WORDING TO KEEP: "broken again" (not "is failing" - it has failed before); \
"I think it's in" (a guess, not a finding - "the cause is in" would send the \
agent off certain about something the user only suspects); "like it used to" \
(names the old behaviour as the target).
7. NOT SAID: which file exactly, what changed, no repro steps beyond the \
wrong password.
</analysis>
<draft>
The login page is failing again: on a wrong password the error message does \
not appear and the screen stays blank. The cause is in the auth handler file.

Please fix it so the error message is shown again and users get proper \
feedback on a failed login.

Also, please rename the save button to submit.
</draft>
<prompt>
The login page is broken again: when you type a wrong password it just shows \
a blank screen instead of the error message. I think it's in the auth handler \
file.

Please have a look and fix it so the error message shows up like it used to.

Also, please rename the save button to submit.
</prompt>

Transcript: "ähm also die Indikatoren funktionieren nicht mehr richtig ich \
möchte dass man sieht ob die Session gerade arbeitet oder nicht weil es ist \
zum Beispiel so dass angezeigt wird sie ist fertig obwohl sie noch arbeitet \
oder andersrum und live aktualisieren tun die sich auch nicht"
Answer:
<analysis>
1. LANGUAGE: German ("ähm also", "ich möchte dass", "oder andersrum"). The \
message is written in German. "du"-Form is not shown either way, so the \
familiar form is used.
2. TASKS (1): repair the session indicators.
3. LITERALS: "Indikatoren", "Session".
4. SITUATION: "angezeigt wird sie ist fertig obwohl sie noch arbeitet oder \
andersrum", and "live aktualisieren tun die sich auch nicht".
5. LIMITS: none stated.
6. WORDING TO KEEP: "funktionieren nicht mehr richtig" - they EXIST and are \
broken, so this is a repair and never "implementiere Indikatoren"; "live" is \
the user's word for the update behaviour.
7. NOT SAID: since when, which screen, no file named.
</analysis>
<draft>
Die Statusanzeigen der Sessions sind fehlerhaft: Eine laufende Session wird \
als abgeschlossen dargestellt und umgekehrt, und eine Aktualisierung in \
Echtzeit findet nicht statt.

Bitte implementiere eine zuverlässige Anzeige des Session-Status, die sich \
automatisch aktualisiert, damit die Übersicht wieder verwendbar ist.
</draft>
<prompt>
Die Indikatoren, die anzeigen, ob eine Session gerade arbeitet, funktionieren \
nicht mehr richtig: Eine Session wird als fertig angezeigt, obwohl sie noch \
arbeitet, und andersrum. Live aktualisieren sie sich auch nicht.

Bitte bring die Indikatoren wieder in Ordnung, sodass man sieht, ob eine \
Session gerade arbeitet, und sodass sie sich live aktualisieren.
</prompt>\
"""

# What a plain-text answer must not contain. Headings and fences are the v1
# failure; bold and bullet marks are the shape a model slides back into when
# it is told "structure" without "prose". Checked against the MESSAGE only —
# the analysis is a numbered list by design and never reaches this.
_MARKDOWN_RE: Final[re.Pattern[str]] = re.compile(r"(?m)^\s*(#{1,6}\s|```|[-*]\s|\d+[.)]\s)|\*\*")

# Typographic debris a fast model writes into identifiers and paths. Measured
# live 2026-08-27: gpt-oss on the fast chain joined "Jarvis‑Bar" and
# "Self‑Hosted" with U+2011 (non-breaking hyphen), which a grep for "Self-Hosted"
# in the receiving agent never matches. The whole hyphen block maps to the
# hyphen-minus when it sits inside a word; a spaced dash stays a spaced dash
# so an aside the model wrote as " – " is not turned into " - " by force.
_TIGHT_HYPHEN_RE: Final[re.Pattern[str]] = re.compile(r"(?<=\w)[‐‑‒–—](?=\w)")
_LOOSE_HYPHEN_RE: Final[re.Pattern[str]] = re.compile(r"[‐‑]")
_SPECIAL_SPACE_RE: Final[re.Pattern[str]] = re.compile(r"[    ​]")
_TRAILING_WS_RE: Final[re.Pattern[str]] = re.compile(r"[ \t]+$", re.MULTILINE)
_BLANK_RUN_RE: Final[re.Pattern[str]] = re.compile(r"\n{3,}")

# --- Fidelity guards -------------------------------------------------------
#
# The polish pass measures how far the words moved. Here they are allowed to
# move, so what is measured instead is what got LOST. Two things, both
# deterministic, both cheap, both fail-open:
#
#   * a literal the user spoke that the message does not carry, and
#   * a message so much shorter than the transcript that context was thinned
#     rather than tightened.
#
# Neither judges style. A rewrite that keeps every literal and most of the
# substance passes, however differently it reads — that judgement is the
# model's, and the prompt above is where it is made.

# What counts as a literal worth carrying: an identifier or path
# (``auth_handler``, ``src/app.ts``, ``JarvisBar``), a version or number of
# two digits or more, and anything the user put in quotes. Deliberately NOT
# ordinary words: the message is allowed to rephrase prose, and a guard over
# every noun would reject every honest rewrite.
_IDENTIFIER_RE: Final[re.Pattern[str]] = re.compile(
    r"""
    \b(?:
        [A-Za-z0-9_]+(?:[./\\-][A-Za-z0-9_]+)+        # a.b, src/app.ts, foo-bar
      | [A-Za-z]+_[A-Za-z0-9_]+                       # snake_case
      | [a-z]+[A-Z][A-Za-z0-9]*                       # camelCase
      | [A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*               # PascalCase
    )\b
    """,
    re.VERBOSE,
)
_NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"\b\d{2,}(?:[.,]\d+)*\b")
# Double quotes only. A lone ``'`` is an apostrophe far more often than a
# quotation mark in a transcript ("it's", "don't"), and treating one as an
# opening quote turns the rest of the sentence into a "literal" that the
# message is then required to reproduce word for word.
_QUOTED_RE: Final[re.Pattern[str]] = re.compile("[\"“„«]([^\"“”„«»\n]{2,60})[\"”»]")

#: How many of the transcript's literals may go missing before the message is
#: rejected. Not zero: a recognizer writes the same spoken name two ways in
#: one breath ("JarvisBar", "Jarvis Bar"), and rejecting on one of those costs
#: a good message. One is a slip; a quarter of them is a writer that stopped
#: reading.
_MAX_LOST_LITERALS: Final[int] = 1
_LOST_LITERAL_SHARE: Final[float] = 0.25

#: How far the message may shrink against the transcript before it counts as
#: thinned rather than tightened, and the shortest transcript this is measured
#: on at all. A dictation is 25-40 % filler, so a healthy message lands around
#: 60-80 % of the spoken word count; under 40 % something the user said is
#: simply not in there. Short transcripts are exempt — "fix the typo on the
#: login page" is a complete request at eight words and its message is allowed
#: to be shorter still.
_MIN_LENGTH_SHARE: Final[float] = 0.40
_MIN_MEASURED_WORDS: Final[int] = 30

_WORD_RE: Final[re.Pattern[str]] = re.compile(r"[^\W\d_]+", re.UNICODE)


def prompt_mode_enabled(cfg: Any) -> bool:
    """``[dictation].prompt_mode`` — off on any config that has not heard of it."""
    return bool(getattr(cfg, "prompt_mode", False))


def build_prompt_mode_prompt(protected_terms: Sequence[str] = ()) -> str:
    """The system prompt for one Prompt Mode call.

    ``protected_terms`` are the spellings the writer must carry across
    untouched — the STT dictionary, the wake word, the user's own name. The
    block is the polish pass's, not a copy: what "spell this exactly" looks
    like to a model is one decision.
    """
    return f"{_SYSTEM_PROMPT}\n\n{build_protected_block(protected_terms)}"


def extract_prompt_block(answer: str) -> str:
    """The message out of a three-block answer, tolerant of a ragged one.

    In order of preference: the content of the LAST ``<prompt>`` (closing tag
    optional, because a model that ran out of budget still wrote a usable
    message up to that point — the truncation guard is what decides whether it
    is usable); otherwise everything after ``</draft>`` and then everything
    after ``</analysis>``, which is the same message with an opening tag
    forgotten; otherwise the whole answer, on the assumption that the model
    ignored the block instruction and wrote the message directly. That last
    case then faces the same guards as before, so a model that answers with
    its worksheet is caught by the markdown guard rather than handing the user
    a numbered list.

    Whatever comes out is stripped of any of our own tags the model echoed
    inside it.
    """
    body = str(answer or "").strip()
    if not body:
        return ""
    opens = list(_PROMPT_OPEN_RE.finditer(body))
    if opens:
        # The LAST opening tag, not the last complete block: a model that
        # names the tag while thinking ("...then I fill the <prompt> block...")
        # opens one inside its analysis and never closes it, and a match that
        # started there would swallow the draft along with the message.
        rest = body[opens[-1].end() :]
        close = _PROMPT_CLOSE_RE.search(rest)
        return _OUR_TAGS_RE.sub("", rest[: close.start()] if close else rest).strip()
    for pattern in _CLOSE_FALLBACKS:
        closes = list(pattern.finditer(body))
        if closes:
            return _OUR_TAGS_RE.sub("", body[closes[-1].end() :]).strip()
    return _OUR_TAGS_RE.sub("", body).strip()


def analysis_word_count(answer: str) -> int:
    """How many words the model spent on its working pass.

    Not a guard — nothing is rejected for a thin analysis, because a short
    transcript deserves a short one. It is logged, so "the writer answered in
    400 ms again" is a question the log can answer without a live capture.
    """
    match = _ANALYSIS_BLOCK_RE.search(str(answer or ""))
    if not match:
        return 0
    return len(_WORD_RE.findall(match.group(1)))


def normalize_prompt_text(text: str) -> str:
    """Undo the typographic debris a fast model leaves in a plain-text prompt.

    Deterministic and lossless in meaning: a non-breaking hyphen inside a word
    becomes the hyphen-minus the user's keyboard produces, special spaces
    become spaces, trailing spaces on a line go (two of them are a markdown
    line break, which the answer must not carry), and runs of blank lines
    collapse to one paragraph break. Runs BEFORE the guards, so a protected
    spelling the model wrote with a U+2011 still counts as present.
    """
    body = str(text or "")
    body = _TIGHT_HYPHEN_RE.sub("-", body)
    body = _LOOSE_HYPHEN_RE.sub("-", body)
    body = _SPECIAL_SPACE_RE.sub(" ", body)
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    body = _TRAILING_WS_RE.sub("", body)
    body = _BLANK_RUN_RE.sub("\n\n", body)
    return body.strip()


def ends_with_sign_off(text: str) -> bool:
    """Whether *text* closes on a courtesy sign-off, as a line or a sentence."""
    body = str(text or "").strip()
    if not body:
        return False
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if lines and _SIGN_OFF_LINE_RE.match(lines[-1]):
        return True
    return bool(_SIGN_OFF_SENTENCE_RE.search(body))


def strip_closing_sign_off(text: str) -> str:
    """*text* with a trailing courtesy line or sentence removed.

    The prompt asks for a message that ends on its substance; this is the
    guarantee, the way ``ensure_closing_thanks`` was the guarantee of the
    opposite in v3. Loops, because a model that writes "Danke!" under "Viele
    Grüße" has written two, and because cutting the last line can expose
    another underneath it. Never strips the whole message: a line of thanks
    that is all there is stays, and a sentence is only cut when a finished
    sentence stands in front of it.

    Runs AFTER the truncation guard, for the reason that guard exists — a
    message that stopped mid-sentence must be caught as damage, not tidied.
    """
    body = str(text or "").strip()
    while True:
        trimmed = _SIGN_OFF_SENTENCE_RE.sub("", body).strip()
        if trimmed != body and trimmed:
            body = trimmed
            continue
        lines = body.splitlines()
        kept = [line.strip() for line in lines if line.strip()]
        if len(kept) < 2 or not _SIGN_OFF_LINE_RE.match(kept[-1]):
            return body
        cut = len(lines)
        while cut > 0 and not lines[cut - 1].strip():
            cut -= 1
        body = "\n".join(lines[: cut - 1]).strip()


def transcript_literals(text: str) -> list[str]:
    """The literals in *text* a faithful message has to carry.

    Identifiers and paths, numbers of two digits or more, and quoted spans.
    Case is preserved in what comes back (the caller folds it) so a log line
    can name the thing that went missing the way the user said it.
    """
    body = str(text or "")
    found: dict[str, str] = {}
    for pattern in (_IDENTIFIER_RE, _NUMBER_RE):
        for match in pattern.finditer(body):
            token = match.group(0)
            found.setdefault(token.casefold(), token)
    for match in _QUOTED_RE.finditer(body):
        token = match.group(1).strip()
        if token:
            found.setdefault(token.casefold(), token)
    return list(found.values())


def lost_literals(raw: str, prompt: str) -> list[str]:
    """Which of the transcript's literals the message does not carry."""
    target = str(prompt or "").casefold()
    return [token for token in transcript_literals(raw) if token.casefold() not in target]


def looks_thinned(raw: str, prompt: str) -> bool:
    """True when the message is too short to still hold what was dictated.

    A word-count proxy, and only over transcripts long enough for the ratio to
    mean anything. It catches the failure the maintainer reported — a message
    that reads well and quietly leaves out half of what was said — without
    having any opinion about wording.
    """
    spoken = len(_WORD_RE.findall(str(raw or "")))
    if spoken < _MIN_MEASURED_WORDS:
        return False
    written = len(_WORD_RE.findall(str(prompt or "")))
    return written < spoken * _MIN_LENGTH_SHARE


def prompt_guard_reason(raw: str, prompt: str, *, protected: Sequence[str] = ()) -> str:
    """Why *prompt* must not be delivered, or ``""`` when it may.

    Structural and factual, never stylistic. The drift guards of the polish
    pass measure how far the words moved, and here they are ALLOWED to move;
    what can still go wrong is the shape of the answer and its fidelity to
    what was said:

    * ``empty`` — nothing came back worth pasting.
    * ``markdown`` — a heading, a fence, bold or a list mark. The answer is
      pasted into a chat box as a message; markdown there is the v1 defect,
      and it is also what a leaked analysis looks like.
    * ``truncated`` — stopped mid-sentence. Reads as complete, is not.
    * ``lost_protected_term`` — a spelling the user protected was in the
      transcript and is not in the prompt. The dictionary exists because the
      recognizer gets these wrong; a writer that drops one has undone that.
    * ``dropped_detail`` — file names, identifiers, numbers or quoted strings
      the user spoke are missing from the message. The agent is sent looking
      for something the user had already named.
    * ``dropped_context`` — the message is under 40 % of the spoken word
      count. A dictation is filler-heavy, but not that filler-heavy: what is
      gone is context, not padding.
    """
    body = (prompt or "").strip()
    if not body:
        return "empty"
    if _MARKDOWN_RE.search(body):
        return "markdown"
    if looks_truncated(body):
        return "truncated"
    source = (raw or "").casefold()
    target = body.casefold()
    for term in protected or ():
        needle = str(term or "").strip().casefold()
        if needle and needle in source and needle not in target:
            return "lost_protected_term"
    literals = transcript_literals(raw)
    if literals:
        missing = lost_literals(raw, body)
        allowed = max(_MAX_LOST_LITERALS, int(len(literals) * _LOST_LITERAL_SHARE))
        if len(missing) > allowed:
            return "dropped_detail"
    if looks_thinned(raw, body):
        return "dropped_context"
    return ""


def timeout_budget_s(cfg: Any, override_s: float | None = None) -> float:
    """The wall-clock ceiling for one call, in seconds."""
    if override_s is not None:
        return max(0.05, float(override_s))
    try:
        ms = int(getattr(cfg, "prompt_mode_timeout_ms", _DEFAULT_TIMEOUT_MS))
    except (TypeError, ValueError):
        # A hand-edited jarvis.toml that never reached the validator (a plain
        # object in tests, an older config class): the shipped default is the
        # right answer and nobody needs a log line about a missing knob.
        ms = _DEFAULT_TIMEOUT_MS
    return max(_MIN_TIMEOUT_MS, min(_MAX_TIMEOUT_MS, ms)) / 1000.0


async def compose_prompt(
    raw: str,
    *,
    cfg: Any,
    protected_terms: Sequence[str] = (),
    timeout_s: float | None = None,
    language: str = "",
) -> PolishOutcome:
    """Turn *raw* into an agent prompt. Never raises, never loses text.

    Returns a :class:`~jarvis.dictation.polish.PolishOutcome` so the delivery
    path, the history row and the settings screen read Prompt Mode through the
    same fields they already read the polish pass through. ``text`` is the
    prompt on :data:`STATUS_PROMPTED` and the untouched *raw* on every other
    status — the caller then falls through to whatever it would have done
    without this feature, which is the polish pass.

    ``language`` is the resolved dictation language. Since v4 the message
    carries no generated closing line, so nothing here has to be written in
    it; the parameter stays because every caller passes it and because the
    language belongs in the log line when a delivery has to be explained.

    The model is the polish pass's own chain (``[dictation].polish_provider``
    and its ``auto`` order), asked for the family's stronger fast model — the
    one the translate pass uses — because turning speech into a brief asks
    more of a model than punctuating it, and that model still answers inside
    the ceiling with an analysis in front of it.
    """
    started = time.perf_counter()
    source = str(raw or "")

    def _result(
        status: str,
        *,
        provider: str = "",
        model: str = "",
        reason: str = "",
        text: str | None = None,
    ) -> PolishOutcome:
        return PolishOutcome(
            text=source if text is None else text,
            status=status,
            provider=provider,
            model=model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            reason=reason,
        )

    try:
        if not prompt_mode_enabled(cfg):
            return _result("off")
        if not source.strip():
            return _result("skipped_short", reason="empty_input")

        from jarvis.dictation import polish as polish_pass

        breaker = polish_pass._breaker()
        if await breaker.is_open():
            return _result("unavailable", reason="circuit_open")

        budget_s = timeout_budget_s(cfg, timeout_s)
        attempt = polish_pass._Attempt()
        system = build_prompt_mode_prompt(protected_terms)
        user = build_polish_user_message(source)
        try:
            answer = await asyncio.wait_for(
                _call_chain(
                    cfg,
                    system=system,
                    user=user,
                    attempt=attempt,
                    deadline=time.monotonic() + budget_s,
                ),
                budget_s,
            )
        except TimeoutError:
            await breaker.record_failure()
            log.info(
                "dictation prompt mode exceeded its %d ms ceiling on %r; delivering "
                "the transcript to the ordinary passes.",
                int(budget_s * 1000),
                attempt.provider,
            )
            return _result(
                "timeout", provider=attempt.provider, model=attempt.model, reason="deadline"
            )

        if answer is None:
            if attempt.error == "no_credential":
                log.debug(
                    "dictation prompt mode found no usable provider family; "
                    "delivering the transcript to the ordinary passes."
                )
                return _result("unavailable", reason="no_credential")
            await breaker.record_failure()
            status = "local_only" if attempt.on_device_only else "provider_error"
            return _result(
                status,
                provider=attempt.provider,
                model=attempt.model,
                reason=attempt.error or "no_provider",
            )

        await breaker.record_success()

        # How long the writer spent thinking, in the only unit that measures
        # it: words it wrote before it started answering. A run that reports 0
        # here is a model that skipped the analysis, which is the v3 behaviour
        # and the thing to watch for after a provider change.
        thought_words = analysis_word_count(answer)
        prompt = normalize_prompt_text(extract_prompt_block(answer))
        reason = prompt_guard_reason(source, prompt, protected=protected_terms)
        if reason:
            log.info(
                "dictation prompt mode answer from %r rejected (%s, %d analysis words); "
                "delivering the transcript to the ordinary passes.",
                attempt.provider,
                reason,
                thought_words,
            )
            return _result(
                "rejected_drift", provider=attempt.provider, model=attempt.model, reason=reason
            )

        log.debug(
            "dictation prompt mode composed a message from %r after %d analysis words "
            "(language %r).",
            attempt.provider,
            thought_words,
            language or "auto",
        )
        return _result(
            STATUS_PROMPTED,
            provider=attempt.provider,
            model=attempt.model,
            text=strip_closing_sign_off(prompt),
        )
    except Exception:
        # ``Exception``, not ``BaseException``, for the reason the polish pass
        # gives: a cancellation or an interpreter exit belongs to whoever
        # raised it, and swallowing one leaves a task that refuses to die.
        log.warning(
            "dictation prompt mode failed unexpectedly; delivering the transcript "
            "to the ordinary passes.",
            exc_info=True,
        )
        return _result("provider_error", reason="unexpected")


async def _call_chain(
    cfg: Any,
    *,
    system: str,
    user: str,
    attempt: Any,
    deadline: float,
) -> str | None:
    """One walk of the polish family chain. Split out so tests can stand in."""
    from jarvis.dictation.polish import _resolve_and_run

    return await _resolve_and_run(
        cfg,
        system=system,
        user=user,
        attempt=attempt,
        deadline=deadline,
        max_output_tokens=_MAX_OUTPUT_TOKENS,
        temperature=_TEMPERATURE,
        # The family's stronger fast model, exactly as the translate pass asks
        # for it: a brief is a rewrite, not a repunctuation.
        translating=True,
    )


__all__ = [
    "PROMPT_MODE_PROMPT_VERSION",
    "STATUS_PROMPTED",
    "analysis_word_count",
    "build_prompt_mode_prompt",
    "compose_prompt",
    "ends_with_sign_off",
    "extract_prompt_block",
    "looks_thinned",
    "lost_literals",
    "normalize_prompt_text",
    "prompt_guard_reason",
    "prompt_mode_enabled",
    "strip_closing_sign_off",
    "timeout_budget_s",
    "transcript_literals",
]
