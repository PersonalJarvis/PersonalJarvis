"""What a composed prompt looks like, and what it is allowed to say.

Every rule here is traceable to the Claude 5 prompting documentation, because
the thing this module replaces was a plausible-sounding prompt that made the
receiving agent behave *worse*:

* **Full specification up front.** Opus 5 "performs best when given the
  complete task specification up front and left to run" — the old 1200-char
  cap worked directly against that.
* **The reason, not only the request.** Fable 5 connects a task to the right
  context when it knows the intent behind it, so ``## Why this matters`` earns
  its place whenever the intent is actually known.
* **An explicit scope bound.** Both models widen tasks — surrounding
  refactors, unrequested abstractions, defensive code for impossible cases —
  unless told not to.
* **No verification ritual.** "If your prompt contains explicit verification
  instructions … remove them: instructions like these cause over-verification
  … and removing them reduces wasted tokens with no loss in quality." Success
  *criteria* stay; "double-check afterwards" is banned.
* **No reasoning echo.** Asking an agent to reproduce its internal reasoning
  can trigger the ``reasoning_extraction`` refusal category on Fable 5.
* **Per-kind guardrails.** The clearest case: on a review, "only report
  high-severity issues" is followed literally and suppresses real findings. On
  an investigation the opposite steer is right — report, do not fix. One
  coherent rule set per kind beats one long contradictory one, so ``task_kind``
  picks the set and only that set is injected.

The composed prompt is always English. The recipient is a coding agent working
in a repository whose artifacts are English (CLAUDE.md §1); a prompt in the
spoken language pulls same-language commit messages and comments after it. The
*spoken readback* to the user is unaffected — that stays in the turn's
resolved output language.
"""
from __future__ import annotations

import re

from .task_kind import (
    KIND_IMPLEMENT,
    KIND_INVESTIGATE,
    KIND_NEUTRAL,
    KIND_QUESTION,
    KIND_REVIEW,
)

# The hard ceiling. MAX_PROMPT_CHARS is the transport cap above this.
# Set above what real briefs measure (2865-3713 across three live runs) rather
# than at it: a limit the writer breaches on every ordinary composition teaches
# it that the limits in this prompt are approximate, and the ones that matter —
# invent nothing, never end on a reference — are not.
MAX_BODY_CHARS = 4500

# What a GOOD prompt weighs, which is a different question from what is allowed.
# The first version stated only the ceiling, and three live compositions came
# back at 549 / 865 / 904 characters — under a third of the budget. A model
# reads "keep it under N" as "be brief", so the target has to be said out loud.
# The floor is the load-bearing half: below it, context the agent will have to
# rediscover for itself has almost certainly been left out.
TARGET_MIN_CHARS = 1800
TARGET_MAX_CHARS = 3200

_SKELETON = """\
## Task
<what to do, in the imperative - two to five sentences. Name the concrete
symbols, files and behaviours involved; do not compress it to a headline>

## Why this matters
<the intent behind the request, and what breaks or improves for the user -
ONLY when it is known or clearly derivable>

## How it works today
<the current behaviour of the code being changed, in real symbol names: which
function does what, who calls it, which state it keeps. This is the part that
saves the agent an entire round of rediscovery>

## Key files
- `@path/to/file` - what lives here and which part of it matters, named
- `@path/to/other` - same, specifically

## Scope
<what is in, what is explicitly out, and the constraints that apply>

## Done when
- <an observable outcome>
- <an observable outcome>\
"""

_SHARED_RULES = f"""\
You turn a spoken instruction into a prompt for a coding agent (Claude Code or \
Codex) that is already running inside the user's repository. The agent will act \
on what you write, so write the brief you would want to receive.

Output format - Markdown, using exactly this skeleton:

{_SKELETON}

Rules for the skeleton:
- Output ONLY the prompt. No preamble, no commentary, no surrounding code \
fence, no quotes.
- `## Task` is mandatory. Every other section is OPTIONAL: omit a section \
entirely when you have nothing grounded to put in it. Never pad a section to \
fill the shape.

DESCRIBING is not INVENTING, and the difference decides how good this prompt \
is. Keep these apart:
- INVENTING is forbidden: a requirement, a constraint, a scope boundary or a \
success criterion the user did not state and the workspace does not establish. \
`## Done when` may only contain what the user asked for or what the repository \
itself determines (its test command, its lint gate). If neither gives you \
anything, omit the section.
- DESCRIBING is wanted, in detail: what the code in the file outlines actually \
does, which function is called from where, what the workspace profile says \
about the stack and conventions, which constraint the repository's own house \
rules impose. None of that is invention - it is context you were given and the \
agent would otherwise spend its first minutes rediscovering. Spend words here.

Be thorough. Aim for {TARGET_MIN_CHARS}-{TARGET_MAX_CHARS} characters. A prompt \
under 800 characters has almost certainly dropped context you were handed: go \
back and say what the relevant code does today, name the symbols involved, and \
state the constraints that apply. Length must come from substance - concrete \
file, symbol and behaviour detail - never from filler, restating the task in \
other words, or hedging.
- Reference files with `@path` inside a `## Key files` list item, using ONLY \
paths from the candidate list you are given. Say what each file holds and \
which function or class in it is the relevant one - "the ranking pipeline" is \
worth far less to the agent than "`_fuse_ranked()`, which merges the ranked \
lists before the relevance gate". Omit the section when no candidate is \
clearly relevant.
- Never end the prompt on an `@path` or a `/command`: a trailing reference \
leaves the agent's completion popup open and the prompt is never submitted.
- Write in ENGLISH, whatever language the user spoke. The agent works in a \
repository whose code, comments and commits are English.
- Preserve every constraint, file, symbol and intent the user expressed. \
Remove speech artefacts: filler words, false starts, self-corrections, and the \
clause addressing the agent by name.
- Never exceed {MAX_BODY_CHARS} characters.

Two subjects must not appear in the prompt AT ALL - not as a requirement and \
not as a prohibition. Say nothing about either; the agent handles both \
correctly on its own, and raising them measurably degrades its work:
- Verification. No "verify your work", no "double-check", and equally no "do \
not double-check". Write neither.
- Its own reasoning. No "explain your reasoning", "show your thinking" or \
"narrate your steps", and equally no instruction forbidding those. Write \
neither. (A line like "Do not narrate your internal reasoning" in the finished \
prompt is this rule being LEAKED instead of followed - it is a defect.)\
"""

# Per-kind guardrails. Short on purpose: a short coherent set is followed, a
# long one is averaged.
_KIND_RULES: dict[str, str] = {
    KIND_IMPLEMENT: """\
This is an IMPLEMENTATION task. Specific to it:
- Give the COMPLETE specification up front, so the agent can work start to \
finish without coming back with questions. This is the kind that most deserves \
length: describe the behaviour that must exist afterwards, the shape it should \
take, where it belongs in the existing code, and how it should behave when \
things go wrong.
- `## How it works today` is close to mandatory here. Name the function the \
change lands in, what it currently does, and who calls it - the agent cannot \
fit a change into code it has to find first.
- `## Scope` matters here. Bound the work: name what is out of scope, and say \
that surrounding cleanup, unrequested refactors, speculative abstractions and \
defensive handling for impossible cases are not wanted.
- `## Done when` states observable outcomes, not activities.\
""",
    KIND_REVIEW: """\
This is a REVIEW task. Specific to it:
- Ask for every finding, each with its severity and the file and line it sits \
on. Filtering happens afterwards, in a separate pass. Never instruct the agent \
to report only serious issues, or to apply a high bar, or to err on the side \
of silence - it will follow that literally and stay quiet about real defects.
- Say what the code is FOR and what would count as a defect in it. A reviewer \
who knows that the ranking decides which results reach a spoken answer reviews \
differently from one who sees a sorting function.
- Name what to weigh the code against when the workspace establishes it: the \
repository's own conventions, its anti-pattern register, its tests. List the \
specific dimensions worth a pass - correctness, edge cases, error handling, \
test coverage, and whatever the code's own purpose makes risky.
- The deliverable is the findings. Do not ask for fixes in the same prompt.\
""",
    KIND_INVESTIGATE: """\
This is an INVESTIGATION task. Specific to it:
- The deliverable is a diagnosis: the cause, the evidence for it, and what \
would confirm it. Say so explicitly.
- Say that the agent should report the finding and stop, and should not apply \
a fix before the user has seen it.
- Carry across every symptom, error message, timing and reproduction detail \
the user gave, VERBATIM. Those are the evidence; do not summarise them away.
- Describe the path the code takes through the area under suspicion, in real \
symbol names, and name the places where it could plausibly go wrong. Giving \
the agent the map is not the same as giving it the answer.\
""",
    KIND_QUESTION: """\
This is a QUESTION. Specific to it:
- The deliverable is an answer grounded in the code, not a change. Say that \
the agent should read the relevant files before answering and should not \
change anything.
- Be specific about what is actually being asked and what a useful answer \
would cover, so the agent does not return either a one-liner or an essay.
- A question rarely needs `## Scope` or `## Done when`; omit them unless the \
user set a real boundary.\
""",
    KIND_NEUTRAL: """\
The kind of work is not clearly determined. Stay with what the user said:
- State the task as they framed it, and bound the scope to it - no \
surrounding cleanup, no unrequested refactors.
- Still describe the relevant code as it stands today. Being unsure which KIND \
of work this is is no reason to hand over less context.
- If they described a problem rather than asking for a change, say that the \
deliverable is the assessment.\
""",
}


def system_prompt(kind: str) -> str:
    """The composer's system prompt for one task kind."""
    return f"{_SHARED_RULES}\n\n{_KIND_RULES.get(kind, _KIND_RULES[KIND_NEUTRAL])}"


def user_block(
    *,
    utterance: str,
    instruction: str,
    terminal_name: str,
    agent_display: str,
    profile_lines: list[str],
    candidates: list[str],
    skeletons: dict[str, str],
    house_rules: str,
) -> str:
    """Everything the composer knows, laid out for one model call.

    Longform data first, the request last: the documentation measures up to a
    30 % quality gain from putting the query at the end for multi-document
    inputs, and the file outlines are exactly that.
    """
    parts: list[str] = []

    if profile_lines:
        parts.append("WORKSPACE\n" + "\n".join(profile_lines))
    if house_rules:
        parts.append("HOUSE RULES OF THIS REPOSITORY\n" + house_rules)

    if skeletons:
        outlines = "\n\n".join(
            f'<file path="{path}">\n{text}\n</file>' for path, text in skeletons.items()
        )
        parts.append(
            "FILE OUTLINES (signatures only - bodies omitted; use these to name "
            "real symbols instead of describing code vaguely)\n" + outlines
        )

    candidate_block = (
        "\n".join(f"- {c}" for c in candidates)
        if candidates
        else "(no candidate files matched - omit the Key files section)"
    )
    parts.append(
        "CANDIDATE FILES (repo-relative; use only these in @ references)\n"
        + candidate_block
    )

    parts.append(
        f"The user is talking to the coding agent {agent_display} running in a "
        f"terminal they call {terminal_name}."
    )
    parts.append(f"WHAT THE USER SAID (verbatim speech transcript)\n{utterance}")
    if instruction and instruction != utterance:
        parts.append("THE INSTRUCTION PART, WITH THE ADDRESSING REMOVED\n" + instruction)
    parts.append("Write the prompt now.")

    return "\n\n".join(parts)


def render_fallback(instruction: str, files: list[str]) -> str:
    """The deterministic prompt: same skeleton, no model involved.

    Used whenever no writer model is reachable. It is always better than the
    raw transcript, so the feature never depends on a provider being up.
    """
    task = " ".join((instruction or "").split())
    if not task:
        return ""
    out = [f"## Task\n{task}"]
    if files:
        listed = "\n".join(f"- `@{f}`" for f in files)
        out.append(f"## Key files\n{listed}")
        # Never end on a reference: a trailing @path holds the completion popup
        # open, and the Enter that follows picks a suggestion instead of
        # submitting the prompt.
        out.append("Start with these files; search further if they are not enough.")
    return "\n\n".join(out)[:MAX_BODY_CHARS]


_TRAILING_TOKEN_RE = re.compile(r"[@/][\w./\\-]*$")


def ends_on_reference(text: str) -> bool:
    """True when ``text`` ends on an ``@path`` or ``/command`` token."""
    return bool(_TRAILING_TOKEN_RE.search((text or "").rstrip()))


# Punctuation a finished sentence ends on.
_SENTENCE_ENDINGS = (".", "!", "?", ":", ";", "`", ")", "]", '"', "'", "…")
# Line shapes that legitimately end without punctuation: list items, headings,
# table rows, fenced-block delimiters.
_STRUCTURAL_PREFIXES = ("-", "*", "+", "#", ">", "|", "```")


def looks_truncated(text: str) -> bool:
    """True when a composed prompt stopped mid-sentence.

    On a thinking model the token budget covers the reasoning as well as the
    answer, so an under-provisioned budget produces a brief that simply stops —
    measured live, one ended on "...to find" and was still handed over as a
    finished prompt. That is worse than the plain deterministic prompt, because
    it READS as complete: the agent starts work on an instruction whose second
    half was never written.

    Deliberately narrow. It only fires when the last line is running prose that
    ends without punctuation. A list item ("- `@a.py` - the ranking pipeline")
    and a heading are complete lines that happen to carry no full stop, and
    treating them as damage would degrade good briefs. A false positive costs
    one rougher prompt; a missed truncation sends an agent off with half a task.
    """
    body = (text or "").strip()
    if not body:
        return True
    last = body.splitlines()[-1].strip()
    if not last or last.startswith(_STRUCTURAL_PREFIXES):
        return False
    return not last.endswith(_SENTENCE_ENDINGS)


def looks_like_brief(text: str) -> bool:
    """True when ``text`` has the shape of a brief rather than of an accident.

    The writer can be a subscription CLI, and a CLI answers on stdout whether it
    understood the request or not. Measured live 2026-07-26: one returned
    ``Error: invalid model selection (--model "..." --effort ""): ... requires
    --effort`` — 146 characters, exit path unknown to us, and the composer
    reported it as a successfully composed prompt. That string would then have
    been typed at a coding agent as its task.

    So the test is structural, not semantic: every brief the blueprint asks for
    carries at least one markdown heading (``## Task`` at minimum), and no
    single-line tool error does. Deliberately loose — this rejects debris, it
    does not grade quality. A false positive costs one rougher prompt; a missed
    one sends an agent off with a diagnostic string for a task.
    """
    body = (text or "").strip()
    if not body:
        return False
    return any(line.lstrip().startswith("#") for line in body.splitlines())


__all__ = [
    "MAX_BODY_CHARS",
    "TARGET_MAX_CHARS",
    "TARGET_MIN_CHARS",
    "ends_on_reference",
    "looks_like_brief",
    "looks_truncated",
    "render_fallback",
    "system_prompt",
    "user_block",
]
