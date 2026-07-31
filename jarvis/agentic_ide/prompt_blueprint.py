"""What a composed prompt looks like, and what it is allowed to say.

Every rule here is traceable to the vendors' own prompting documentation,
because the thing this module replaces was a plausible-sounding prompt that
made the receiving agent behave *worse*:

* **Goal and constraints, never the implementation.** The single most
  load-bearing rule, and the one the first version got backwards. Measured
  live 2026-07-27: asked to make composition faster, the composer wrote "use
  non-blocking background tasks for writer resolution" — it handed the agent a
  solution that was *already implemented*, so the agent's task became
  re-doing finished work. Every current frontier guide converges here: state
  what good looks like and the bounds, and let the model choose the route.
  Enumerated steps measurably REDUCE output quality on Fable 5, and leaner
  system prompts score higher on agentic coding evaluations while costing a
  fraction of the tokens.
* **The handover is not the work.** The instruction arrives as speech aimed at
  Jarvis, so it names the handover as well as the task: "prompt T2 and T3 to
  …", "write Alex the prompt for …", "tell Nova it should …". THIS BRIEF IS
  THAT PROMPT. Measured live 2026-07-29, on an order that translates as "write
  the prompt for T2 and T3, and make sure that …": the brief's ``## Task`` read
  "Create or update the system prompts for terminals T2 and T3" — two coding
  agents were sent off to write prompts for themselves, and the analysis the
  user actually asked for reached nobody. The work is what follows the
  handover; writing a prompt is the task only when the user is talking about
  prompt TEXT that lives in the repository.
* **Resolve what the agent cannot see.** A spoken instruction refers back into
  the conversation it came out of — "points two and three", "the second one",
  "do that for the wake path too" — and the agent receives the brief alone.
  The same live composition wrote "Ensure that points 2 and 3 from the current
  context are specifically incorporated": a pointer to something the recipient
  can never open, which is worth exactly as much as writing nothing. The
  recent turns travel with the instruction (``conversation``) so the reference
  can be replaced by what it stands for.
* **Full specification up front.** Complete does not mean prescriptive: the
  agent should not have to come back with questions, which is a statement
  about the CONTEXT it was handed, not about how many steps were dictated.
* **The reason, not only the request.** A model connects a task to the right
  context when it knows the intent behind it, so ``## Why this matters`` earns
  its place whenever the intent is actually known.
* **Scope as intent, not as a prohibition list.** The first version listed
  what the agent must not do (no surrounding cleanup, no unrequested
  refactors, no speculative abstractions, no defensive handling). Anthropic's
  own measured replacement for exactly that failure mode is an intent
  instruction — deliver what was asked at the scope intended, make routine
  judgement calls, flag rather than silently widen — which in testing cut
  scope drift to near zero without producing extra clarifying questions.
* **No verification ritual.** "If your prompt contains explicit verification
  instructions … remove them: instructions like these cause over-verification
  … and removing them reduces wasted tokens with no loss in quality." Success
  *criteria* stay; "double-check afterwards" is banned.
* **No reasoning echo.** Asking an agent to reproduce its internal reasoning
  can trigger the ``reasoning_extraction`` refusal category on Fable 5.
* **Say each thing exactly once.** A rule repeated in two sections is not
  twice as followed; it is noise that dilutes the rules that matter, and it is
  paid for on every composition in latency the user waits through.
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
from collections.abc import Sequence

from . import conversation as conversation_module
from .task_kind import (
    KIND_IMPLEMENT,
    KIND_INVESTIGATE,
    KIND_NEUTRAL,
    KIND_QUESTION,
    KIND_REVIEW,
)

# The hard ceiling. MAX_PROMPT_CHARS is the transport cap above this.
# Set above what real briefs measure rather than at it: a limit the writer
# breaches on every ordinary composition teaches it that the limits in this
# prompt are approximate, and the ones that matter — invent nothing, never end
# on a reference — are not.
MAX_BODY_CHARS = 3600

# What a GOOD prompt weighs, which is a different question from what is allowed.
# Both bounds were learned the hard way and BOTH are load-bearing:
#
# * Without a stated target the writer is brief — three live compositions came
#   back at 549 / 865 / 904 characters, and a lean-prompt A/B on 2026-07-27
#   produced a `## Done when` reading "the time taken is reduced", which is not
#   an acceptance criterion at all. A model reads "keep it under N" as "be
#   brief", so the target has to be said out loud.
# * The old 1800-3200 target then bought length from the wrong place. The brief
#   it produced measured 3801 characters — over its own ceiling — and spent the
#   surplus dictating an implementation the agent should have chosen itself.
#
# So the target came down and the rules changed what the words are spent ON:
# describing the code that exists, not prescribing the code to write. Every
# character also costs the user real waiting time — 3801 characters took 21.8 s
# against 16.0 s for a leaner brief on the same writer.
TARGET_MIN_CHARS = 1400
TARGET_MAX_CHARS = 2400

_SKELETON = """\
## Task
<what must be achieved, in the imperative - two to four sentences naming the
concrete symbols, files and behaviours involved>

## Why this matters
<the intent behind the request, and what breaks or improves for the user -
ONLY when known or clearly derivable>

## How it works today
<the current behaviour in real symbol names: which function does what, who
calls it, which state it keeps>

## Key files
- `@path/to/file` - what lives here and which symbol in it matters
- `@path/to/other` - same, specifically

## Scope
<the bound the user actually drew>

## Done when
- <an observable outcome>
- <an observable outcome>\
"""

_SHARED_RULES = f"""\
You turn a spoken instruction into a brief for a coding agent (Claude Code or \
Codex) already running inside the user's repository. The agent will act on what \
you write, so write the brief you would want to receive.

Give the agent the GOAL, the CONTEXT and the CONSTRAINTS - never the \
implementation. Which approach, which mechanism, which order are its decisions, \
made against code it can see and you cannot; a solution you guess at gets built \
even when it is wrong or already there. Say what must be true when it is done, \
and leave the route open.

The user is SPEAKING to Jarvis about this agent, so their words name the \
handover as well as the work: "prompt T2 and T3 to ...", "write Alex the prompt \
for ...", "tell Nova it should ...". THE BRIEF YOU ARE WRITING IS THAT PROMPT. \
Never make prompt-writing the agent's task - a `## Task` reading "create the \
prompt for T2" sends the agent off to do your job and loses the one thing the \
user asked for. The work is whatever the sentence asks for AROUND the handover. \
(The exception is narrow and unmistakable: the user is talking about prompt \
text that lives in the repository as a file.)

The brief is ALL the agent gets. It sees no conversation, no earlier answer, no \
"context" of any kind, so anything the user said by pointing back - "points two \
and three", "the second option", "what you just suggested", "that one" - must \
be REPLACED by what it stands for, spelled out from the conversation you were \
given. A brief that forwards the pointer instead ("incorporate points 2 and 3 \
from the current context") gives the agent nothing it can act on. If a \
reference cannot be resolved from what you were given, write what IS known and \
leave the unresolvable part out rather than pointing at it.

Markdown, exactly this skeleton:

{_SKELETON}

- Output ONLY the brief: no preamble, no code fence, no quotes.
- `## Task` is mandatory; every other section is OPTIONAL. Omit any you cannot \
ground rather than padding it.

DESCRIBING is not INVENTING, and the difference decides how good this brief is:
- INVENTING is forbidden: a requirement, constraint, boundary or success \
criterion the user did not state and the workspace does not establish. `## Done \
when` holds only what the user asked for or what the repository itself \
determines (its test command, its lint gate); `## Scope` only a line the user \
actually drew. Omit either otherwise.
- DESCRIBING is wanted, in detail: what the code in the outlines does, which \
function is called from where, what the workspace profile and house rules \
impose. That is context you were handed and the agent would otherwise rediscover.
- Aim for {TARGET_MIN_CHARS}-{TARGET_MAX_CHARS} characters, never over \
{MAX_BODY_CHARS}. Under 800 you have dropped context you were given. Length \
comes from concrete file, symbol and behaviour detail - never from filler, \
restatement, hedging or dictated steps.
- `@path` references only inside `## Key files`, only from the candidate list, \
each naming the symbol that matters ("`_fuse_ranked()`, which merges the ranked \
lists before the relevance gate" beats "the ranking pipeline"). Never end the \
prompt on an `@path` or `/command` - a trailing reference holds the agent's \
completion popup open and the prompt is never submitted.
- ENGLISH, whatever language the user spoke.
- Preserve every constraint, file, symbol and intent expressed; drop speech \
artefacts and the clause addressing the agent by name.

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
- Specify the OUTCOME completely, so the agent can work start to finish without \
coming back with questions: the behaviour that must exist afterwards, how it \
should behave when things go wrong, and any constraint it has to respect. \
Complete means the agent lacks no context - not that you dictated the steps.
- `## How it works today` is close to mandatory here: the function the change \
lands in, what it currently does, and who calls it - the agent cannot fit a \
change into code it has to find first. Give it the MAP, not every path; the \
helpers it will meet on its own do not need listing.
- `## Scope` only when the user actually drew a line. Do not fill it with \
generic don'ts - "no unrequested refactors", "no speculative abstractions", "no \
surrounding cleanup" are things the agent already gets right, and spending the \
section on them costs the place a real boundary would have gone.
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
- State the task as they framed it, at the scope they framed it. Do not \
sharpen an open request into a specific one - if they were broad, the agent \
gets to decide where to start.
- Still describe the relevant code as it stands today. Being unsure which KIND \
of work this is is no reason to hand over less context.
- If they described a problem rather than asking for a change, say that the \
deliverable is the assessment.\
""",
}


def system_prompt(kind: str) -> str:
    """The composer's system prompt for one task kind."""
    return f"{_SHARED_RULES}\n\n{_KIND_RULES.get(kind, _KIND_RULES[KIND_NEUTRAL])}"


def attachment_block(attachments: list) -> str:
    """The dropped files' contents, laid out for the writer. ``""`` when none.

    Each entry is a ``drop_analysis.DropAnalysis``. The description of a
    screenshot and the text of a document are the whole reason the drop was
    analysed: without them the writer can only say "the file the user dropped",
    which is exactly the empty prompt this feature exists to prevent.

    The reference travels WITH the content so the writer can cite the file the
    agent will open, rather than inventing a path for a picture that only exists
    as a description.
    """
    usable = [a for a in attachments or [] if getattr(a, "name", "")]
    if not usable:
        return ""
    parts: list[str] = []
    for item in usable:
        kind = getattr(item, "kind", "") or "file"
        header = f'<attachment name="{item.name}" kind="{kind}" reference="{item.reference}">'
        if item.detail and getattr(item, "described_by", "") == "vision":
            body = f"Description of what this image shows:\n{item.detail}"
        elif item.detail:
            body = f"Contents:\n{item.detail}"
        else:
            body = item.note or "No content could be read from this file."
        parts.append(f"{header}\n{body}\n</attachment>")
    return "\n\n".join(parts)


# What the writer must do with a dropped file, appended only when there is one.
# Stated as its own rule set rather than folded into the shared rules: on a
# prompt with no attachment these lines are noise, and a rule that applies to
# nothing teaches the model that the rules are approximate.
_ATTACHMENT_RULES = """\
The user DROPPED one or more files into this instruction, and their contents \
are given above. Specific to that:
- Carry the substance across. The agent may not be able to open an image at \
all, so the description is its only view of it: put what the image actually \
shows into `## How it works today` or `## Task`, in detail, rather than \
writing "the screenshot the user dropped".
- Quote the exact strings. Error messages, stack traces, labels and code from \
an attachment go into the prompt VERBATIM — those are what the agent will \
search the repository for, and a paraphrase cannot be grepped.
- Reference the file as well, using the reference given in its attachment tag, \
so an agent that CAN open it does. The description is the floor, not a \
replacement.
- The attachment is context, not the task. What the user asked for still \
decides what the prompt asks the agent to do.\
"""


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
    attachments: list | None = None,
    conversation: Sequence[tuple[str, str]] | None = None,
) -> str:
    """Everything the composer knows, laid out for one model call.

    Longform data first, the request last: the documentation measures up to a
    30 % quality gain from putting the query at the end for multi-document
    inputs, and the file outlines are exactly that.

    ``conversation`` is the handful of turns the instruction came out of, and it
    sits directly in front of the instruction rather than with the longform
    data: it is not reference material, it is what the sentence's pronouns and
    ordinals point AT. Without it "above all points two and three" is
    unresolvable and travels into the brief as a pointer the agent cannot
    follow (live 2026-07-29).
    """
    parts: list[str] = []

    if profile_lines:
        parts.append("WORKSPACE\n" + "\n".join(profile_lines))
    if house_rules:
        parts.append("HOUSE RULES OF THIS REPOSITORY\n" + house_rules)

    dropped = attachment_block(attachments or [])
    if dropped:
        parts.append(
            "FILES THE USER DROPPED (already saved where the agent can open "
            "them; an image was described by a model that could see it)\n" + dropped
        )
        parts.append(_ATTACHMENT_RULES)

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

    spoken = conversation_module.render(conversation or ())
    if spoken:
        parts.append(
            "THE CONVERSATION THIS CAME OUT OF (oldest first; the agent will "
            "never see any of it, so resolve every back-reference against it)\n"
            + spoken
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


def render_fallback(
    instruction: str,
    files: list[str],
    attachments: list | None = None,
) -> str:
    """The deterministic prompt: same skeleton, no model involved.

    Used whenever no writer model is reachable. It is always better than the
    raw transcript, so the feature never depends on a provider being up.

    Dropped files are carried here as well, and that is the load-bearing half
    for a downloader with no writing model: the screenshot was still described
    (vision and prompt-writing are separate providers, and having one without
    the other is ordinary), so the description belongs in the prompt whether or
    not a model wrote the prose around it.
    """
    task = " ".join((instruction or "").split())
    usable = [a for a in attachments or [] if getattr(a, "name", "")]
    if not task and not usable:
        return ""
    out = [f"## Task\n{task}"] if task else []

    if usable:
        blocks: list[str] = []
        for item in usable:
            head = f"### {item.name}"
            if item.reference:
                # Plain hyphen, like the rest of the agent-facing text here: this
                # string is TYPED into a terminal, and a pane's encoding is not
                # ours to assume.
                head += f" - {item.reference}"
            if item.detail and getattr(item, "described_by", "") == "vision":
                blocks.append(f"{head}\nWhat this image shows:\n\n{item.detail}")
            elif item.detail:
                blocks.append(f"{head}\nContents:\n\n```\n{item.detail}\n```")
            else:
                blocks.append(f"{head}\n{item.note or 'No content could be read.'}")
        out.append("## Dropped files\n" + "\n\n".join(blocks))

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
    "attachment_block",
    "ends_on_reference",
    "looks_like_brief",
    "looks_truncated",
    "render_fallback",
    "system_prompt",
    "user_block",
]
