"""Actual Browser-Use/Chromium checks; opt in with an isolated installed runtime."""
from __future__ import annotations
import asyncio
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
import pytest

from jarvis.society.browser import install
from jarvis.society.browser.live import LiveSessions

pytestmark = pytest.mark.skipif(not os.environ.get("JARVIS_BROWSER_TEST_PYTHON"),
                               reason="requires isolated Browser-Use runtime")

class PageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"""<!doctype html><title>Live browser fixture</title>
        <input aria-label="Name"><h1 id="counter">0</h1>
        <script>let n=0;setInterval(()=>{document.querySelector('#counter').textContent=++n;
        document.body.style.background=n%2?'#fdd':'#ddf'},60)</script>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass  # Fixture traffic must not fill test output.

@pytest.fixture
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), PageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join()

@pytest.fixture
def live(tmp_path, monkeypatch):
    python = Path(os.environ["JARVIS_BROWSER_TEST_PYTHON"])
    binary = Path(os.environ["JARVIS_BROWSER_TEST_EXECUTABLE"])
    monkeypatch.setattr(install, "is_installed", lambda _: True)
    monkeypatch.setattr(install, "venv_python", lambda _: python)
    monkeypatch.setattr(install, "browser_executable", lambda _: binary)
    return LiveSessions(tmp_path)

async def test_live_pixels_change_between_tasks_and_sessions_stay_open(live, site):
    agent = SimpleNamespace(agent_id="scout", model="", browser_allowed_domains=["http*://127.0.0.1"])
    try:
        session, queue = await live.subscribe(agent)
        await live.control(session, "viewer", "takeover", {"enabled": True})
        await live.control(session, "viewer", "navigate", {"url": site})
        await live.control(session, "viewer", "takeover", {"enabled": False})
        frames = []
        deadline = asyncio.get_running_loop().time() + 10
        while len(frames) < 20 and asyncio.get_running_loop().time() < deadline:
            event = await asyncio.wait_for(queue.get(), 5)
            if event["kind"] == "frame":
                frames.append(event)
        assert len(frames) >= 20
        assert len({f["data"] for f in frames}) > 5
        assert not session.run_lock.locked()
        assert await live.ensure(agent) is session
        await live.unsubscribe(session, queue, "viewer")
        assert not session.closed
    finally:
        await live.close()

async def test_manual_control_has_one_owner(live):
    agent = SimpleNamespace(agent_id="second", model="", browser_allowed_domains=[])
    try:
        session = await live.ensure(agent)
        await live.control(session, "one", "takeover", {"enabled": True})
        with pytest.raises(ValueError, match="another viewer"):
            await live.control(session, "two", "takeover", {"enabled": True})
        with pytest.raises(ValueError, match="control first"):
            await live.control(session, "two", "text", {"text": "forbidden"})
    finally:
        await live.close()
