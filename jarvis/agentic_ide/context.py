"""The context block that turns focus mode into an actual coding mode.

When the user flips the Agentic IDE into focus mode, every turn should be
answered *inside* the open workspace: Jarvis should know which folder is open,
what kind of codebase it is, which skills that repo defines, which agents are
running in which pane, and what each of them last printed. Without that, "is
Mika stuck?" is unanswerable and the assistant guesses — the failure mode this
block exists to prevent.

Two things live here, and the distinction matters:

* **A role directive.** Focus mode is not a hint, it is a different assistant:
  an agentic-coding partner for this one repository. It plans work with the
  user, decides which pane should do what, and hands the work over — it does not
  do the coding itself and does not start invisible background agents. Stating
  that explicitly is what stopped the live 2026-07-25 failure, where "let Kai do
  a deep dive" dispatched a background mission while Kai sat idle. The
  deterministic guards (``intent.owns_turn``, consulted by the router's
  force-spawn check and the spawn gate) are the enforcement; this text is what
  makes the model *want* the right thing in the first place, so a phrasing the
  regex misses still lands correctly.
* **The facts.** Folder, stack, branch, panes, and what each pane last printed.

Cost discipline (AP-9 / AP-26): building the block touches nothing but
in-memory state — the session's cached project profile and each terminal's ring
buffer. No disk read, no subprocess, no network. The project profile was
computed ONCE when the session started, precisely so this path stays free.
"""
from __future__ import annotations

import time

# Per-terminal output shown in the block. Enough to say what an agent is doing,
# small enough that ten panes do not crowd out the conversation.
_LINES_PER_TERMINAL = 6
_MAX_CHARS = 4500

_HEADER = (
    "[AGENTIC IDE — focused coding mode is ON]\n"
    "You are the user's agentic-coding partner for the one repository below. "
    "Coding agents are already running in named terminals in front of the user; "
    "your job is to think WITH them about this codebase and to drive those "
    "agents — not to write the code yourself, and not to start background "
    "workers.\n"
    "\n"
    "How to behave while this mode is on:\n"
    "- When the user tells a named terminal to do something (\"tell Kai to …\", "
    "\"Mika soll …\", \"let Nova refactor …\"), send it to THAT terminal with "
    "the agentic-ide-prompt function. That is the whole point of this mode. "
    "NEVER spawn a background agent for work aimed at a terminal, and never "
    "answer with what you WOULD have sent — send it.\n"
    "- Hand the work over in the USER's words — everything they asked for, "
    "every constraint and file they named, nothing invented and nothing "
    "summarised away. Do not write the brief yourself: a prompt writer that "
    "has read this repository turns what you pass on into the briefed task "
    "with @path references, and a one-line headline you composed instead "
    "REPLACES that knowledge of the code with your guess at it. Passing the "
    "instruction along whole is the value you add here.\n"
    "- When the user asks what an agent is doing, read it with "
    "agentic-ide-terminal-report and answer from what that terminal actually "
    "printed. Never guess, never take a screenshot — the terminals are readable "
    "directly.\n"
    "- NEVER claim you sent, forwarded, passed on or told a terminal anything "
    "unless a function call in THIS turn actually did it. Saying \"I have let "
    "Alex know\" while nothing reached Alex is the worst failure this mode has: "
    "the user walks away believing an agent is working, and only finds the idle "
    "terminal later. If the work did not go out, say plainly that it did not and "
    "why — an honest \"I could not reach that terminal\" is always better than a "
    "confident sentence that turns out to be false.\n"
    "- Brainstorming, architecture, and 'what should we do next' are answered "
    "inline, against this codebase, and you may propose which terminal should "
    "take which part.\n"
    "- Say the terminal's name out loud in your answers, so the user always "
    "knows who is doing what.\n"
    "\n"
    "The facts below are the live state of that workspace. It is context, not a "
    "script — do not recite it back unprompted."
)


def _terminal_block(term) -> list[str]:  # noqa: ANN001 - Terminal, avoid import cycle
    status = term.status
    bits = [f"{term.name} ({term.display_name}) — {status}"]
    if status == "live" and term.last_output_at:
        idle = max(0, int(time.time() - term.last_output_at))
        bits.append(f"last output {idle}s ago")
    if status == "exited" and term.exit_code is not None:
        bits.append(f"exit code {term.exit_code}")
    if status == "error" and term.error:
        bits.append(term.error)
    if term.prompts_sent:
        bits.append(f"{term.prompts_sent} prompt(s) sent from Jarvis")
    lines = [f"- {', '.join(bits)}"]
    if term.last_prompt:
        lines.append(f'  last prompt sent: "{term.last_prompt[:200]}"')
    tail = term.transcript.tail(_LINES_PER_TERMINAL)
    if tail:
        lines.append("  recent output:")
        lines.extend(f"    {line[:200]}" for line in tail)
    return lines


def focus_context_block(max_chars: int = _MAX_CHARS) -> str:
    """Workspace-awareness block for this turn, or "" when focus mode is off."""
    try:
        from .session import get_registry

        session = get_registry().session
    except Exception:  # noqa: BLE001 - never let awareness break a turn
        return ""
    if session is None or not session.focus_mode:
        return ""

    parts: list[str] = [_HEADER, ""]
    parts.extend(session.profile.summary_lines())
    parts.append("")
    if session.terminals:
        parts.append(f"Terminals in this workspace ({len(session.terminals)}):")
        for term in session.terminals:
            parts.extend(_terminal_block(term))
    else:
        parts.append("No terminals are open in this workspace yet.")

    block = "\n".join(parts)
    if len(block) > max_chars:
        block = block[: max_chars - 1] + "…"
    return block


__all__ = ["focus_context_block"]
