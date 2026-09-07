"""What a caller may hand the tool-use loop: steering, phases, verification.

The loop is one turn's agentic cycle — gather context, act, verify, loop
until the answer holds. Three of those four are the loop's own; the fourth,
verification, and the person's ability to steer while it runs, belong to
whoever owns the conversation. So they arrive as ONE optional object:

* ``drain_steer`` — messages the person sent WHILE the turn ran. The loop
  folds them into the next round as user text, so a running job is redirected
  instead of being restarted (Grok Bot: "send a new message to interrupt work
  in progress").
* ``verify`` — the check that runs when the loop would otherwise finish: it
  judges the answer against the request and the log of tools that actually
  ran, and may send the turn back for one revision.
* ``on_phase`` — what the turn is doing right now (``gather`` / ``act`` /
  ``verify``), for the timeline and the island.

``LoopControl`` is ``None`` on every other path (voice, scheduled tasks,
missions), and those turns stay byte-identical. Stdlib only: this module
imports nothing from ``jarvis`` so the loop stays cheap to import.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

__all__ = ["Ask", "LoopControl", "ToolRecord", "VerifyOutcome", "VerifyRequest"]

#: ``(system, user) -> raw text`` — one tool-less question on the turn's OWN
#: brain, so a verifier needs no provider of its own (AP-6/AP-21).
Ask = Callable[[str, str], Awaitable[str]]

#: Phases a turn reports. ``gather`` opens every round (the model reads what
#: it has), ``act`` runs when tools are about to execute, ``verify`` when the
#: answer is being checked.
PHASE_GATHER = "gather"
PHASE_ACT = "act"
PHASE_VERIFY = "verify"


@dataclass(frozen=True, slots=True)
class ToolRecord:
    """One tool call that really happened — the only evidence a verifier gets.

    Text in an answer is not evidence that anything ran; this log is.
    """

    name: str
    ok: bool
    blocked: bool = False
    #: First ~200 characters of the result (or the error), for the verifier.
    preview: str = ""


@dataclass(frozen=True, slots=True)
class VerifyRequest:
    """What the verifier judges: the ask, the answer, and what actually ran."""

    user_text: str
    answer: str
    tool_log: tuple[ToolRecord, ...]
    #: 0 on the first pass, 1 after one revision, …
    attempt: int = 0


@dataclass(frozen=True, slots=True)
class VerifyOutcome:
    """``accepted`` ends the turn; otherwise ``instruction`` drives one revision."""

    accepted: bool
    instruction: str = ""
    detail: str = ""


@dataclass(slots=True)
class LoopControl:
    """The hooks one turn may bring. Every field is optional."""

    #: Returns (and clears) the messages the person sent while the turn ran.
    drain_steer: Callable[[], Sequence[str]] | None = None
    #: The verification pass; it gets the turn's own brain as ``Ask``.
    verify: Callable[[VerifyRequest, Ask], Awaitable[VerifyOutcome]] | None = None
    #: ``(phase, detail)`` — reported once per change, never per token.
    on_phase: Callable[[str, str], Awaitable[None]] | None = None
    #: How many revisions a verdict may ask for before the turn ends anyway.
    max_verify: int = 2
