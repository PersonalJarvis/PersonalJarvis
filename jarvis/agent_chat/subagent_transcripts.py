"""A coding agent's sub-agents, read from the CLI's own session files.

``codex exec --json`` streams the main agent only: a sub-agent's steps never
reach the thread's stream. Codex files each sub-agent as a rollout of its own,
whose first line (``session_meta``) names the thread that spawned it
(``parent_thread_id``) and the line its own work starts at
(``subagent_history_start_ordinal`` — everything before is the parent's context,
copied in). Read from there, a sub-agent's rollout is its conversation: its
words, its thoughts' summaries and its tool calls with their results.

Every event this returns speaks the agent-chat vocabulary
(``jarvis/agent_chat/events.py``), so the thread folds it with the same reducer
as everything else. Nothing here is OS-specific: paths come from ``pathlib`` and
the CLI's own ``CODEX_HOME``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from jarvis.agent_chat.events import make_event

log = logging.getLogger(__name__)

#: A Codex thread id as it appears in a rollout file name.
_THREAD_ID: Final = re.compile(r"^[0-9a-fA-F-]{16,64}$")
#: How many sub-agent levels are followed below the main agent.
_MAX_DEPTH: Final = 4
#: The most rollout files one lookup opens, so a heavy Codex history stays cheap.
_MAX_FILES: Final = 400
#: The longest tool output a step keeps; the thread shows a preview anyway.
_MAX_OUTPUT: Final = 8000


@dataclass(slots=True)
class CodexSubagent:
    thread_id: str
    parent_thread_id: str
    nickname: str = ""
    role: str = ""
    path: str = ""
    started_ms: int = 0
    updated_ms: int = 0
    status: str = "running"
    summary: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "parent_thread_id": self.parent_thread_id,
            "nickname": self.nickname,
            "role": self.role,
            "path": self.path,
            "started_ms": self.started_ms,
            "updated_ms": self.updated_ms,
            "status": self.status,
            "summary": self.summary,
            "events": self.events,
        }


def codex_homes(account_id: str | None = None) -> list[Path]:
    """Every CODEX_HOME a thread's sub-agents may live in: its seat's first."""
    homes: list[Path] = []
    try:
        from jarvis import agent_accounts

        if account_id:
            homes.append(agent_accounts.config_dir_for("codex", account_id))  # type: ignore[arg-type]
        homes.extend(
            agent_accounts.config_dir_for("codex", account.id)  # type: ignore[arg-type]
            for account in agent_accounts.list_accounts("codex")  # type: ignore[arg-type]
        )
    except Exception as exc:  # noqa: BLE001 - the default home below still answers; logged
        log.debug("codex account homes unavailable: %s", type(exc).__name__)
    raw = os.environ.get("CODEX_HOME")
    if raw:
        homes.append(Path(raw).expanduser())
    homes.append(Path.home() / ".codex")
    unique: list[Path] = []
    for home in homes:
        if home not in unique:
            unique.append(home)
    return unique


def _ms(value: Any) -> int:
    if not isinstance(value, str) or not value:
        return 0
    text = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:  # an unparsable timestamp sorts as the oldest
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def _rollouts_since(homes: Iterable[Path], since_ms: int) -> Iterator[Path]:
    """Rollout files from the day before ``since_ms`` onward, newest day first."""
    start = datetime.fromtimestamp(max(0, since_ms) / 1000, UTC).date() - timedelta(days=1)
    today = datetime.now(UTC).date() + timedelta(days=1)
    days: list[Any] = []
    day = today
    while day >= start and len(days) < 60:
        days.append(day)
        day -= timedelta(days=1)
    opened = 0
    for home in homes:
        for day in days:
            folder = home / "sessions" / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("rollout-*.jsonl")):
                opened += 1
                if opened > _MAX_FILES:
                    return
                yield path


def _first_line(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            line = handle.readline()
    except OSError as exc:
        log.debug("codex rollout unreadable %s: %s", path.name, exc)
        return None
    try:
        obj = json.loads(line)
    except ValueError:  # a non-JSON first line is not a session header
        return None
    return obj if isinstance(obj, dict) and obj.get("type") == "session_meta" else None


def _text_parts(content: Any, *kinds: str) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [
        str(part.get("text") or "")
        for part in content
        if isinstance(part, dict) and part.get("type") in kinds
    ]
    return "\n".join(part for part in parts if part)


def _arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:  # plain-text tool input is kept as text
            return {"input": raw}
        return parsed if isinstance(parsed, dict) else {"input": parsed}
    return {}


def _call(name: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """A Codex tool as the thread names it: shell commands read like the stream's."""
    if name in {"exec_command", "shell", "shell_command", "local_shell"}:
        command = args.get("cmd", args.get("command", ""))
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        return "RunCommand", {"command": str(command)}
    return name, args


def _step_events(obj: dict[str, Any], seen: set[str]) -> list[dict[str, Any]]:
    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
    kind = str(payload.get("type") or "")
    item_id = str(payload.get("id") or payload.get("call_id") or "")
    out: list[dict[str, Any]] = []
    if kind == "message" and payload.get("role") == "assistant":
        text = _text_parts(payload.get("content"), "output_text", "text")
        if text.strip():
            out.append(make_event("assistant_text", {"message_id": item_id, "text": text}))
    elif kind == "reasoning":
        summary = _text_parts(payload.get("summary"), "summary_text")
        if summary.strip():
            out.append(
                make_event(
                    "reasoning", {"message_id": item_id, "text": summary, "duration_ms": None}
                )
            )
    elif kind in {"function_call", "custom_tool_call"}:
        call_id = str(payload.get("call_id") or item_id)
        if not call_id or call_id in seen:
            return out
        seen.add(call_id)
        raw_name = str(payload.get("name") or "tool")
        args = (
            _arguments(payload.get("arguments"))
            if kind == "function_call"
            else {"patch" if raw_name == "apply_patch" else "input": payload.get("input")}
        )
        name, args = _call(raw_name, args)
        out.append(make_event("tool_call", {"call_id": call_id, "name": name, "input": args}))
    elif kind in {"function_call_output", "custom_tool_call_output"}:
        output = payload.get("output")
        text = (
            output
            if isinstance(output, str)
            else _text_parts(output, "input_text", "output_text", "text")
        )
        if isinstance(output, dict):
            text = str(output.get("content") or output.get("output") or "")
        out.append(
            make_event(
                "tool_result",
                {
                    "call_id": str(payload.get("call_id") or ""),
                    "output": text[:_MAX_OUTPUT],
                    "is_error": False,
                    "duration_ms": None,
                },
            )
        )
    elif kind == "web_search_call":
        action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
        out.append(
            make_event(
                "tool_call",
                {
                    "call_id": item_id,
                    "name": "WebSearch",
                    "input": {"query": str(action.get("query") or "")},
                },
            )
        )
        out.append(
            make_event("tool_result", {"call_id": item_id, "output": "done", "is_error": False})
        )
    stamp = _ms(obj.get("timestamp"))
    for event in out:
        if stamp:
            event["ts_ms"] = stamp
    return out


def read_codex_subagent(path: Path, meta: dict[str, Any]) -> CodexSubagent:
    """One sub-agent's own conversation and how it stands, from its rollout."""
    payload = meta.get("payload") if isinstance(meta.get("payload"), dict) else {}
    agent = CodexSubagent(
        thread_id=str(payload.get("id") or ""),
        parent_thread_id=str(payload.get("parent_thread_id") or ""),
        nickname=str(payload.get("agent_nickname") or ""),
        role=str(payload.get("agent_role") or ""),
        path=str(payload.get("agent_path") or ""),
        started_ms=_ms(payload.get("timestamp")) or _ms(meta.get("timestamp")),
    )
    try:
        start = int(payload.get("subagent_history_start_ordinal") or 0)
    except (TypeError, ValueError):  # a missing or bad ordinal means read from the start
        start = 0
    seen: set[str] = set()
    last_text = ""
    status = "running"
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError as exc:
        log.debug("codex rollout unreadable %s: %s", path.name, exc)
        return agent
    with handle:
        for index, line in enumerate(handle):
            if index == 0 or not line.strip():
                continue
            try:
                obj = json.loads(line)
            except ValueError:  # a torn or partial transcript line is skipped
                continue
            if not isinstance(obj, dict):
                continue
            ordinal = obj.get("ordinal")
            if isinstance(ordinal, int) and ordinal < start:
                continue
            agent.updated_ms = max(agent.updated_ms, _ms(obj.get("timestamp")))
            if obj.get("type") == "event_msg":
                event = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
                etype = str(event.get("type") or "")
                if etype == "task_started":
                    status = "running"
                elif etype == "task_complete":
                    status = "done"
                    message = event.get("last_agent_message")
                    if isinstance(message, str) and message.strip():
                        last_text = message
                elif etype == "turn_aborted":
                    status = "stopped"
                elif etype == "error":
                    status = "failed"
                    last_text = str(event.get("message") or last_text)
                continue
            if obj.get("type") != "response_item":
                continue
            for event in _step_events(obj, seen):
                if event["kind"] == "assistant_text":
                    last_text = str(event["payload"]["text"])
                agent.events.append(event)
    agent.status = status
    agent.summary = last_text
    return agent


def codex_subagents(
    parent_thread_id: str, *, since_ms: int, homes: list[Path] | None = None
) -> list[CodexSubagent]:
    """Every sub-agent below ``parent_thread_id``, nested ones after their parent."""
    if not _THREAD_ID.match(parent_thread_id or ""):
        return []
    children: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
    for path in _rollouts_since(homes if homes is not None else codex_homes(), since_ms):
        meta = _first_line(path)
        payload = meta.get("payload") if meta and isinstance(meta.get("payload"), dict) else None
        if not payload or payload.get("thread_source") not in (None, "subagent"):
            continue
        parent = str(payload.get("parent_thread_id") or "")
        if parent and payload.get("id"):
            children.setdefault(parent, []).append((path, meta))  # type: ignore[arg-type]
    out: list[CodexSubagent] = []

    def walk(thread_id: str, depth: int) -> None:
        if depth > _MAX_DEPTH:
            return
        for path, meta in sorted(children.get(thread_id, []), key=lambda row: row[0].name):
            agent = read_codex_subagent(path, meta)
            out.append(agent)
            walk(agent.thread_id, depth + 1)

    walk(parent_thread_id, 0)
    return out
