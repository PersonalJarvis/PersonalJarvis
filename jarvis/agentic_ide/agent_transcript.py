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

Two, both load-bearing. A conversation view answers with the last
:data:`MAX_TURNS` exchanges, cut at a person's message and never inside one —
a turn is shown from what was asked, or not at all. The file is streamed from
its head, one line at a time, to find those turns: a pane given one long task
has its question at the HEAD of the file and megabytes of tool output after
it, and a read that started from the end and stopped short came back with the
answer's torso and no question (BUG-196). A line-by-line pass over a
20-megabyte record costs a tenth of a second, less than decoding a fixed tail
as one block did; :data:`MAX_READ_BYTES` only keeps a runaway file from being
walked whole on every poll. And each block of text is capped, because a tool
result can be a whole file and this is a conversation, not a file viewer.

Cross-platform: paths come from :mod:`.agent_sessions`, everything is read as
UTF-8 with replacement, and nothing here is OS-specific.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
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

#: How much of a file is walked to find those turns — from the head, so the
#: turn a pane is on is read from the question that opened it, however much
#: output came after (a 2 MB tail once cut a 3.6 MB session's one prompt off
#: and drew the answer without it, BUG-196). Far above any real session: the
#: largest on the maintainer's box is 21 MB and streams in a tenth of a
#: second. Past the bound the read starts this far before the end, and the
#: first turn shows from wherever that lands — a runaway file is still a
#: file, and a torso beats a blank stage.
MAX_READ_BYTES = 64_000_000


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


def _lines(path: Path, *, max_bytes: int = MAX_READ_BYTES) -> Iterator[str]:
    """The lines of ``path``, oldest first, streamed — never the file in one piece.

    A file larger than ``max_bytes`` is read from that far before its end. The
    line the seek lands in is a fragment and is dropped whole, so a caller only
    ever sees lines that begin where the CLI began them; the last line may
    still be one the CLI is mid-way through writing, and fails to parse, which
    is the right outcome for half a JSON object — the next poll sees it whole.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            skip = size - max_bytes
            if skip > 0:
                logger.debug(
                    "Agent transcript: {} is {} bytes, reading the last {}",
                    path.name,
                    size,
                    max_bytes,
                )
                handle.seek(skip)
                handle.readline()
            for raw in handle:
                yield raw.decode("utf-8", "replace")
    except OSError as exc:
        logger.warning("Agent transcript: cannot read {}: {}", path.name, exc)


def _rows(path: Path, *, max_bytes: int = MAX_READ_BYTES) -> Iterator[dict[str, Any]]:
    """Every JSON object in ``path``, oldest first, one at a time.

    A line that does not parse is skipped in silence, and that IS the handling:
    a CLI writing its file while this reads it always leaves a half line at the
    end, and transcripts interleave plain text with JSONL. Nothing is lost —
    the next poll sees the complete line.
    """
    for line in _lines(path, max_bytes=max_bytes):
        stripped = line.strip()
        if not stripped or not stripped.startswith("{"):
            continue
        try:
            payload = json.loads(stripped)
        except (ValueError, TypeError):
            # Not a JSON line — the parse contract, not a swallowed failure.
            continue
        if isinstance(payload, dict):
            yield payload


#: Argument names worth showing as a step's headline, best first.
#:
#: Matched by NAME rather than by tool, so a tool this module has never heard of
#: still gets a readable line as long as it names its arguments the way every
#: other tool does. That is what keeps this from being a table of one product's
#: tool names — a thing that would be wrong by the next release.
_TARGET_KEYS = (
    "file_path",
    "path",
    # Grok Build, OpenCode and Antigravity spell the same argument their way.
    "target_file",
    "filePath",
    "absolute_path",
    "AbsolutePath",
    "notebook_path",
    "command",
    "CommandLine",
    "cmd",
    "pattern",
    "Pattern",
    "query",
    "Query",
    "url",
    "prompt",
    "description",
    "subject",
    "target_directory",
    "SearchDirectory",
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


def _agent_key(agent: str) -> str:
    """The registry key ``agent`` reads through: its own name, or its binary's.

    A launch profile over another CLI's binary (GLM Coding Plan drives Claude
    Code) writes that CLI's record, so it reads through that CLI's entry — the
    same indirection :func:`agent_sessions._adapter_for` uses to resume it.
    """
    name = str(agent or "").strip().lower()
    if not name:
        return ""
    from jarvis.workspace import agents as workspace_agents

    entry = workspace_agents.get_agent(name)
    return entry.adapter_key if entry is not None else name


def can_read(agent: str) -> bool:
    """Does this CLI keep a conversation this module knows how to read?"""
    return _agent_key(agent) in _READERS


def first_user_text(agent: str, session_id: str, *, home: Path | None = None) -> str | None:
    """The first thing a PERSON said in one session — what opened it.

    ``None`` when this CLI keeps no readable record or the session has no file
    yet; ``""`` when the file is there but nobody has spoken in it so far. Both
    are transient for a pane that has just started, and neither is an error.
    Reads the head of the file only (:data:`MAX_HEAD_BYTES`); the tail readers
    above are for the conversation's recent past.
    """
    opener = _OPENERS.get(_agent_key(agent))
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
    reader = _READERS.get(_agent_key(agent))
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


_EVENT_READERS: dict[
    str, Callable[[str, Path | None, bool], list[dict[str, Any]] | _EventLog | None]
] = {
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
    reader = _EVENT_READERS.get(_agent_key(agent))
    if reader is None or not str(session_id or "").strip():
        return None
    try:
        read_result = reader(session_id.strip(), home, live)
    except OSError as exc:
        logger.warning(
            "Agent transcript: {} session {} unreadable as events: {}", agent, session_id, exc
        )
        return None
    # The newer readers answer with the log itself, whose events are the list.
    return read_result.events if isinstance(read_result, _EventLog) else read_result


# --------------------------------------------------------------------------- #
# the other CLIs — one adapter each, all answering in the event vocabulary
# --------------------------------------------------------------------------- #
#
# Every reader below builds an :class:`_EventLog` and nothing else; the turn
# summary (:func:`read`) and the opening line (:func:`first_user_text`) are
# derived from that log rather than parsed a second time, so a CLI costs one
# parser, not three that drift. Each parser reads the layout it was MEASURED
# against — a live install of the CLI on the maintainer's box, or, where no
# conversation existed to measure, the CLI's own source — and says so.


def _turns_from_events(events: list[dict[str, Any]]) -> list[Turn]:
    """The turn summary of an event log — what :func:`read` answers."""
    turns: list[Turn] = []
    for event in events:
        kind = event.get("kind")
        payload = event.get("payload") or {}
        if kind == "user_message":
            _append(turns, "user", str(payload.get("text") or ""))
        elif kind == "assistant_text":
            _append(turns, "assistant", str(payload.get("text") or ""))
        elif kind == "tool_call":
            target, detail = _headline(payload.get("input"))
            name = str(payload.get("name") or "tool")
            _step(turns, Step(tool=name, target=target, detail=detail))
    return turns


def _turns_via(
    reader: Callable[[str, Path | None, bool], _EventLog | None],
) -> Callable[[str, Path | None], list[Turn] | None]:
    """A :data:`_READERS` entry built on an event reader."""

    def _read(session_id: str, home: Path | None) -> list[Turn] | None:
        log = reader(session_id, home, False)
        return None if log is None else _turns_from_events(log.events)

    return _read


def _opener_via(
    reader: Callable[[str, Path | None, bool], _EventLog | None],
) -> Callable[[str, Path | None], str | None]:
    """A :data:`_OPENERS` entry built on an event reader.

    ``None`` when there is no file, ``""`` when nobody has spoken in it yet —
    the same two answers the head readers give, so :mod:`.opening` treats
    every CLI alike.
    """

    def _first(session_id: str, home: Path | None) -> str | None:
        log = reader(session_id, home, False)
        if log is None:
            return None
        spoken = [e for e in log.events if e.get("kind") == "user_message"]
        if len(spoken) >= MAX_TURNS:
            # The log is a window on the recent past; a session this long has
            # its opening outside it, and a later message is not the opening.
            return ""
        return str((spoken[0].get("payload") or {}).get("text") or "") if spoken else ""

    return _first


def _own_id(session_id: str) -> str:
    """``session_id`` when it can name a directory, else "" — never a path."""
    value = str(session_id or "").strip()
    return "" if not value or "/" in value or "\\" in value or value in (".", "..") else value


def _decode_json(value: Any) -> Any:
    """``value`` parsed when it is JSON text, else as it came.

    Antigravity writes every argument as JSON inside JSON (``"4"``,
    ``"\\"*.png\\""``), OpenCode stores a row's body as text, Kimi streams a
    call's arguments as a string; a path or a sentence is none of those and
    stays a string.
    """
    if isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in ('"', "{", "[", "-") or stripped[:1].isdigit():
            try:
                return json.loads(stripped)
            except (ValueError, TypeError):
                # The CLI's argument text was not JSON after all — shown as is.
                return value
    return value


def _count_tokens(usage: Any, names: dict[str, str]) -> dict[str, int]:
    """A CLI's token report in the chat's own key names — numbers only."""
    if not isinstance(usage, dict):
        return {}
    counted: dict[str, int] = {}
    for theirs, ours in names.items():
        value = usage.get(theirs)
        if isinstance(value, int | float) and not isinstance(value, bool):
            counted[ours] = int(value)
    return counted


# ------------------------------------------------------------------ Grok Build
#
# ~/.grok/sessions/<cwd-group>/<session-id>/updates.jsonl — the ACP
# ``session/update`` stream the TUI itself is drawn from, one JSON-RPC
# notification per line, each carrying the agent's own clock. The session
# directory also holds ``chat_history.jsonl`` (the model's context: whole
# messages, but no timestamps) and ``summary.json`` (the model and effort the
# session runs on); the stream is what is read, and the summary is read once
# for the two facts the stream states nowhere. MEASURED on grok-build sessions
# of 2026-08-27; the same file `agent_sessions._grok_conversation_exists`
# decides on. A message streams as consecutive ``*_chunk`` records and is
# joined back into one here.

_GROK_USAGE = {
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
    "cachedReadTokens": "cache_read_input_tokens",
    "cacheCreationTokens": "cache_creation_input_tokens",
    "reasoningTokens": "thinking_tokens",
}


class _Chunks:
    """Consecutive stream chunks of one message, joined back into the message.

    A thought's duration is read from the record that FOLLOWS it (see
    :class:`_EventLog`), so a thought streamed as forty chunks must reach the
    log as one — forty would make thirty-nine of them a millisecond long.
    """

    def __init__(self, log: _EventLog) -> None:
        self.log = log
        self.kind = ""
        self.key = ""
        self.parts: list[str] = []
        self.started = 0
        self.count = 0

    def add(self, kind: str, text: str, ts: int, key: str = "") -> None:
        """``key`` tells two messages of one kind apart when nothing else does.

        Two prompts sent back to back are two ``user_message_chunk`` runs with
        no record between them; the prompt index in the chunk's own metadata
        is what separates them.
        """
        if kind != self.kind or key != self.key:
            self.flush()
        if not self.parts:
            self.started = ts
            self.count += 1
        self.kind = kind
        self.key = key
        self.parts.append(text)

    def flush(self) -> None:
        if not self.parts:
            self.kind = ""
            return
        text, kind, started = "".join(self.parts), self.kind, self.started
        self.parts, self.kind = [], ""
        message_id = f"chunk-{self.count}"
        if kind == "user":
            self.log.user(_spoken(text), started)
        elif kind == "thought":
            self.log.thinking(text, message_id, started)
        elif kind == "text":
            self.log.text(text, message_id, started)


def _grok_file(session_id: str, home: Path | None) -> Path | None:
    own = _own_id(session_id)
    if not own:
        return None
    root = agent_sessions._grok_home(home) / "sessions"
    if not root.is_dir():
        return None
    folder = next((p for p in root.glob(f"*/{own}") if p.is_dir()), None)
    if folder is None:
        return None
    path = folder / "updates.jsonl"
    return path if path.is_file() else None


def _grok_summary(folder: Path) -> dict[str, Any]:
    """``summary.json`` of a session — the model and effort it runs on, or {}."""
    path = folder / "summary.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, ValueError) as exc:
        logger.debug("Agent transcript: grok summary unreadable: {}", exc)
        return {}
    return payload if isinstance(payload, dict) else {}


def _grok_chunk_text(update: dict[str, Any]) -> str:
    content = update.get("content")
    if isinstance(content, dict):
        return str(content.get("text") or "")
    return _result_text(content)


def _grok_tool_name(update: dict[str, Any]) -> str:
    meta = update.get("_meta")
    tool = meta.get("x.ai/tool") if isinstance(meta, dict) else None
    if isinstance(tool, dict) and tool.get("name"):
        return str(tool["name"])
    return str(update.get("title") or "tool")


def _first_text(value: Any, depth: int = 0) -> Any:
    """The text inside a CLI's typed output envelope, or the envelope itself.

    Grok Build returns ``{"type": "ListDir", "Content": {"content": "…"}}``:
    a shape per tool, with the text a couple of keys down. The envelope is
    kept whole when no text is found in it.
    """
    if isinstance(value, dict) and depth < 3:
        for key, inner in value.items():
            if str(key).lower() in ("content", "output", "text", "stdout", "result"):
                found = _first_text(inner, depth + 1)
                if isinstance(found, str) and found:
                    return found
        # ``{"type": "ReadFile", "FileNotFound": "Error: …"}`` — one payload
        # under a name only that tool knows.
        rest = [inner for key, inner in value.items() if key != "type"]
        if len(rest) == 1:
            found = _first_text(rest[0], depth + 1)
            if isinstance(found, str) and found:
                return found
    return value


def _grok_output(update: dict[str, Any]) -> Any:
    """What a finished call returned: its content blocks, else the raw output."""
    blocks = update.get("content")
    if isinstance(blocks, list) and blocks:
        parts: list[str] = []
        for block in blocks:
            inner = block.get("content") if isinstance(block, dict) else None
            if isinstance(inner, dict):
                if inner.get("type") == "image":
                    parts.append("[image]")
                else:
                    parts.append(str(inner.get("text") or ""))
        return "\n".join(p for p in parts if p)
    return _first_text(update.get("rawOutput"))


def _grok_events(session_id: str, home: Path | None, live: bool) -> _EventLog | None:
    path = _grok_file(session_id, home)
    if path is None:
        return None
    log = _EventLog(provider="grok-build", live=live)
    summary = _grok_summary(path.parent)
    log.model = str(summary.get("current_model_id") or "")
    log.effort = str(summary.get("reasoning_effort") or "")
    chunks = _Chunks(log)
    # A background task reports its call as completed twice (once when it is
    # backgrounded, once when it ends); a result lands on its call once.
    answered: set[str] = set()
    for row in _rows(path):
        params = row.get("params")
        if not isinstance(params, dict):
            continue
        update = params.get("update")
        if not isinstance(update, dict):
            continue
        meta = params.get("_meta")
        ts = (
            (_ts_ms(meta.get("agentTimestampMs")) if isinstance(meta, dict) else None)
            or _ts_ms(row.get("timestamp"))
            or log.last_ms
        )
        kind = str(update.get("sessionUpdate") or "")
        if kind == "user_message_chunk":
            raw_meta = update.get("_meta")
            own_meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
            if own_meta.get("modelId"):
                log.model = str(own_meta["modelId"])
            prompt = str(own_meta.get("promptIndex", ""))
            chunks.add("user", _grok_chunk_text(update), ts, key=prompt)
            continue
        if kind == "agent_thought_chunk":
            chunks.add("thought", _grok_chunk_text(update), ts)
            continue
        if kind == "agent_message_chunk":
            chunks.add("text", _grok_chunk_text(update), ts)
            continue
        chunks.flush()
        if kind == "tool_call":
            call_id = str(update.get("toolCallId") or f"call-{len(log.events)}")
            log.tool_call(call_id, _grok_tool_name(update), update.get("rawInput"), ts)
        elif kind == "tool_call_update":
            # The first update of a call refines its title and locations; only
            # one with a terminal status carries what the call returned.
            status = str(update.get("status") or "")
            call_id = str(update.get("toolCallId") or "")
            if status not in ("completed", "failed") or not call_id or call_id in answered:
                continue
            answered.add(call_id)
            log.tool_result(call_id, _grok_output(update), status == "failed", ts)
        elif kind == "turn_completed":
            log.usage_total(_count_tokens(update.get("usage"), _GROK_USAGE), ts)
            stop = str(update.get("stop_reason") or "")
            log.close_turn(ts, "cancelled" if stop in ("cancelled", "aborted") else "done")
        # hook runs, plans, background-task bookkeeping, sub-agent spawns and
        # the session recap are the CLI's own housekeeping, not the conversation.
    chunks.flush()
    log.events = log.finish()
    return log


# ----------------------------------------------------------------- Antigravity
#
# <gemini-home>/antigravity-cli/brain/<id>/.system_generated/logs/transcript.jsonl
# — one step per line: the person's request (``USER_INPUT``, wrapped in a
# ``<USER_REQUEST>`` block beside the harness's metadata), the model's step
# (``PLANNER_RESPONSE``: its thinking, its prose, its tool calls), each call's
# output as the ``GENERIC`` step that follows in order, and the system's
# checkpoints and messages. Tool-call arguments are JSON-encoded strings
# inside JSON. MEASURED on agy 1.1.20 (2026-08-25). The record names no
# model, so the pane's own picks stand.

_AGY_REQUEST = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)


def _agy_file(session_id: str, home: Path | None) -> Path | None:
    own = _own_id(session_id)
    if not own:
        return None
    path = agent_sessions._agy_transcript(agent_sessions._agy_cli_root(home), own)
    return path if path.is_file() else None


def _agy_args(args: Any) -> Any:
    if not isinstance(args, dict):
        return _decode_json(args)
    return {str(key): _decode_json(value) for key, value in args.items()}


def _agy_events(session_id: str, home: Path | None, live: bool) -> _EventLog | None:
    path = _agy_file(session_id, home)
    if path is None:
        return None
    log = _EventLog(provider="antigravity", live=live)
    # Calls whose output has not arrived yet, in the order the model made them
    # — the transcript pairs a result with its call by position, not by id.
    pending: list[str] = []
    for row in _rows(path):
        kind = str(row.get("type") or "")
        ts = _ts_ms(row.get("created_at")) or log.last_ms
        step = str(row.get("step_index") if row.get("step_index") is not None else len(log.events))
        content = str(row.get("content") or "")
        if kind == "USER_INPUT":
            if str(row.get("source") or "") != "USER_EXPLICIT":
                continue
            match = _AGY_REQUEST.search(content)
            log.user(match.group(1) if match else _spoken(content), ts)
        elif kind == "PLANNER_RESPONSE":
            thinking = row.get("thinking")
            if isinstance(thinking, str) and thinking.strip():
                log.thinking(thinking, f"step-{step}", ts)
            if content:
                log.text(content, f"step-{step}", ts)
            calls = row.get("tool_calls")
            if isinstance(calls, list):
                for index, call in enumerate(calls):
                    if not isinstance(call, dict):
                        continue
                    call_id = f"step-{step}-{index}"
                    log.tool_call(
                        call_id, str(call.get("name") or "tool"), _agy_args(call.get("args")), ts
                    )
                    pending.append(call_id)
        elif kind == "GENERIC":
            if pending:
                # The record does not say whether the call failed; the output
                # itself does, and it is shown whole.
                log.tool_result(pending.pop(0), content, False, ts)
        elif kind == "ERROR_MESSAGE" and content:
            # What the pane showed in place of an answer — the model retries
            # after it, so the turn stays open.
            log.text(content, f"step-{step}", ts)
        # CHECKPOINT and SYSTEM_MESSAGE are the harness talking to the model.
    log.events = log.finish()
    return log


# -------------------------------------------------------------------- OpenCode
#
# The session database (see `agent_sessions._opencode_db`): a ``message`` row
# per message (role, model, variant, token totals, timestamps) and ``part``
# rows for its body — ``text``, ``reasoning`` with its own start and end,
# ``tool`` with the call's input and its state, ``step-start``/``step-finish``.
# MEASURED on opencode 1.18.23 (2026-08-26). Bounded by message count rather
# than bytes, because this is a database and not a file.

_OPENCODE_MAX_MESSAGES = 600

_OPENCODE_TOKENS = {
    "input": "input_tokens",
    "output": "output_tokens",
    "reasoning": "thinking_tokens",
}


def _opencode_tokens(tokens: Any) -> dict[str, Any]:
    """A message's token report in the shape :meth:`_EventLog.usage` reads."""
    counted: dict[str, Any] = _count_tokens(tokens, _OPENCODE_TOKENS)
    cache = tokens.get("cache") if isinstance(tokens, dict) else None
    counted.update(
        _count_tokens(
            cache,
            {"read": "cache_read_input_tokens", "write": "cache_creation_input_tokens"},
        )
    )
    thinking = counted.pop("thinking_tokens", None)
    if thinking is not None:
        counted["output_tokens_details"] = {"thinking_tokens": thinking}
    return counted


def _opencode_events(session_id: str, home: Path | None, live: bool) -> _EventLog | None:
    own = str(session_id or "").strip()
    if not own or not agent_sessions._opencode_rows(
        home, "SELECT 1 FROM session WHERE id = ? LIMIT 1", (own,)
    ):
        return None
    log = _EventLog(provider="opencode", live=live)
    messages = agent_sessions._opencode_rows(
        home,
        "SELECT id, time_created, data FROM message WHERE session_id = ? "
        "ORDER BY time_created DESC LIMIT ?",
        (own, _OPENCODE_MAX_MESSAGES),
    )
    messages.reverse()
    if not messages:
        log.events = log.finish()
        return log
    parts_by_message: dict[str, list[tuple[str, Any, Any, dict[str, Any]]]] = {}
    for part_id, message_id, created, updated, data in agent_sessions._opencode_rows(
        home,
        "SELECT id, message_id, time_created, time_updated, data FROM part "
        "WHERE session_id = ? AND time_created >= ? ORDER BY time_created",
        (own, messages[0][1]),
    ):
        part = _decode_json(data)
        if isinstance(part, dict):
            parts_by_message.setdefault(str(message_id), []).append(
                (str(part_id), created, updated, part)
            )
    for message_id, created, data in messages:
        message = _decode_json(data)
        if not isinstance(message, dict):
            continue
        clock = message.get("time")
        ts = (
            (_ts_ms(clock.get("created")) if isinstance(clock, dict) else None)
            or _ts_ms(created)
            or log.last_ms
        )
        parts = parts_by_message.get(str(message_id), [])
        role = str(message.get("role") or "")
        if role == "user":
            texts = [str(p.get("text") or "") for _, _, _, p in parts if p.get("type") == "text"]
            log.user(_spoken("\n\n".join(t for t in texts if t)), ts)
            continue
        if role != "assistant":
            continue
        if isinstance(message.get("modelID"), str) and message["modelID"]:
            log.model = message["modelID"]
        if isinstance(message.get("variant"), str) and message["variant"]:
            log.effort = message["variant"]
        for part_id, part_created, part_updated, part in parts:
            ptype = str(part.get("type") or "")
            pts = _ts_ms(part_created) or ts
            if ptype == "reasoning":
                span = part.get("time")
                started = (_ts_ms(span.get("start")) if isinstance(span, dict) else None) or pts
                log.thinking(str(part.get("text") or ""), part_id, started)
            elif ptype == "text":
                log.text(str(part.get("text") or ""), part_id, pts)
            elif ptype == "tool":
                raw_state = part.get("state")
                state: dict[str, Any] = raw_state if isinstance(raw_state, dict) else {}
                call_id = str(part.get("callID") or part_id)
                span = state.get("time")
                started = (_ts_ms(span.get("start")) if isinstance(span, dict) else None) or pts
                log.tool_call(call_id, str(part.get("tool") or "tool"), state.get("input"), started)
                status = str(state.get("status") or "")
                if status in ("completed", "error"):
                    ended = (
                        (_ts_ms(span.get("end")) if isinstance(span, dict) else None)
                        or _ts_ms(part_updated)
                        or pts
                    )
                    output = state.get("error") if status == "error" else state.get("output")
                    log.tool_result(call_id, output, status == "error", ended)
        log.usage(str(message_id), _opencode_tokens(message.get("tokens")), max(ts, log.last_ms))
        error = message.get("error")
        if isinstance(error, dict) and (error.get("message") or error.get("name")):
            log.error = str(error.get("message") or error.get("name"))
        elif isinstance(error, str) and error:
            log.error = error
    log.events = log.finish()
    return log


# ------------------------------------------------------------------------ Kimi
#
# <root>/sessions/<bucket>/<session-id>/agents/main/wire.jsonl (current
# generation) or <root>/sessions/<bucket>/<session-id>/wire.jsonl (legacy) —
# the wire log both generations replay a session from. Two record shapes:
#
# * **Wrapped** (legacy, protocol 1.1): ``{"timestamp": s, "message":
#   {"type", "payload"}}`` — ``TurnBegin`` with the person's input,
#   ``ContentPart`` (a ``think`` or ``text`` part), ``ToolCall``,
#   ``ToolResult``, ``StatusUpdate`` with the step's token usage. MEASURED on
#   a kimi-cli session of 2026-02-05.
# * **Flat** (current, protocol 1.4): ``{"type", ..., "time": ms}`` —
#   ``context.append_message`` for the person (and the harness's reminders,
#   told apart by ``origin.kind``), ``context.append_loop_event`` carrying the
#   agent's ``content.part`` / ``tool.call`` / ``tool.result`` / ``step.end``,
#   ``usage.record`` naming the model. Taken from the CLI's own writer
#   (kimi-code 0.29.2, ``agent-core/context`` and ``turn_step``): no signed-in
#   install existed to record a live conversation with, so the shapes are the
#   source's, and a future record shape degrades to fewer rows, never an error.

_KIMI_USAGE = {
    "output": "output_tokens",
    "input_other": "input_tokens",
    "inputOther": "input_tokens",
    "input_cache_read": "cache_read_input_tokens",
    "inputCacheRead": "cache_read_input_tokens",
    "input_cache_creation": "cache_creation_input_tokens",
    "inputCacheCreation": "cache_creation_input_tokens",
}


def _kimi_file(session_id: str, home: Path | None) -> Path | None:
    own = _own_id(session_id)
    if not own:
        return None
    root = agent_sessions._kimi_root(home)
    if root is None:
        return None
    sessions = root / "sessions"
    if not sessions.is_dir():
        return None
    folder = next((p for p in sessions.glob(f"*/{own}") if p.is_dir()), None)
    if folder is None:
        return None
    for candidate in (folder / "agents" / "main" / "wire.jsonl", folder / "wire.jsonl"):
        if candidate.is_file():
            return candidate
    return None


def _kimi_text(content: Any) -> str:
    """The prose of a Kimi message body: text parts joined, other media named."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = str(block.get("type") or "")
        if btype == "text":
            parts.append(str(block.get("text") or ""))
        elif btype.endswith("_url"):
            parts.append(f"[{btype[:-4]}]")
    return "\n".join(p for p in parts if p)


def _kimi_call(log: _EventLog, call: Any, ts: int) -> None:
    if not isinstance(call, dict):
        return
    raw = call.get("function")
    function: dict[str, Any] = raw if isinstance(raw, dict) else {}
    name = str(function.get("name") or call.get("name") or "tool")
    arguments = function.get("arguments") if function else call.get("args")
    call_id = str(call.get("id") or f"call-{len(log.events)}")
    log.tool_call(call_id, name, _decode_json(arguments), ts)


def _kimi_part(log: _EventLog, part: Any, message_id: str, ts: int) -> None:
    if not isinstance(part, dict):
        return
    ptype = str(part.get("type") or "")
    if ptype == "think":
        log.thinking(str(part.get("think") or part.get("text") or ""), message_id, ts)
    elif ptype == "text":
        log.text(str(part.get("text") or ""), message_id, ts)


class _KimiCall:
    """A legacy ``ToolCall`` held back while its ``ToolCallPart`` deltas arrive.

    The legacy wire records a call before its arguments have streamed, then the
    arguments in parts; emitting on the first record would show every such
    call without its target.
    """

    def __init__(self, log: _EventLog) -> None:
        self.log = log
        self.held: tuple[dict[str, Any], int] | None = None

    def hold(self, payload: dict[str, Any], ts: int) -> None:
        self.flush()
        self.held = (payload, ts)

    def extend(self, part: str) -> None:
        if self.held is None:
            return
        payload, _ts = self.held
        function = payload.get("function")
        if isinstance(function, dict):
            function["arguments"] = str(function.get("arguments") or "") + part

    def flush(self, unless_before: str | None = None) -> None:
        """Emit the held call — except when ``unless_before`` names another call.

        Results of EARLIER calls land while a later call's arguments are still
        streaming (measured: the third of three parallel reads); such a result
        is not what closes the held call.
        """
        if self.held is None:
            return
        payload, ts = self.held
        if unless_before is not None and unless_before != str(payload.get("id") or ""):
            return
        self.held = None
        _kimi_call(self.log, payload, ts)


def _kimi_wrapped(log: _EventLog, message: dict[str, Any], ts: int, calls: _KimiCall) -> None:
    """One legacy wire record — ``message.type`` names it, ``payload`` is it."""
    kind = str(message.get("type") or "")
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return
    if kind == "ToolCall":
        calls.hold(payload, ts)
        return
    if kind == "ToolCallPart":
        calls.extend(str(payload.get("arguments_part") or ""))
        return
    if kind == "ToolResult":
        calls.flush(unless_before=str(payload.get("tool_call_id") or ""))
    else:
        # A token report marks the end of the model's step: every call of it
        # has its arguments by then.
        calls.flush()
    if kind == "TurnBegin":
        log.user(_spoken(_kimi_text(payload.get("user_input"))), ts)
    elif kind == "ContentPart":
        _kimi_part(log, payload, f"part-{len(log.events)}", ts)
    elif kind == "ToolResult":
        returned = payload.get("return_value")
        returned = returned if isinstance(returned, dict) else {"output": returned}
        log.tool_result(
            str(payload.get("tool_call_id") or ""),
            returned.get("output"),
            bool(returned.get("is_error")),
            ts,
        )
    elif kind == "StatusUpdate":
        message_id = str(payload.get("message_id") or f"status-{len(log.events)}")
        log.usage(message_id, _count_tokens(payload.get("token_usage"), _KIMI_USAGE), ts)
    # StepBegin and the approval exchange are the TUI's own bookkeeping.


def _kimi_flat(log: _EventLog, row: dict[str, Any], ts: int) -> None:
    """One current-generation wire record — ``type`` names it."""
    kind = str(row.get("type") or "")
    if kind == "context.append_message":
        message = row.get("message")
        if not isinstance(message, dict):
            return
        origin = message.get("origin")
        source = str(origin.get("kind") or "user") if isinstance(origin, dict) else "user"
        role = str(message.get("role") or "")
        if role == "user":
            # A reminder the harness appends in the person's name says so in
            # its origin; the person's own prompt is the one with kind "user".
            if source == "user":
                log.user(_spoken(_kimi_text(message.get("content"))), ts)
        elif role == "assistant":
            message_id = f"message-{len(log.events)}"
            content = message.get("content")
            if isinstance(content, list):
                for part in content:
                    _kimi_part(log, part, message_id, ts)
            elif isinstance(content, str):
                log.text(content, message_id, ts)
            calls = message.get("toolCalls") or message.get("tool_calls")
            if isinstance(calls, list):
                for call in calls:
                    _kimi_call(log, call, ts)
        elif role == "tool":
            log.tool_result(
                str(message.get("toolCallId") or message.get("tool_call_id") or ""),
                _kimi_text(message.get("content")),
                bool(message.get("isError") or message.get("is_error")),
                ts,
            )
        return
    if kind == "context.append_loop_event":
        event = row.get("event")
        if not isinstance(event, dict):
            return
        etype = str(event.get("type") or "")
        if etype == "content.part":
            part_id = str(event.get("uuid") or f"part-{len(log.events)}")
            _kimi_part(log, event.get("part"), part_id, ts)
        elif etype == "tool.call":
            log.tool_call(
                str(event.get("toolCallId") or event.get("uuid") or f"call-{len(log.events)}"),
                str(event.get("name") or "tool"),
                event.get("args"),
                ts,
            )
        elif etype == "tool.result":
            result = event.get("result")
            result = result if isinstance(result, dict) else {"output": result}
            log.tool_result(
                str(event.get("toolCallId") or event.get("parentUuid") or ""),
                result.get("output"),
                bool(result.get("isError") or result.get("is_error")),
                ts,
            )
        elif etype == "step.end":
            message_id = str(event.get("uuid") or f"step-{len(log.events)}")
            log.usage(message_id, _count_tokens(event.get("usage"), _KIMI_USAGE), ts)
        return
    if kind == "usage.record":
        model = row.get("model")
        if isinstance(model, str) and model:
            log.model = model
    elif kind == "turn.cancel":
        log.close_turn(ts, "cancelled")
    # turn.prompt duplicates the append_message the prompt becomes; the rest
    # (config, tools, compaction, permission and plan-mode records) is state.


def _kimi_events(session_id: str, home: Path | None, live: bool) -> _EventLog | None:
    path = _kimi_file(session_id, home)
    if path is None:
        return None
    log = _EventLog(provider="kimi", live=live)
    calls = _KimiCall(log)
    for row in _rows(path):
        message = row.get("message")
        if isinstance(message, dict) and "payload" in message:
            _kimi_wrapped(log, message, _ts_ms(row.get("timestamp")) or log.last_ms, calls)
        else:
            calls.flush()
            _kimi_flat(log, row, _ts_ms(row.get("time")) or log.last_ms)
    calls.flush()
    log.events = log.finish()
    return log


# --------------------------------------------------------------- registration
#
# Keyed by the adapter key of `jarvis.workspace.agents` — a launch profile
# over another CLI's binary (GLM Coding Plan drives Claude Code) writes that
# CLI's record and resolves to it through :func:`_agent_key`.

_MORE_EVENT_READERS: dict[str, Callable[[str, Path | None, bool], _EventLog | None]] = {
    "grok-build": _grok_events,
    "antigravity": _agy_events,
    "opencode": _opencode_events,
    "kimi": _kimi_events,
}
_EVENT_READERS.update(_MORE_EVENT_READERS)
_READERS.update({key: _turns_via(reader) for key, reader in _MORE_EVENT_READERS.items()})
_OPENERS.update({key: _opener_via(reader) for key, reader in _MORE_EVENT_READERS.items()})


__all__ = [
    "MAX_TEXT",
    "MAX_TURNS",
    "Step",
    "Turn",
    "can_read",
    "read",
    "read_events",
]
