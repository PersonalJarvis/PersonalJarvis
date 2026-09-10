"""Additive SQLite state and idempotency receipts, alongside the original chat log."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .control_types import ChatControlState, CommandRequest, CommandResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_chat_controls (
    session_id TEXT PRIMARY KEY,
    state_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_chat_command_receipts (
    session_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    result_json TEXT,
    PRIMARY KEY (session_id, request_id)
);
CREATE TABLE IF NOT EXISTS agent_chat_goal_inputs (
    session_id TEXT PRIMARY KEY,
    data_json TEXT NOT NULL
);
"""


class ControlStore:
    def __init__(self, chat_store: Any) -> None:
        # This is the chat package's existing connection/lock, including :memory: tests.
        self._db = chat_store._conn
        self._lock = chat_store._lock
        with self._lock:
            self._db.executescript(_SCHEMA)
            for row in self._db.execute(
                "SELECT session_id, state_json FROM agent_chat_controls"
            ).fetchall():
                state = ChatControlState.model_validate_json(row["state_json"])
                if state.goal and state.goal.status == "active":
                    state.goal.status = "paused"
                    state.goal.reason = "App session ended; use /continue to resume."
                    state.revision += 1
                    self._db.execute(
                        "UPDATE agent_chat_controls SET state_json=? WHERE session_id=?",
                        (state.model_dump_json(), state.session_id),
                    )
            self._db.commit()

    def get(self, session_id: str) -> ChatControlState:
        with self._lock:
            row = self._db.execute(
                "SELECT state_json FROM agent_chat_controls WHERE session_id=?", (session_id,)
            ).fetchone()
        return (
            ChatControlState.model_validate_json(row[0])
            if row
            else ChatControlState(session_id=session_id)
        )

    def save(self, state: ChatControlState) -> None:
        state.revision += 1
        with self._lock:
            self._db.execute(
                "INSERT INTO agent_chat_controls VALUES (?, ?) ON CONFLICT(session_id) "
                "DO UPDATE SET state_json=excluded.state_json",
                (state.session_id, state.model_dump_json()),
            )
            self._db.commit()

    def save_inputs(self, sid: str, attachments: list[dict]) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO agent_chat_goal_inputs VALUES (?, ?) ON CONFLICT(session_id) "
                "DO UPDATE SET data_json=excluded.data_json",
                (sid, json.dumps(attachments, ensure_ascii=False)),
            )
            self._db.commit()

    def inputs(self, sid: str) -> list[dict]:
        with self._lock:
            row = self._db.execute(
                "SELECT data_json FROM agent_chat_goal_inputs WHERE session_id=?", (sid,)
            ).fetchone()
        return json.loads(row[0]) if row else []

    def delete(self, sid: str) -> None:
        with self._lock:
            for statement in (
                "DELETE FROM agent_chat_controls WHERE session_id=?",
                "DELETE FROM agent_chat_goal_inputs WHERE session_id=?",
                "DELETE FROM agent_chat_command_receipts WHERE session_id=?",
            ):
                self._db.execute(statement, (sid,))
            self._db.commit()

    def claim(self, session_id: str, request: CommandRequest) -> CommandResult | None:
        fingerprint = hashlib.sha256(
            json.dumps(
                [request.command, request.arguments, request.attachments],
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        with self._lock:
            row = self._db.execute(
                "SELECT fingerprint, result_json FROM agent_chat_command_receipts "
                "WHERE session_id=? AND request_id=?",
                (session_id, request.request_id),
            ).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise ValueError("This request id already belongs to another command")
                if row[1] is None:
                    raise ValueError(
                        "Command was interrupted or is still running; "
                        "inspect its result before retrying"
                    )
                return CommandResult.model_validate_json(row[1])
            self._db.execute(
                "INSERT INTO agent_chat_command_receipts VALUES (?, ?, ?, NULL)",
                (session_id, request.request_id, fingerprint),
            )
            self._db.commit()
        return None

    def finish(self, session_id: str, result: CommandResult) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE agent_chat_command_receipts SET result_json=? "
                "WHERE session_id=? AND request_id=?",
                (result.model_dump_json(), session_id, result.request_id),
            )
            self._db.commit()
