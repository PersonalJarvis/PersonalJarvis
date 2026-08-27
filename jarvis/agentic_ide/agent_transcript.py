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
from datetime import datetime
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
# the opening — the first thing a person said
# --------------------------------------------------------------------------- #
#
# A session's title is its first message, the way a chat's is. That message is
# at the HEAD of the file, which is the one part the tail readers above never
# look at — and reading it is the only way a pane keeps its name across a
# backend restart (the sent prompt is memory) and past the first screen of
# output (the typed echo scrolls out of the replay buffer). See
# :mod:`jarvis.agentic_ide.opening` for who asks and how often.

#: How much of the file's head is read to find that message. A CLI opens its
#: file with bookkeeping — snapshots, queue rows, the harness's own reminders —
#: and the person's first message follows within a few kilobytes; this bound
#: only keeps a pathological file from being read whole.
MAX_HEAD_BYTES = 512 * 1024


def _head_rows(path: Path, *, max_bytes: int = MAX_HEAD_BYTES) -> list[dict[str, Any]]:
    """Every JSON object in the head of ``path``, oldest first.

    The last line of a bounded read is usually half an object; it fails to parse
    and is skipped, which is the right outcome for a fragment.
    """
    try:
        with path.open("rb") as handle:
            blob = handle.read(max_bytes)
    except OSError as exc:
        logger.warning("Agent transcript: cannot read {}: {}", path.name, exc)
        return []
    out: list[dict[str, Any]] = []
    for line in blob.decode("utf-8", "replace").splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            payload = json.loads(stripped)
        except (ValueError, TypeError):
            # Not a JSON line — the parse contract, not a swallowed failure.
            continue
        if isinstance(payload, dict):
            out.append(payload)
    return out


def _text_of(content: Any) -> str:
    """The prose of one message body: a string, or its text blocks joined.

    Tool results, images and the rest of a body's blocks are not prose and are
    left out — a tool result arrives as a USER record, and quoting it would put
    the agent's own output in the person's voice.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and str(block.get("type") or "").endswith("text"):
            parts.append(str(block.get("text") or ""))
    return "\n".join(parts)


def _claude_first_user_text(session_id: str, home: Path | None) -> str | None:
    path = _claude_file(session_id, home)
    if path is None:
        return None
    for row in _head_rows(path):
        if str(row.get("type") or "") != "user":
            continue
        # A meta row is the CLI annotating the conversation (an image's size, a
        # slash command's echo); a sidechain is a sub-agent's conversation.
        # Neither is the person opening this session.
        if row.get("isMeta") or row.get("isSidechain"):
            continue
        message = row.get("message")
        if not isinstance(message, dict) or str(message.get("role") or "") != "user":
            continue
        spoken = _spoken(_text_of(message.get("content")))
        if spoken:
            return spoken
    return ""


def _codex_first_user_text(session_id: str, home: Path | None) -> str | None:
    path = _codex_file(session_id, home)
    if path is None:
        return None
    for row in _head_rows(path):
        if str(row.get("type") or "") != "response_item":
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict) or str(payload.get("type") or "") != "message":
            continue
        # `developer` is the harness; the person is `user`, and the harness
        # wraps what IT adds to a user message in tags `_spoken` strips.
        if str(payload.get("role") or "") != "user":
            continue
        spoken = _spoken(_text_of(payload.get("content")))
        if spoken:
            return spoken
    return ""


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

#: The same CLIs, read from the other end: the first thing the person said.
_OPENERS: dict[str, Callable[[str, Path | None], str | None]] = {
    "claude": _claude_first_user_text,
    "codex": _codex_first_user_text,
}


def can_read(agent: str) -> bool:
    """Does this CLI keep a conversation this module knows how to read?"""
    return str(agent or "").strip().lower() in _READERS


def first_user_text(agent: str, session_id: str, *, home: Path | None = None) -> str | None:
    """The first thing a PERSON said in one session — what opened it.

    ``None`` when this CLI keeps no readable record or the session has no file
    yet; ``""`` when the file is there but nobody has spoken in it so far. Both
    are transient for a pane that has just started, and neither is an error.
    Reads the head of the file only (:data:`MAX_HEAD_BYTES`); the tail readers
    above are for the conversation's recent past.
    """
    opener = _OPENERS.get(str(agent or "").strip().lower())
    if opener is None or not str(session_id or "").strip():
        return None
    try:
        return opener(session_id.strip(), home)
    except OSError as exc:
        logger.warning("Agent transcript: {} session {} unreadable: {}", agent, session_id, exc)
        return None


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


# --------------------------------------------------------------------------- #
# the conversation as agent-chat events
# --------------------------------------------------------------------------- #
#
# The turns above are a summary: prose and the names of the steps. The agent
# CHAT renders more than that — the thinking where it happened (or how long it
# took where the vendor redacts it), every tool call with its result, the
# tokens a turn cost — and it renders all of it from ONE vocabulary, the event
# log of :mod:`jarvis.agent_chat.events`. So a pane's transcript is offered in
# that vocabulary too: the same reducer that folds a chat session's log then
# folds a terminal's, and the Agentic IDE can put a running CLI on the chat
# stage without a second renderer that would drift from the first.
#
# The cut between turns is the person's own message, exactly as the chat's
# runners cut them: everything the agent did after one and before the next is
# one turn. Timestamps come from the records, so a thought's duration is the
# gap to whatever the agent wrote next, and a tool result is timed from its
# call. Nothing here guesses at a clock the file does not carry.


def _ts_ms(value: Any) -> int | None:
    """A record's ``timestamp`` as epoch milliseconds, or ``None``.

    Both shapes the CLIs write: ISO-8601 text (Claude Code, Codex) and a plain
    epoch number, in seconds or milliseconds. Anything else is not a time.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value * 1000) if value < 1e12 else int(value)
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        if raw.endswith(("Z", "z")):
            raw = raw[:-1] + "+00:00"
        try:
            return int(datetime.fromisoformat(raw).timestamp() * 1000)
        except ValueError:
            return None
    return None


def _summary(name: str, args: Any) -> str:
    """The one argument a tool call is read by — the same pick `_headline` makes."""
    target, _detail = _headline(args)
    return target or name


class _EventLog:
    """The agent-chat event log of one transcript, built in file order.

    ``live`` is what the caller knows and the file does not: whether the pane
    is still working. A file ends the same way whether the agent finished an
    hour ago or is thinking right now, so the last turn is closed as ``done``
    when the pane is idle and left running — its last thought announced as a
    live one — when it is not. The timeline then says "thinking…" for a pane
    that is, and "Done" for one that stopped, from the same file.
    """

    def __init__(self, *, provider: str, live: bool) -> None:
        self.events: list[dict[str, Any]] = []
        self.provider = provider
        self.live = live
        self.model = ""
        self.effort = ""
        self.turn_id: str | None = None
        self.turns = 0
        self.turn_started_ms = 0
        self.last_ms = 0
        self.error: str | None = None
        self._usage_by_message: dict[str, dict[str, int]] = {}
        self._usage: dict[str, int] = {}
        self._call_started: dict[str, int] = {}
        #: A finished thought waits for the NEXT record's timestamp, which is
        #: the only place its duration can be read from.
        self._thought: tuple[str, str, int] | None = None

    # ----------------------------------------------------------------- core
    def _emit(self, kind: str, payload: dict[str, Any], ts: int) -> None:
        self.last_ms = max(self.last_ms, ts)
        self.events.append(
            {"seq": len(self.events) + 1, "ts_ms": ts, "kind": kind, "payload": payload}
        )

    def _ensure_turn(self, ts: int) -> str:
        if self.turn_id is None:
            self.turns += 1
            self.turn_id = f"turn-{self.turns}"
            self.turn_started_ms = ts
            self._usage_by_message = {}
            self._usage = {}
            self.error = None
            self._emit(
                "turn_started",
                {
                    "turn_id": self.turn_id,
                    "provider": self.provider,
                    "model": self.model,
                    "effort": self.effort,
                    "runner": "cli",
                },
                ts,
            )
        return self.turn_id

    def _flush_thought(self, now: int) -> None:
        if self._thought is None:
            return
        text, message_id, started = self._thought
        self._thought = None
        self._emit(
            "reasoning",
            {
                "turn_id": self.turn_id,
                "message_id": message_id,
                "text": text,
                "duration_ms": max(0, now - started),
            },
            started,
        )

    def close_turn(self, ts: int, status: str = "done") -> None:
        """End the open turn, if any — a boundary reached or the agent stopped."""
        if self.turn_id is None:
            return
        self._flush_thought(ts)
        self._emit(
            "turn_finished",
            {
                "turn_id": self.turn_id,
                "status": status,
                "duration_ms": max(0, max(ts, self.last_ms) - self.turn_started_ms),
                "usage": dict(self._usage),
                "error": self.error,
                "cost_usd": None,
            },
            max(ts, self.last_ms),
        )
        self.turn_id = None

    # -------------------------------------------------------------- records
    def user(self, text: str, ts: int) -> None:
        body = _clip(text)
        if not body:
            return
        self.close_turn(ts)
        self._emit("user_message", {"text": body}, ts)

    def thinking(self, text: str, message_id: str, ts: int) -> None:
        self._ensure_turn(ts)
        self._flush_thought(ts)
        self._thought = (_clip(text), message_id, ts)

    def text(self, text: str, message_id: str, ts: int) -> None:
        body = _clip(text)
        if not body:
            return
        turn_id = self._ensure_turn(ts)
        self._flush_thought(ts)
        self._emit(
            "assistant_text",
            {"turn_id": turn_id, "message_id": message_id, "text": body},
            ts,
        )

    def tool_call(self, call_id: str, name: str, args: Any, ts: int) -> None:
        turn_id = self._ensure_turn(ts)
        self._flush_thought(ts)
        self._call_started[call_id] = ts
        payload = args if isinstance(args, dict) else {"input": args}
        self._emit(
            "tool_call",
            {
                "turn_id": turn_id,
                "call_id": call_id,
                "name": name,
                "input": payload,
                "summary": _summary(name, args),
            },
            ts,
        )

    def tool_result(self, call_id: str, output: Any, is_error: bool, ts: int) -> None:
        turn_id = self._ensure_turn(ts)
        self._flush_thought(ts)
        started = self._call_started.get(call_id)
        self._emit(
            "tool_result",
            {
                "turn_id": turn_id,
                "call_id": call_id,
                "output": _clip(_result_text(output)),
                "is_error": bool(is_error),
                "duration_ms": max(0, ts - started) if started is not None else None,
            },
            ts,
        )

    def usage(self, message_id: str, usage: Any, ts: int) -> None:
        """One message's token report, folded into the turn's running total.

        A message id repeats across the records of one reply (a thought, a
        sentence, a tool call each carry the same usage), so the report is kept
        once per id — and at its LARGEST, because the first copy is the
        message-start snapshot whose output count is a placeholder (BUG-173).
        """
        if not isinstance(usage, dict) or self.turn_id is None:
            return
        counted = {
            key: int(usage[key])
            for key in _USAGE_KEYS
            if isinstance(usage.get(key), int | float) and not isinstance(usage.get(key), bool)
        }
        details = usage.get("output_tokens_details")
        if isinstance(details, dict) and isinstance(details.get("thinking_tokens"), int | float):
            counted["thinking_tokens"] = int(details["thinking_tokens"])
        if not counted:
            return
        previous = self._usage_by_message.get(message_id)
        if previous:
            counted = {k: max(counted.get(k, 0), previous.get(k, 0)) for k in (*counted, *previous)}
        if counted == previous:
            return
        self._usage_by_message[message_id] = counted
        totals: dict[str, int] = {}
        for per_message in self._usage_by_message.values():
            for key, value in per_message.items():
                totals[key] = totals.get(key, 0) + value
        self._usage = totals
        self._emit("usage_delta", {"turn_id": self.turn_id, "usage": totals}, ts)

    def usage_total(self, usage: dict[str, int], ts: int) -> None:
        """A turn-level total, from a CLI that reports one instead of per message."""
        if not usage or self.turn_id is None:
            return
        self._usage = dict(usage)
        self._emit("usage_delta", {"turn_id": self.turn_id, "usage": dict(usage)}, ts)

    # ---------------------------------------------------------------- close
    def finish(self) -> list[dict[str, Any]]:
        """The log, ended the way the pane's state says it ended."""
        if self.turn_id is not None:
            if self.live:
                # Still working: the last thought is the one happening now.
                if self._thought is not None:
                    text, message_id, started = self._thought
                    self._thought = None
                    self._emit(
                        "reasoning_started",
                        {"turn_id": self.turn_id, "message_id": message_id},
                        started,
                    )
                    if text:
                        self._emit(
                            "reasoning_delta",
                            {"turn_id": self.turn_id, "message_id": message_id, "text": text},
                            started,
                        )
            else:
                self.close_turn(self.last_ms)
        elif self.live and self.events and self.events[-1]["kind"] == "user_message":
            # Asked and not yet answered, on a pane that is working: open the
            # turn so the timeline says so instead of ending on the question.
            self._ensure_turn(self.events[-1]["ts_ms"])
        return _last_turns(self.events, MAX_TURNS)


#: The token counts worth carrying — the chat shows output only, but the rest
#: is what a later cost line would read (jarvis/agent_chat/runner_cli.py).
_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def _result_text(content: Any) -> str:
    """A tool result's text, whatever shape the CLI stored it in."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" or "text" in block:
                    parts.append(str(block.get("text") or ""))
                elif block.get("type") == "image":
                    parts.append("[image]")
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(p for p in parts if p)
    if isinstance(content, dict):
        for key in ("output", "content", "text"):
            if isinstance(content.get(key), str | list):
                return _result_text(content[key])
        try:
            return json.dumps(content, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(content)
    return "" if content is None else str(content)


def _last_turns(events: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """The events of the last ``limit`` exchanges — cut at a person's message.

    Every turn starts at a ``user_message``, so cutting there drops whole
    turns and never the head of one; the previous turn's ``turn_finished``
    comes right before the message and goes with its turn.
    """
    starts = [i for i, ev in enumerate(events) if ev["kind"] == "user_message"]
    if len(starts) <= limit:
        return events
    return events[starts[-limit] :]


def _claude_events(session_id: str, home: Path | None, live: bool) -> list[dict[str, Any]] | None:
    path = _claude_file(session_id, home)
    if path is None:
        return None
    log = _EventLog(provider="claude", live=live)
    for row in _rows(path):
        # A sidechain is a sub-agent's own conversation, filed in the same
        # record; it is not what this pane said.
        if row.get("isSidechain"):
            continue
        kind = str(row.get("type") or "")
        if kind not in ("user", "assistant"):
            continue
        message = row.get("message")
        if not isinstance(message, dict):
            continue
        ts = _ts_ms(row.get("timestamp")) or log.last_ms
        content = message.get("content")
        if kind == "user":
            # `isMeta` marks the harness writing in the user's name — command
            # caveats, session notes — which no person typed.
            if row.get("isMeta"):
                continue
            if isinstance(content, str):
                log.user(_spoken(content), ts)
                continue
            if not isinstance(content, list):
                continue
            spoken: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = str(block.get("type") or "")
                if btype == "tool_result":
                    log.tool_result(
                        str(block.get("tool_use_id") or ""),
                        block.get("content"),
                        bool(block.get("is_error")),
                        ts,
                    )
                elif btype == "text":
                    spoken.append(str(block.get("text") or ""))
            if spoken:
                log.user(_spoken("\n\n".join(spoken)), ts)
            continue

        # assistant
        model = message.get("model")
        if isinstance(model, str) and model:
            log.model = model
        effort = row.get("effort")
        if isinstance(effort, str) and effort:
            log.effort = effort
        mid = str(message.get("id") or row.get("uuid") or "")
        row_id = str(row.get("uuid") or mid)
        if isinstance(content, str):
            log.text(content, row_id, ts)
            log.usage(mid, message.get("usage"), ts)
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = str(block.get("type") or "")
            if btype == "thinking":
                log.thinking(str(block.get("thinking") or ""), mid, ts)
            elif btype == "text":
                log.text(str(block.get("text") or ""), row_id, ts)
            elif btype == "tool_use":
                log.tool_call(
                    str(block.get("id") or row_id),
                    str(block.get("name") or "tool"),
                    block.get("input"),
                    ts,
                )
        log.usage(mid, message.get("usage"), ts)
    return log.finish()


def _codex_events(session_id: str, home: Path | None, live: bool) -> list[dict[str, Any]] | None:
    path = _codex_file(session_id, home)
    if path is None:
        return None
    log = _EventLog(provider="codex", live=live)
    for row in _rows(path):
        kind = str(row.get("type") or "")
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        ts = _ts_ms(row.get("timestamp")) or log.last_ms
        if kind == "turn_context":
            model = payload.get("model")
            if isinstance(model, str) and model:
                log.model = model
            continue
        if kind == "event_msg":
            ptype = str(payload.get("type") or "")
            if ptype == "task_complete":
                error = payload.get("error")
                if isinstance(error, dict) and error.get("message"):
                    log.error = str(error["message"])
                    log.close_turn(ts, "error")
                else:
                    log.close_turn(ts)
            elif ptype == "turn_aborted":
                log.close_turn(ts, "cancelled")
            elif ptype == "token_count":
                info = payload.get("info")
                usage = (
                    info.get("last_token_usage") or info.get("total_token_usage")
                    if isinstance(info, dict)
                    else None
                )
                if isinstance(usage, dict):
                    counted = {
                        key: int(usage[key])
                        for key in _USAGE_KEYS
                        if isinstance(usage.get(key), int | float)
                        and not isinstance(usage.get(key), bool)
                    }
                    log.usage_total(counted, ts)
            continue
        if kind != "response_item":
            continue
        ptype = str(payload.get("type") or "")
        item_id = str(payload.get("id") or payload.get("call_id") or f"row-{len(log.events)}")
        if ptype == "message":
            role = str(payload.get("role") or "")
            content = payload.get("content")
            texts: list[str] = []
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                texts.extend(
                    str(block.get("text") or "")
                    for block in content
                    if isinstance(block, dict) and str(block.get("type") or "").endswith("text")
                )
            joined = "\n\n".join(t for t in texts if t)
            if role == "user":
                log.user(_spoken(joined), ts)
            elif role == "assistant":
                log.text(joined, item_id, ts)
            # `developer` is the harness briefing the model — not conversation.
        elif ptype in ("function_call", "local_shell_call", "custom_tool_call"):
            arguments = payload.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except (ValueError, TypeError):
                    # Keep the raw string — the timeline prints either shape.
                    pass
            log.tool_call(
                item_id,
                str(payload.get("name") or payload.get("type") or "tool"),
                arguments,
                ts,
            )
        elif ptype in ("function_call_output", "custom_tool_call_output"):
            output = payload.get("output")
            is_error = False
            if isinstance(output, dict):
                is_error = bool(output.get("is_error") or output.get("error"))
            log.tool_result(item_id, output, is_error, ts)
        elif ptype == "reasoning":
            parts: list[str] = []
            for key in ("summary", "content"):
                blocks = payload.get(key)
                if isinstance(blocks, list):
                    parts.extend(
                        str(block.get("text") or "")
                        for block in blocks
                        if isinstance(block, dict) and block.get("text")
                    )
            log.thinking("\n\n".join(p for p in parts if p), item_id, ts)
    return log.finish()


_EVENT_READERS: dict[str, Callable[[str, Path | None, bool], list[dict[str, Any]] | None]] = {
    "claude": _claude_events,
    "codex": _codex_events,
}


def read_events(
    agent: str,
    session_id: str,
    *,
    home: Path | None = None,
    live: bool = False,
) -> list[dict[str, Any]] | None:
    """The recent conversation of one session as agent-chat events, oldest first.

    The same ``None`` contract as :func:`read`: no readable record, no file yet,
    or a file that cannot be read — the caller shows the live pane. ``live``
    says whether the pane is still working, which decides how the last turn
    ends (see :class:`_EventLog`).
    """
    reader = _EVENT_READERS.get(str(agent or "").strip().lower())
    if reader is None or not str(session_id or "").strip():
        return None
    try:
        return reader(session_id.strip(), home, live)
    except OSError as exc:
        logger.warning(
            "Agent transcript: {} session {} unreadable as events: {}", agent, session_id, exc
        )
        return None


__all__ = [
    "MAX_TEXT",
    "MAX_TURNS",
    "Step",
    "Turn",
    "can_read",
    "read",
    "read_events",
]
