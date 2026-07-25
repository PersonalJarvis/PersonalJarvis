"""The context block that turns focus mode into an actual coding mode.

When the user flips the Agentic IDE into focus mode, every turn should be
answered *inside* the open workspace: Jarvis should know which folder is open,
what kind of codebase it is, which skills that repo defines, which agents are
running in which pane, and what each of them last printed. Without that, "is
Mika stuck?" is unanswerable and the assistant guesses — the failure mode this
block exists to prevent.

Cost discipline (AP-9 / AP-26): building the block touches nothing but
in-memory state — the session's cached project profile and each terminal's ring
buffer. No disk read, no subprocess, no network. The project profile was
computed ONCE when the session started, precisely so this path stays free.

The block is written the way the wiki-context block is: it states what it is and
what it is for, so the model treats it as situational awareness rather than as
an instruction to narrate the terminals unprompted.
"""
from __future__ import annotations

import time

# Per-terminal output shown in the block. Enough to say what an agent is doing,
# small enough that ten panes do not crowd out the conversation.
_LINES_PER_TERMINAL = 6
_MAX_CHARS = 4000

_HEADER = (
    "[AGENTIC IDE — focused coding mode is ON]\n"
    "You are assisting inside a live coding workspace that the user has open in "
    "the app. The facts below are the current state of that workspace: the "
    "folder, what kind of project it is, and the coding agents running in named "
    "terminals. Use it to answer questions about the workspace and its agents "
    "concretely (by name), and to decide what to send to which terminal. It is "
    "context, not a script — do not recite it back unprompted."
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
