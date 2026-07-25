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

# Long enough for a real brief with five sections; short enough that the pane's
# input box does not become the whole screen. MAX_PROMPT_CHARS (4000) remains
# the hard transport cap above this.
MAX_BODY_CHARS = 3000

_SKELETON = """\
## Task
<one to three imperative sentences: what to do>

## Why this matters
<the intent behind the request - ONLY when it is known or clearly derivable>

## Key files
- `@path/to/file` - what it contributes to this task
- `@path/to/other` - what it contributes

## Scope
<what is in, and what is explicitly out>

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
- Invent nothing. No requirement, constraint, scope or success criterion the \
user did not state and the workspace does not establish. `## Done when` may \
only contain what the user asked for or what the repository itself determines \
(its test command, its lint gate). If neither gives you anything, omit it.
- Reference files with `@path` inside a `## Key files` list item, using ONLY \
paths from the candidate list you are given, each with a short reason. Omit \
the section when no candidate is clearly relevant.
- Never end the prompt on an `@path` or a `/command`: a trailing reference \
leaves the agent's completion popup open and the prompt is never submitted.
- Write in ENGLISH, whatever language the user spoke. The agent works in a \
repository whose code, comments and commits are English.
- Preserve every constraint, file, symbol and intent the user expressed. \
Remove speech artefacts: filler words, false starts, self-corrections, and the \
clause addressing the agent by name.
- Keep the whole prompt under {MAX_BODY_CHARS} characters.
- Do not ask the agent to narrate, echo or explain its internal reasoning.
- Do not ask the agent to verify, re-check or double-check its own work \
afterwards. It already does that unprompted, and asking for it makes the \
result worse, not better.\
"""

# Per-kind guardrails. Short on purpose: a short coherent set is followed, a
# long one is averaged.
_KIND_RULES: dict[str, str] = {
    KIND_IMPLEMENT: """\
This is an IMPLEMENTATION task. Specific to it:
- Give the complete specification up front, so the agent can work start to \
finish without coming back with questions.
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
- Name what to weigh the code against when the workspace establishes it: the \
repository's own conventions, its anti-pattern register, its tests.
- The deliverable is the findings. Do not ask for fixes in the same prompt.\
""",
    KIND_INVESTIGATE: """\
This is an INVESTIGATION task. Specific to it:
- The deliverable is a diagnosis: the cause, the evidence for it, and what \
would confirm it. Say so explicitly.
- Say that the agent should report the finding and stop, and should not apply \
a fix before the user has seen it.
- Carry across every symptom, error message, timing and reproduction detail \
the user gave. Those are the evidence; do not summarise them away.\
""",
    KIND_QUESTION: """\
This is a QUESTION. Specific to it:
- The deliverable is an answer grounded in the code, not a change. Say that \
the agent should read the relevant files before answering and should not \
change anything.
- Keep it short. A question rarely needs `## Scope` or `## Done when`; omit \
them unless the user set a real boundary.\
""",
    KIND_NEUTRAL: """\
The kind of work is not clearly determined. Stay with what the user said:
- State the task as they framed it, and bound the scope to it - no \
surrounding cleanup, no unrequested refactors.
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


__all__ = [
    "MAX_BODY_CHARS",
    "ends_on_reference",
    "render_fallback",
    "system_prompt",
    "user_block",
]
