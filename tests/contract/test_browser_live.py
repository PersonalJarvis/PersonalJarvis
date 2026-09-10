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

pytestmark = pytest.mark.skipif(
    not os.environ.get("JARVIS_BROWSER_TEST_PYTHON"), reason="requires isolated Browser-Use runtime"
)


class PageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/download":
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="fixture.txt"')
            self.end_headers()
            self.wfile.write(b"isolated browser download")
            return
        body = b"""<!doctype html><title>Live browser fixture</title>
        <input aria-label="Name"><a href="/download">Download fixture</a>
        <form method="POST" action="/upload" enctype="multipart/form-data">
        <input type="file" name="attachment" aria-label="Upload" onchange="this.form.requestSubmit()"></form>
        <h1 id="counter">0</h1>
        <script>let n=0;function paint(){document.querySelector('#counter').textContent=++n;
        document.body.style.background=n%2?'#fdd':'#ddf';requestAnimationFrame(paint)}
        requestAnimationFrame(paint)</script>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # Fixture traffic must not fill test output.

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Upload received: " + body)


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
    agent = SimpleNamespace(
        agent_id="scout", model="", browser_allowed_domains=["http*://127.0.0.1"]
    )
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


async def test_takeover_pauses_and_resumes_the_same_browser_job(live, site):
    import json
    import contextvars

    agent = SimpleNamespace(
        agent_id="paused", model="", browser_allowed_domains=["http*://127.0.0.1"]
    )
    first_call = asyncio.Event()
    release = asyncio.Event()
    calls = []
    turn_context = contextvars.ContextVar("browser_test_turn", default="")
    await live.ensure(agent)  # A viewer may have started the reader before the turn.
    token = turn_context.set("active-turn")

    async def model(payload):
        assert turn_context.get() == "active-turn"
        calls.append(payload)
        if payload["schema"].get("title") == "JudgementResult":
            return {
                "ok": True,
                "text": json.dumps({"verdict": True, "reasoning": "The test finished"}),
            }
        if len(calls) == 1:
            first_call.set()
            await release.wait()
            action = {"wait": {"seconds": 1}}
        else:
            action = {"done": {"text": "Resumed successfully", "success": True}}
        return {
            "ok": True,
            "text": json.dumps(
                {
                    "thinking": "",
                    "evaluation_previous_goal": "Continue",
                    "memory": "Local test",
                    "next_goal": "Finish",
                    "action": [action],
                }
            ),
        }

    async def apply(payload):
        result = await payload["apply"]()
        return {"ok": not bool(result.get("error")), "error": result.get("error")}

    job = asyncio.create_task(
        live.run(
            agent, task="Wait once, then finish", max_steps=4, llm=model, action=apply, vision=False
        )
    )
    try:
        await asyncio.wait_for(first_call.wait(), 45)
        session = live.sessions[agent.agent_id]
        takeover = asyncio.create_task(live.control(session, "user", "takeover", {"enabled": True}))
        await asyncio.sleep(0.1)
        assert not takeover.done()
        release.set()
        await asyncio.wait_for(takeover, 30)
        assert not job.done()
        assert len(calls) == 1
        await live.control(session, "user", "navigate", {"url": site})
        await live.control(session, "user", "takeover", {"enabled": False})
        result = await asyncio.wait_for(job, 45)
        assert result["ok"], result
        planning = [p for p in calls if "action" in p["schema"].get("properties", {})]
        assert len(planning) == 2
        observed = "\n".join(
            str(m.get("content")) for m in planning[-1]["messages"] if m["role"] == "user"
        )
        assert site in observed and "Name" in observed
        assert not session.closed
    finally:
        turn_context.reset(token)
        release.set()
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
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


async def test_agent_download_is_a_current_task_workspace_artifact(live, site):
    import json
    import re

    agent = SimpleNamespace(
        agent_id="files", model="", browser_allowed_domains=["http*://127.0.0.1"]
    )
    calls = 0
    effects = []

    async def model(payload):
        nonlocal calls
        if payload["schema"].get("title") == "JudgementResult":
            return {"ok": True, "text": json.dumps({"verdict": True, "reasoning": "Downloaded"})}
        calls += 1
        if calls == 1:
            observation = "\n".join(
                str(m.get("content")) for m in payload["messages"] if m["role"] == "user"
            )
            link = re.search(r"\[(\d+)\]<a\b", observation)
            assert link, observation
            action = {"click": {"index": int(link[1])}}
        else:
            action = {"done": {"text": "Downloaded", "success": True}}
        return {
            "ok": True,
            "text": json.dumps(
                {
                    "thinking": "",
                    "evaluation_previous_goal": "Ready",
                    "memory": "",
                    "next_goal": "Finish",
                    "action": [action],
                }
            ),
        }

    async def apply(payload):
        result = await payload["apply"]()
        effects.append(result)
        return {"ok": not bool(result.get("error")), "error": result.get("error")}

    try:
        session = await live.ensure(agent)
        await live.control(session, "viewer", "takeover", {"enabled": True})
        await live.control(session, "viewer", "navigate", {"url": site})
        await live.control(session, "viewer", "takeover", {"enabled": False})
        result = await live.run(
            agent, task="Download the fixture", max_steps=3, llm=model, action=apply, vision=False
        )
        assert result["ok"], result
        assert len(result["artifacts"]) == 1, (
            result,
            effects,
            list((live.data_dir / "society" / "files" / "workspace").rglob("*")),
            session.stderr_tail[-1500:],
        )
        path = Path(result["artifacts"][0])
        assert path.is_relative_to(live.data_dir / "society" / "files" / "workspace")
        assert path.read_bytes() == b"isolated browser download"
        again = await live.run(
            agent,
            task="Finish without downloading",
            max_steps=2,
            llm=model,
            action=apply,
            vision=False,
        )
        assert again["ok"] and again["artifacts"] == []
    finally:
        await live.close()


async def test_agent_uploads_only_the_supplied_workspace_file(live, site):
    import json
    import re

    agent = SimpleNamespace(
        agent_id="upload", model="", browser_allowed_domains=["http*://127.0.0.1"]
    )
    upload = live.data_dir / "society" / "upload" / "workspace" / "upload.txt"
    calls = 0

    async def model(payload):
        nonlocal calls
        if payload["schema"].get("title") == "JudgementResult":
            return {
                "ok": True,
                "text": json.dumps({"verdict": True, "reasoning": "Upload verified"}),
            }
        calls += 1
        observation = "\n".join(
            str(m.get("content")) for m in payload["messages"] if m["role"] == "user"
        )
        if calls == 1:
            field = re.search(r"\[(\d+)\]<input[^\n]*type=file", observation)
            assert field, observation
            action = {"upload_file": {"index": int(field[1]), "path": str(upload)}}
        else:
            assert "Upload received" in observation and "isolated upload proof" in observation
            action = {"done": {"text": "Upload verified", "success": True}}
        return {
            "ok": True,
            "text": json.dumps(
                {
                    "thinking": "",
                    "evaluation_previous_goal": "Ready",
                    "memory": "",
                    "next_goal": "Finish",
                    "action": [action],
                }
            ),
        }

    async def apply(payload):
        result = await payload["apply"]()
        return {"ok": not bool(result.get("error")), "error": result.get("error")}

    try:
        session = await live.ensure(agent)
        upload.write_text("isolated upload proof", encoding="utf-8")
        await live.control(session, "viewer", "takeover", {"enabled": True})
        await live.control(session, "viewer", "navigate", {"url": site})
        await live.control(session, "viewer", "takeover", {"enabled": False})
        result = await live.run(
            agent,
            task="Upload the supplied file",
            max_steps=3,
            llm=model,
            action=apply,
            vision=False,
            files=[str(upload)],
        )
        assert result["ok"], result
        assert calls >= 2
    finally:
        await live.close()


async def test_cancelled_takeover_does_not_pause_the_job_later(live):
    import json

    agent = SimpleNamespace(agent_id="disconnected", model="", browser_allowed_domains=[])
    thinking = asyncio.Event()
    release = asyncio.Event()

    async def model(payload):
        if payload["schema"].get("title") == "JudgementResult":
            return {"ok": True, "text": json.dumps({"verdict": True, "reasoning": "Finished"})}
        thinking.set()
        await release.wait()
        return {
            "ok": True,
            "text": json.dumps(
                {
                    "thinking": "",
                    "evaluation_previous_goal": "Ready",
                    "memory": "",
                    "next_goal": "Finish",
                    "action": [{"done": {"text": "Finished", "success": True}}],
                }
            ),
        }

    async def apply(payload):
        await payload["apply"]()
        return {"ok": True}

    job = asyncio.create_task(
        live.run(agent, task="Finish", max_steps=2, llm=model, action=apply, vision=False)
    )
    try:
        await asyncio.wait_for(thinking.wait(), 45)
        session = live.sessions[agent.agent_id]
        takeover = asyncio.create_task(live.control(session, "gone", "takeover", {"enabled": True}))
        await asyncio.sleep(0.2)
        assert not takeover.done()
        takeover.cancel()
        await asyncio.gather(takeover, return_exceptions=True)
        release.set()
        assert (await asyncio.wait_for(job, 30))["ok"]
        assert session.control_owner is None
        # Let the cancelled worker-side request finish; it must not acquire control late.
        await asyncio.sleep(0.2)
        assert (
            await live.run(
                agent, task="Finish again", max_steps=2, llm=model, action=apply, vision=False
            )
        )["ok"]
    finally:
        release.set()
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
        await live.close()


async def test_idle_animation_stream_soak(live, site, record_property):
    import time

    agent = SimpleNamespace(
        agent_id="soak", model="", browser_allowed_domains=["http*://127.0.0.1"]
    )
    try:
        session, queue = await live.subscribe(agent)
        await live.control(session, "viewer", "takeover", {"enabled": True})
        await live.control(session, "viewer", "navigate", {"url": site})
        await live.control(session, "viewer", "takeover", {"enabled": False})
        duration = float(os.environ.get("JARVIS_BROWSER_SOAK_SECONDS", "5"))
        # Measure steady rendering separately from the first target attachment.
        first_frame_started = time.monotonic()
        while True:
            event = await asyncio.wait_for(queue.get(), 5)
            if event["kind"] == "frame":
                break
        record_property("first_frame_ms", round((time.monotonic() - first_frame_started) * 1000, 2))
        started = time.monotonic()
        ages = []
        changed = set()
        while time.monotonic() - started < duration:
            event = await asyncio.wait_for(queue.get(), 5)
            if event["kind"] == "frame":
                ages.append(max(0, time.time() - event["timestamp"]))
                changed.add(event["data"])
        fps = len(ages) / (time.monotonic() - started)
        p95 = sorted(ages)[int(len(ages) * 0.95)]
        record_property("stream_fps", round(fps, 2))
        record_property("capture_to_backend_p95_ms", round(p95 * 1000, 2))
        record_property("duration_seconds", duration)
        assert fps >= 10, f"Only {fps:.2f} frames/s"
        assert p95 <= 0.5, f"Frame age p95: {p95:.3f}s"
        assert len(changed) >= duration * 5
        assert not session.run_lock.locked()
    finally:
        await live.close()
