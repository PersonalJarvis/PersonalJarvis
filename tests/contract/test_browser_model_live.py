"""Opt-in end-to-end tasks using real model access and the managed browser."""
from __future__ import annotations
import asyncio
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.config import SafetyConfig, load_config
from jarvis.core.protocols import ExecutionContext
from jarvis.brain.resolver import resolve_browser_brain
from jarvis.safety.approval import ApprovalWorkflow
from jarvis.safety.risk_tier import RiskTierEvaluator
from jarvis.safety.tool_executor import ToolExecutor
from jarvis.society.runtime import SocietyRuntime
from jarvis.society.browser.bridge import execute_live

pytestmark = pytest.mark.skipif(os.environ.get("JARVIS_BROWSER_MODEL_TEST") != "1",
                               reason="requires authorized live model access")

class FormHandler(BaseHTTPRequestHandler):
    received = []
    def do_GET(self):
        body = b"""<!doctype html><title>Browser task fixture</title><h1>Test form</h1>
        <form method="post" action="/submit"><label>Name <input name="name"></label>
        <button type="submit">Save test form</button></form>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.received.append(data)
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h1>Test form saved successfully</h1>")
    def log_message(self, *args):
        pass  # Deterministic fixture; request logs add no test evidence.

async def test_actual_model_submits_fixture_form(tmp_path):
    provider = os.environ.get("JARVIS_BROWSER_MODEL_PROVIDER", "openai")
    data = Path(os.environ["JARVIS_BROWSER_MODEL_DATA"])
    runtime = SocietyRuntime(data, seed_starter_team=False)
    await runtime.ensure_started()
    name = "Browser test " + uuid4().hex[:7]
    agent, _ = await runtime.roster.create(
        name=name, provider=provider, model="", browser_allowed_domains=["http*://127.0.0.1"],
        permission_ceiling="monitor", approval_rules={"always_allow": ["core:browser"]},
    )
    cfg = load_config()
    runtime.browser.live.model_resolver = lambda a: resolve_browser_brain(cfg, a.provider, a.model)
    bus = EventBus()
    runtime.browser.live.executor = ToolExecutor(
        bus, RiskTierEvaluator(SafetyConfig()), ApprovalWorkflow(bus)
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), FormHandler)
    FormHandler.received = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ctx = ExecutionContext(uuid4(), "Fill the disposable test form", {}, None)
    try:
        result = await asyncio.wait_for(execute_live(runtime, agent, runtime.browser, {
            "task": "Fill the Name field with Ada, click Save test form, then report the success heading.",
            "url": f"http://127.0.0.1:{server.server_port}", "max_steps": 8,
        }, ctx), timeout=240)
        assert result.success, result.error or result.output
        assert any(b"name=Ada" in body for body in FormHandler.received), result.output
        session = runtime.browser.live.sessions[agent.agent_id]
        assert not session.closed
    finally:
        await runtime.close()
        server.shutdown()
        server.server_close()
        thread.join()

