"""The context block that turns focus mode into an actual coding mode.

When the user flips the Agentic IDE into focus mode, every turn should be
answered *inside* the open workspace: Jarvis should know which folder is open,
what kind of codebase it is, which skills that repo defines, which agents are
running in which pane, and what each of them last printed. Without that, "is T2
stuck?" is unanswerable and the assistant guesses — the failure mode this block
exists to prevent.

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
# small enough that ten panes do not crowd out the conversation. Past
# _CROWDED_AT panes the per-pane share shrinks: the block is hard-capped at
# _MAX_CHARS, and six lines each meant the cap silently dropped the LAST panes
# entirely — three lines each keeps every pane visible AND costs fewer of the
# ~1 100 uncacheable input tokens this block adds to every turn.
_LINES_PER_TERMINAL = 6
_LINES_PER_TERMINAL_CROWDED = 3
_CROWDED_AT = 5
_MAX_CHARS = 4500

_HEADER = (
    "[AGENTIC IDE — focused coding mode is ON]\n"
    "You are the user's agentic-coding partner for the one repository below. "
    "Coding agents are already running in numbered terminals in front of the "
    "user; your job is to think WITH them about this codebase and to drive "
    "those agents — not to write the code yourself, and not to start background "
    "workers.\n"
    "\n"
    "Each terminal is called T plus its place in the grid, left to right: T1, "
    "T2, T3. The user says that number, and so do you.\n"
    "\n"
    "How to behave while this mode is on:\n"
    "- When the user tells a terminal to do something (\"tell T1 to …\", "
    "\"T2 soll …\", \"prompt terminal three\", \"let the second one "  # i18n-allow: quoted addressing example
    "refactor …\"), send it to THAT terminal with "
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
    "T1 know\" while nothing reached T1 is the worst failure this mode has: "
    "the user walks away believing an agent is working, and only finds the idle "
    "terminal later. If the work did not go out, say plainly that it did not and "
    "why — an honest \"I could not reach that terminal\" is always better than a "
    "confident sentence that turns out to be false.\n"
    "- A pane listed below as STILL BEING WRITTEN has received NOTHING yet. The "
    "prompt writer takes 10-30 seconds, and the earlier prompts counted for that "
    "pane are OLD ones, not this one. Say the work is still going out — never "
    "that it arrived, and never that the agent has started.\n"
    "- Brainstorming, architecture, and 'what should we do next' are answered "
    "inline, against this codebase, and you may propose which terminal should "
    "take which part.\n"
    "- Say the terminal's number out loud in your answers (\"T2 is on it\"), so "
    "the user always knows which pane is doing what.\n"
    "\n"
    "The facts below are the live state of that workspace. It is context, not a "
    "script — do not recite it back unprompted."
)


def _terminal_block(  # noqa: ANN001 - Terminal, avoid import cycle
    term,
    tail_lines: int = _LINES_PER_TERMINAL,
    *,
    writing_for_s: float | None = None,
) -> list[str]:
    status = term.status
    bits = [f"{term.name} ({term.display_name}) — {status}"]
    if writing_for_s is not None:
        # Stated before the receipt count on purpose: the counted prompts are
        # what the model turned into "I have prompted T5" while this one was
        # still being written (live 2026-08-13 11:20:12).
        bits.append(
            f"A BRIEF IS STILL BEING WRITTEN for it ({int(writing_for_s)}s so "
            f"far) — nothing has reached {term.name} yet"
        )
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
    tail = term.transcript.tail(tail_lines)
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
    visible = session.contextual_terminal()
    if visible is not None:
        parts.append(
            f"Chat view is on screen with {visible.name} as the one visible terminal. "
            f"Deictic references such as 'this terminal', 'the terminal here', "
            f"or 'what is it doing?' mean {visible.name}, unless the user "
            "explicitly names another call-sign."
        )
        parts.append("")
    if session.terminals:
        tail_lines = (
            _LINES_PER_TERMINAL
            if len(session.terminals) < _CROWDED_AT
            else _LINES_PER_TERMINAL_CROWDED
        )
        # Which panes have a brief ON THE WAY. The one workspace fact that
        # exists nowhere else: a pane with a 20 s composition running looks
        # exactly like an idle pane that was prompted an hour ago, and the model
        # answered from the older receipt.
        try:
            from .fanout import in_flight_briefs

            writing = dict(in_flight_briefs(session))
        except Exception:  # noqa: BLE001 - a missing fact never costs the block
            writing = {}
        parts.append(f"Terminals in this workspace ({len(session.terminals)}):")
        for term in session.terminals:
            parts.extend(
                _terminal_block(
                    term, tail_lines, writing_for_s=writing.get(term.name)
                )
            )
    else:
        parts.append("No terminals are open in this workspace yet.")

    block = "\n".join(parts)
    if len(block) > max_chars:
        block = block[: max_chars - 1] + "…"
    return block


__all__ = ["focus_context_block"]
