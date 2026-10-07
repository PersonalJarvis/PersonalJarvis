"""Scripted OpenAI-compatible chat-completions server for runtime tests.

External agent runtimes (Hermes, OpenClaw) accept any OpenAI-compatible
base URL, so this stdlib-only server lets tests and the upstream canary
drive a real runtime turn without a paid key.

Behaviour, decided from the latest user message:

* ``CALL_TOOL <name> <json-args>`` -> answers with one tool call to ``<name>``
  when the request offers a tool whose name ends with ``<name>``.
* after a ``tool`` role message -> answers ``tool done: <first 80 chars>``.
* anything else -> answers ``pong: <message>``.

Both streaming (SSE) and non-streaming responses are supported. Every
request body is recorded on ``server.requests`` for assertions.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MODEL_ID = "fake-model"


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(part.get("text", "")) for part in content if isinstance(part, dict)
        )
    return ""


def _decide(body: dict[str, Any]) -> dict[str, Any]:
    """Return ``{"text": str}`` or ``{"tool": name, "args": str}``."""
    messages = body.get("messages") or []
    if messages and messages[-1].get("role") == "tool":
        return {"text": "tool done: " + _text_of(messages[-1].get("content"))[:80]}
    # The person's turn may arrive as several user messages (a runtime can
    # append its own context block after the typed text): read them all.
    pending: list[str] = []
    for message in reversed(messages):
        if message.get("role") != "user":
            break
        pending.insert(0, _text_of(message.get("content")).strip())
    last_user = "\n".join(pending)
    marker = last_user.rfind("CALL_TOOL ")
    if marker >= 0:
        rest = last_user[marker + len("CALL_TOOL ") :].strip().splitlines()[0]
        name, _, args = rest.partition(" ")
        offered = [
            str((tool.get("function") or {}).get("name", ""))
            for tool in body.get("tools") or []
        ]
        match = next((tool for tool in offered if tool.endswith(name)), "")
        if match:
            return {"tool": match, "args": args.strip() or "{}"}
        return {"text": f"no tool named {name} offered"}
    return {"text": "pong: " + last_user[-200:]}


class _Handler(BaseHTTPRequestHandler):
    server: FakeOpenAIServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return  # keep test output quiet; requests are recorded instead

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            self._json(200, {"object": "list", "data": [{"id": MODEL_ID, "object": "model"}]})
            return
        self._json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": {"message": "bad json"}})
            return
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._json(404, {"error": {"message": f"unsupported path {self.path}"}})
            return
        self.server.requests.append(body)
        decision = _decide(body)
        if body.get("stream"):
            self._stream(decision)
        else:
            self._json(200, _completion(decision))

    def _stream(self, decision: dict[str, Any]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        created = int(time.time())
        if "tool" in decision:
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_fake_1",
                        "type": "function",
                        "function": {"name": decision["tool"], "arguments": decision["args"]},
                    }
                ],
            }
            finish = "tool_calls"
        else:
            delta = {"role": "assistant", "content": decision["text"]}
            finish = "stop"
        chunks = [
            {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
            {
                "choices": [],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        ]
        for chunk in chunks:
            chunk.update(
                {
                    "id": "chatcmpl-fake",
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": MODEL_ID,
                }
            )
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def _completion(decision: dict[str, Any]) -> dict[str, Any]:
    if "tool" in decision:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_fake_1",
                    "type": "function",
                    "function": {"name": decision["tool"], "arguments": decision["args"]},
                }
            ],
        }
        finish = "tool_calls"
    else:
        message = {"role": "assistant", "content": decision["text"]}
        finish = "stop"
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


class FakeOpenAIServer(ThreadingHTTPServer):
    """Run with ``with FakeOpenAIServer() as server: server.base_url``."""

    daemon_threads = True

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        super().__init__((host, port), _Handler)
        self.requests: list[dict[str, Any]] = []
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/v1"

    def __enter__(self) -> FakeOpenAIServer:
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown()
        self.server_close()
