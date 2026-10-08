"""A stand-in for the ``openclaw`` CLI, for Gateway supervision tests.

Run as ``python tests/fakes/fake_openclaw.py <args>``. Understands:

* ``--version`` — prints ``OpenClaw 2026.9.8 (fake)``;
* ``gateway run --port N`` — serves ``GET /healthz`` (200) on 127.0.0.1:N
  until killed; exits 1 at once when the port is taken;
* ``doctor --fix ...`` — writes ``doctor.done`` into the working folder.

``FAKE_OPENCLAW_MODE``:

* ``config-once`` — the Gateway exits 78 (``EX_CONFIG``) until doctor ran;
* ``doctor-hang`` — doctor writes its pid to ``doctor.pid`` and sleeps.
"""

from __future__ import annotations

import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


class _Health(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — http.server API
        ok = self.path == "/healthz"
        self.send_response(200 if ok else 404)
        self.end_headers()
        self.wfile.write(b"ok" if ok else b"no")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


def main(argv: list[str]) -> int:
    mode = os.environ.get("FAKE_OPENCLAW_MODE", "")
    if argv[:1] == ["--version"]:
        print("OpenClaw 2026.9.8 (fake)")
        return 0
    if argv[:1] == ["doctor"]:
        if mode == "doctor-hang":
            Path("doctor.pid").write_text(str(os.getpid()), encoding="utf-8")
            time.sleep(600)
        Path("doctor.done").write_text("1", encoding="utf-8")
        return 0
    if argv[:2] == ["gateway", "run"]:
        if mode == "config-once" and not Path("doctor.done").exists():
            return 78
        port = int(argv[argv.index("--port") + 1])
        try:
            server = HTTPServer(("127.0.0.1", port), _Health)
        except OSError:
            return 1  # EADDRINUSE: the port was taken
        server.serve_forever()
        return 0
    print(f"fake openclaw: unsupported {argv}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
