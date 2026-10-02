"""A loopback HTTP server that streams scripted Ollama ``/api/chat`` replies.

The voice engine talks to Ollama with the standard library's ``http.client``
(it runs in its own environment without httpx), so this fake is a real socket
server on 127.0.0.1 with an ephemeral port. Each POST to ``/api/chat`` pops the
next scripted reply and streams it as NDJSON; every request body is recorded.

Usage::

    with FakeOllamaChatStream() as fake:
        fake.script([{"message": {"content": "Hi."}, "done": False}, {"done": True}])
        chat = OllamaChat("m", base_url=fake.base_url)
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

__all__ = ["FakeOllamaChatStream"]


class FakeOllamaChatStream:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self._replies: list[tuple[int, list[dict[str, Any]] | str]] = []
        self._lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                del format, args  # keep test output quiet

            def do_POST(self) -> None:  # noqa: N802 - http.server naming
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                with fake._lock:
                    fake.requests.append({"path": self.path, "body": body})
                    status, reply = fake._replies.pop(0) if fake._replies else (200, [])
                if isinstance(reply, str):
                    payload = reply.encode("utf-8")
                    self.send_response(status)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                self.send_response(status)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                for event in reply:
                    self.wfile.write((json.dumps(event) + "\n").encode("utf-8"))
                    self.wfile.flush()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def script(self, events: list[dict[str, Any]], *, status: int = 200) -> None:
        """Queue one streamed reply."""
        self._replies.append((status, events))

    def script_error(self, status: int, body: str) -> None:
        """Queue one plain error reply (e.g. Ollama's 400 for an unsupported option)."""
        self._replies.append((status, body))

    def __enter__(self) -> FakeOllamaChatStream:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
