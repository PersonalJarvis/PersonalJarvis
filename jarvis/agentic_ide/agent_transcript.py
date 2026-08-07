"""What the agent actually said, read from the CLI's own record of it.

## Why not read the screen

A pane shows a TUI, and a TUI is a picture. It repaints rows in place, wraps to
the window it happened to have, draws its own logo, and prints spinners and
status lines that mean nothing an hour later. Anything that turns that picture
back into a conversation is guessing — and it guesses wrong in the way that
matters most: a line it does not recognise is either dropped (so real output
vanishes) or kept (so ``Philosophising…`` and a half-drawn banner become part of
the transcript). Both were on screen at once in the report this module answers.

But the conversation is not only on the screen. Every coding CLI worth resuming
already writes it down — that record is what :mod:`.agent_sessions` points a
resume handle at — and it is *structured*: roles are declared, tool calls carry
their name and their arguments, prose is prose. So this reads THAT, and the
guessing stops.

## What it produces

One shape for every CLI: an ordered list of :class:`Turn`, each a role, its
text, and the :class:`Step` s the agent took inside it. Callers never branch on
which product wrote the file (AP-21) — they ask for a transcript and get either
one or ``None``, and ``None`` means "this CLI keeps no readable record", which
the surface answers by showing the live pane instead. A CLI added later gets the
same deal for free by registering an adapter.

## Bounds

Two, both load-bearing. A long-running session's file reaches megabytes, so the
read starts from the END of the file and walks backwards only as far as it must
— a conversation view shows the last stretch, and paying disk for the first
thousand turns to render the last ten is how a chat surface becomes slower the
longer it is used. And each block of text is capped, because a tool result can
be a whole file and this is a conversation, not a file viewer.

Cross-platform: paths come from :mod:`.agent_sessions`, everything is read as
UTF-8 with replacement, and nothing here is OS-specific.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from . import agent_sessions

#: How many turns a transcript answers with. A conversation view is a window on
#: the recent past; scrolling further back is a different feature with a
#: different cost, and pretending otherwise makes every open slower.
MAX_TURNS = 60

#: Per-block ceiling. A tool result can be an entire file; a chat surface shows
#: what a person reads, and the pane behind it still holds the raw output.
MAX_TEXT = 4000

#: How much of the file's tail may be read to find those turns. Generous enough
#: that MAX_TURNS is reached in one pass for any normal session, bounded so a
#: pathological file cannot pull megabytes into memory.
MAX_TAIL_BYTES = 2_000_000

#: Read granularity when walking backwards from the end of the file.
_CHUNK = 256 * 1024


@dataclass(frozen=True, slots=True)
class Step:
    """One thing the agent DID between two things it said.

    ``target`` is the single argument worth reading at a glance — the path it
    edited, the command it ran — and ``detail`` is everything else, for the
    reader who opens the step. Both may be empty: a tool nobody has taught this
    module about still shows up under its own name rather than disappearing,
    which is the fail-safe direction.
    """

    tool: str
    target: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "target": self.target, "detail": self.detail}


@dataclass(slots=True)
class Turn:
    """One side of the conversation speaking once, and what it did while doing so."""

    role: str
    text: str = ""
    steps: list[Step] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "text": self.text,
            "steps": [s.to_dict() for s in self.steps],
        }


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #


def _clip(text: str) -> str:
    """``text`` within :data:`MAX_TEXT`, keeping both ends when it is not.

    Both ends rather than the head: a truncated tool result's last lines are
    where the error is, and a truncated answer's last lines are where it says
    what it concluded.
    """
    value = str(text or "").strip()
    if len(value) <= MAX_TEXT:
        return value
    half = MAX_TEXT // 2
    return f"{value[:half]}\n\n[…]\n\n{value[-half:]}"


def _tail_lines(path: Path, *, max_bytes: int = MAX_TAIL_BYTES) -> list[str]:
    """The last lines of ``path``, without reading what comes before them.

    Walks backwards in chunks and stops at ``max_bytes``. The first line of the
    result may be a fragment — the caller parses per line and a fragment simply
    fails to parse, which is the correct outcome for half a JSON object.
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        logger.debug("Agent transcript: cannot stat {}: {}", path.name, exc)
        return []
    want = min(size, max_bytes)
    try:
        with path.open("rb") as handle:
            handle.seek(size - want)
            blob = handle.read(want)
    except OSError as exc:
        logger.warning("Agent transcript: cannot read {}: {}", path.name, exc)
        return []
    text = blob.decode("utf-8", "replace")
    return text.splitlines()


def _rows(path: Path) -> list[dict[str, Any]]:
    """Every JSON object in the tail of ``path``, oldest first.

    A line that does not parse is skipped in silence, and that IS the handling:
    the first line of a tail read is usually half an object, and a CLI writing
    its file while this reads it will always produce one. Nothing is lost — the
    next poll sees the complete line.
    """
    out: list[dict[str, Any]] = []
    for line in _tail_lines(path):
        stripped = line.strip()
        if not stripped or not stripped.startswith("{"):
            continue
        try:
            payload = json.loads(stripped)
        except (ValueError, TypeError):
            # Not a JSON line — transcripts interleave plain text with JSONL,
            # so skipping is the parse contract, not a swallowed failure.
            continue
        if isinstance(payload, dict):
            out.append(payload)
    return out


#: Argument names worth showing as a step's headline, best first.
#:
#: Matched by NAME rather than by tool, so a tool this module has never heard of
#: still gets a readable line as long as it names its arguments the way every
#: other tool does. That is what keeps this from being a table of one product's
#: tool names — a thing that would be wrong by the next release.
_TARGET_KEYS = (
    "file_path",
    "path",
    "notebook_path",
    "command",
    "pattern",
    "query",
    "url",
    "prompt",
    "description",
    "subject",
)


def _headline(payload: Any) -> tuple[str, str]:
    """``(target, detail)`` for a tool call's arguments.

    The target is one line — a path, a command — because a step is read at a
    glance and a wall of JSON is not read at all. The detail is the whole call,
    for whoever opens it.
    """
    if not isinstance(payload, dict):
        text = str(payload or "").strip()
        return (text.splitlines()[0] if text else "", _clip(text))
    target = ""
    for key in _TARGET_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            target = " ".join(value.split())
            break
    try:
        detail = json.dumps(payload, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        # Unserializable args still get shown — str() is the honest fallback.
        detail = str(payload)
    return (target[:200], _clip(detail))


#: An XML-ish block, opening tag through closing tag, or a lone tag.
#:
#: Not a list of the tags any one product uses — that list would be wrong by
#: the next release. The SHAPE is the signal: a coding CLI wraps everything it
#: injects into a user turn on the user's behalf in one of these, and a person
#: typing at a prompt does not write in tags.
_MACHINE_BLOCK = re.compile(
    r"<([a-zA-Z][\w:-]*)\b[^>]*>.*?</\1\s*>|<[a-zA-Z][\w:-]*\b[^>]*/?>",
    re.DOTALL,
)


def _spoken(text: str) -> str:
    """What a PERSON said in a user record, or "" when nobody did.

    A CLI writes far more user records than the user does: tool results, session
    notifications, command echoes, reminders injected by the harness. They are
    all real records with ``role: user``, and rendering them is how a
    conversation view ends up showing ``<task-notification>`` in the reader's own
    voice — which it did.

    The test is subtractive rather than a denylist: strip the machine blocks and
    see whether a sentence remains. Nothing left means nobody spoke. Something
    left means they did, and that something is exactly what they typed — which
    is also the right answer for the common case of a person adding a line above
    a pasted block.
    """
    remainder = _MACHINE_BLOCK.sub(" ", str(text or ""))
    return remainder.strip()


def _append(turns: list[Turn], role: str, text: str) -> None:
    """Add ``text`` to the open turn of ``role``, or open a new one.

    Consecutive blocks from one side are one turn: a CLI writes an answer as
    several records — a thought, a sentence, a tool call, another sentence — and
    rendering each as its own bubble is how a single reply becomes six.
    """
    body = _clip(_spoken(text) if role == "user" else text)
    if not body:
        return
    if turns and turns[-1].role == role:
        turns[-1].text = _clip(f"{turns[-1].text}\n\n{body}" if turns[-1].text else body)
        return
    turns.append(Turn(role=role, text=body))


def _step(turns: list[Turn], step: Step) -> None:
    """Record a step against the open assistant turn, opening one if needed."""
    if not turns or turns[-1].role != "assistant":
        turns.append(Turn(role="assistant"))
    turns[-1].steps.append(step)


# --------------------------------------------------------------------------- #
# Claude Code — ~/.claude/projects/<folder>/<session-id>.jsonl
# --------------------------------------------------------------------------- #


def _claude_file(session_id: str, home: Path | None) -> Path | None:
    # Where a CLI keeps its history is `agent_sessions`' knowledge, not this
    # module's — the two answer questions about the same files and a second copy
    # of the layout would drift from the first. Same package, same layer.
    projects = agent_sessions._claude_home(home) / "projects"
    if not projects.is_dir():
        return None
    return next(projects.glob(f"*/{session_id}.jsonl"), None)


def _claude_turns(session_id: str, home: Path | None) -> list[Turn] | None:
    path = _claude_file(session_id, home)
    if path is None:
        return None
    turns: list[Turn] = []
    for row in _rows(path):
        kind = str(row.get("type") or "")
        if kind not in ("user", "assistant"):
            # Modes, snapshots, attachments, titles — the CLI's own bookkeeping.
            continue
        message = row.get("message")
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        content = message.get("content")
        # A plain string is the short form of one text block.
        if isinstance(content, str):
            if role in ("user", "assistant"):
                _append(turns, role, content)
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = str(block.get("type") or "")
            if btype == "text" and role in ("user", "assistant"):
                _append(turns, role, str(block.get("text") or ""))
            elif btype == "tool_use":
                target, detail = _headline(block.get("input"))
                _step(
                    turns,
                    Step(
                        tool=str(block.get("name") or "tool"),
                        target=target,
                        detail=detail,
                    ),
                )
            # `thinking` is deliberately dropped: it is the model reasoning with
            # itself, it is not addressed to the reader, and it is the single
            # largest block in most transcripts. `tool_result` likewise — it
            # arrives as a USER record, and rendering it would put the agent's
            # own tool output in the user's voice, which is exactly the confusion
            # the screen-reading version produced.
    return turns


# --------------------------------------------------------------------------- #
# Codex — ~/.codex/sessions/<y>/<m>/<d>/rollout-*-<session-id>.jsonl
# --------------------------------------------------------------------------- #


def _codex_file(session_id: str, home: Path | None) -> Path | None:
    sessions = agent_sessions._codex_home(home) / "sessions"
    if not sessions.is_dir():
        return None
    return next(sessions.glob(f"*/*/*/rollout-*{session_id}*.jsonl"), None)


def _codex_turns(session_id: str, home: Path | None) -> list[Turn] | None:
    path = _codex_file(session_id, home)
    if path is None:
        return None
    turns: list[Turn] = []
    for row in _rows(path):
        if str(row.get("type") or "") != "response_item":
            # `event_msg` duplicates the messages it announces, and the rest is
            # session metadata. Reading both would print every answer twice.
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        ptype = str(payload.get("type") or "")
        if ptype in ("function_call", "local_shell_call", "custom_tool_call"):
            arguments = payload.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except (ValueError, TypeError):
                    # Keep the raw string — _headline renders either shape.
                    pass
            target, detail = _headline(arguments)
            _step(
                turns,
                Step(
                    tool=str(payload.get("name") or payload.get("type") or "tool"),
                    target=target,
                    detail=detail,
                ),
            )
            continue
        if ptype != "message":
            continue
        role = str(payload.get("role") or "")
        # `developer` is the harness talking to the model — instructions, not
        # conversation. Showing it would open every chat with our own preamble.
        if role not in ("user", "assistant"):
            continue
        content = payload.get("content")
        if isinstance(content, str):
            _append(turns, role, content)
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and str(block.get("type") or "").endswith("text"):
                _append(turns, role, str(block.get("text") or ""))
    return turns


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #

#: Which CLIs keep a record this module can read. Absence is not a failure — a
#: CLI without an entry degrades to "watch the live pane", honestly and without
#: an error (CLAUDE.md §3).
_READERS: dict[str, Callable[[str, Path | None], list[Turn] | None]] = {
    "claude": _claude_turns,
    "codex": _codex_turns,
}


def can_read(agent: str) -> bool:
    """Does this CLI keep a conversation this module knows how to read?"""
    return str(agent or "").strip().lower() in _READERS


def read(agent: str, session_id: str, *, home: Path | None = None) -> list[Turn] | None:
    """The recent conversation of one session, oldest turn first.

    ``None`` — never an exception and never a lie — when this CLI keeps no
    readable record, when the session has no file yet (a pane that has just
    started), or when the file cannot be read. The caller shows the live pane in
    that case, which is always correct and never empty.
    """
    reader = _READERS.get(str(agent or "").strip().lower())
    if reader is None or not str(session_id or "").strip():
        return None
    try:
        turns = reader(session_id.strip(), home)
    except OSError as exc:
        logger.warning("Agent transcript: {} session {} unreadable: {}", agent, session_id, exc)
        return None
    if turns is None:
        return None
    # Drop turns that ended up carrying nothing — a tool-result-only record, a
    # message whose every block was filtered.
    live = [t for t in turns if t.text or t.steps]
    return live[-MAX_TURNS:]


__all__ = [
    "MAX_TEXT",
    "MAX_TURNS",
    "Step",
    "Turn",
    "can_read",
    "read",
]
