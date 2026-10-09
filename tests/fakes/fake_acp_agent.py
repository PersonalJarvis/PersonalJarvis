"""A scripted Agent Client Protocol agent on stdio, for runtime tests.

Run as ``python tests/fakes/fake_acp_agent.py``. It speaks just enough ACP
(``initialize``, ``session/new``, ``session/load``, ``session/prompt``) for
the CLI runner's ACP path, the way Hermes and OpenClaw do, without a model.

Environment:

* ``FAKE_ACP_STORE`` — JSON file holding known session ids and their prompt
  history, so ``session/load`` works across processes like a real runtime.
* ``FAKE_ACP_LOG`` — optional file every received frame is appended to.

What a prompt does depends on its text:

* contains ``TOOL`` — one ``tool_call`` named ``mcp_jarvis_society_memory_recall``
  then its completed ``tool_call_update``, then text;
* contains ``ASK`` — a ``session/request_permission`` first; the answer's
  option id is echoed in the reply;
* contains ``FAIL`` — the prompt request answers a JSON-RPC error;
* contains ``HANG`` — answers, then keeps running after stdin closes (a
  runtime whose child holds the pipe);
* otherwise — ``echo: <text>`` streamed in two chunks plus a thought.

``session/load`` of an unknown id answers ``{}`` (Hermes' behaviour).
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from http.client import HTTPConnection
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def _store_path() -> Path | None:
    raw = os.environ.get("FAKE_ACP_STORE")
    return Path(raw) if raw else None


def _load_store() -> dict[str, list[str]]:
    path = _store_path()
    if path is None or not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_store(store: dict[str, list[str]]) -> None:
    path = _store_path()
    if path is not None:
        path.write_text(json.dumps(store), encoding="utf-8")


def _send(frame: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(frame) + "\n")
    sys.stdout.flush()


def _update(session_id: str, update: dict[str, Any]) -> None:
    _send(
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"sessionId": session_id, "update": update},
        }
    )


def _text(session_id: str, text: str, kind: str = "agent_message_chunk") -> None:
    _update(session_id, {"sessionUpdate": kind, "content": {"type": "text", "text": text}})


def _read() -> dict[str, Any] | None:
    line = sys.stdin.readline()
    if not line:
        return None
    log = os.environ.get("FAKE_ACP_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(line)
    return json.loads(line)


def _prompt(rid: Any, session_id: str, text: str, store: dict[str, list[str]]) -> None:
    store.setdefault(session_id, []).append(text)
    _save_store(store)
    if "EOF_PARTIAL" in text:
        _text(session_id, "partial only")
        raise SystemExit(0)
    if "WAIT_CANCEL" in text:
        _text(session_id, "waiting")
        while frame := _read():
            if frame.get("method") == "session/cancel":
                _send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "cancelled"}})
                return
        return
    if endpoint := os.environ.get("FAKE_ACP_GATEWAY"):
        url = urlsplit(endpoint)
        assert url.scheme == "http" and url.hostname == "127.0.0.1"
        connection = HTTPConnection(url.hostname, url.port, timeout=5)
        try:
            connection.request(
                "POST",
                url.path + "/chat/completions",
                body=json.dumps(
                    {
                        "model": "fake-model",
                        "messages": [{"role": "user", "content": text}],
                    }
                ),
                headers={
                    "Authorization": "Bearer " + os.environ["FAKE_ACP_TOKEN"],
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            if response.status < 400:
                result = json.load(response)
                _text(session_id, result["choices"][0]["message"]["content"])
            else:
                # Reproduce runtimes that retry for minutes or render an
                # upstream failure as a normal, completed assistant response.
                if os.environ.get("FAKE_ACP_RETRY_WAIT"):
                    time.sleep(600)
                _text(
                    session_id, "custom rate-limited every one of 3 attempts; hermes fallback add"
                )
        finally:
            connection.close()
        _send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
        return
    if "FAIL" in text:
        _send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": "model down"}})
        return
    if "ASK" in text:
        _send(
            {
                "jsonrpc": "2.0",
                "id": "perm-1",
                "method": "session/request_permission",
                "params": {
                    "sessionId": session_id,
                    "toolCall": {"toolCallId": "tc-ask", "title": "terminal", "kind": "execute"},
                    "options": [
                        {"optionId": "yes", "name": "Allow", "kind": "allow_once"},
                        {"optionId": "always", "name": "Always", "kind": "allow_always"},
                        {"optionId": "no", "name": "Deny", "kind": "reject_once"},
                    ],
                },
            }
        )
        answer = _read() or {}
        outcome = (answer.get("result") or {}).get("outcome") or {}
        _text(session_id, f"permission: {outcome.get('optionId') or outcome.get('outcome')}")
    elif "TOOL" in text:
        _update(
            session_id,
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "tc-1",
                "title": "mcp_jarvis_society_memory_recall",
                "kind": "other",
                "status": "in_progress",
                "rawInput": {"query": "hi"},
            },
        )
        _update(
            session_id,
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "tc-1",
                "status": "completed",
                "content": [{"type": "content", "content": {"type": "text", "text": "recalled"}}],
            },
        )
        _text(session_id, "used the tool")
    else:
        _text(session_id, "thinking", kind="agent_thought_chunk")
        _text(session_id, "echo: ")
        _text(session_id, text[-80:])
    _update(session_id, {"sessionUpdate": "usage_update", "used": 12, "size": 1000})
    _send(
        {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "stopReason": "max_tokens" if "TRUNCATE" in text else "end_turn",
                "usage": {"inputTokens": 11, "outputTokens": 7, "totalTokens": 18},
            },
        }
    )
    if "HANG" in text:
        time.sleep(600)


def main() -> int:
    store = _load_store()
    while True:
        frame = _read()
        if frame is None:
            return 0
        method = frame.get("method")
        rid = frame.get("id")
        params = frame.get("params") or {}
        if method == "initialize":
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "protocolVersion": 1,
                        "agentInfo": {"name": "fake-acp", "version": "1.0.0"},
                        "agentCapabilities": {"loadSession": True},
                    },
                }
            )
        elif method == "session/new":
            session_id = uuid.uuid4().hex
            store[session_id] = []
            _save_store(store)
            _send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": session_id}})
        elif method == "session/load":
            session_id = str(params.get("sessionId"))
            if session_id not in store:
                _send({"jsonrpc": "2.0", "id": rid, "result": {}})
                continue
            for old in store[session_id]:
                _text(session_id, old, kind="user_message_chunk")
                _text(session_id, "replayed answer")
            _send({"jsonrpc": "2.0", "id": rid, "result": {"modes": None, "_meta": {}}})
        elif method == "session/prompt":
            blocks = params.get("prompt") or []
            text = "".join(str(b.get("text") or "") for b in blocks if isinstance(b, dict))
            _prompt(rid, str(params.get("sessionId")), text, store)
        elif rid is not None:
            _send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "nope"}})


if __name__ == "__main__":
    sys.exit(main())
