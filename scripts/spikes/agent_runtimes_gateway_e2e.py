"""Live check: a Hermes / OpenClaw agent through Jarvis' model gateway.

Starts the gateway (``jarvis.ui.web.runtime_gateway_routes``) behind the
real ``SurfaceSecurity`` guard on a free loopback port, builds the agent's
route exactly as a chat turn does (``model_map.route_for``), and runs two
turns of a real runtime through Jarvis' own driver against it. The model
calls go to the named Jarvis provider through Jarvis' own plugin (or, for
``openai-codex``, the ChatGPT subscription); the runtime only ever sees the
gateway token. Two model calls per run (a runtime may retry); all runtime
state lands in a temporary folder.

    python scripts/spikes/agent_runtimes_gateway_e2e.py hermes ollama qwen3.5:9b
    python scripts/spikes/agent_runtimes_gateway_e2e.py openclaw openai-codex
    python scripts/spikes/agent_runtimes_gateway_e2e.py hermes openai-codex --replay

``--replay`` keeps ChatGPT out of it (no allowance used): the subscription
answers from a stand-in in its own event format.
"""

from __future__ import annotations

import asyncio
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "spikes"))

from agent_runtimes_e2e import _run  # noqa: E402

from jarvis.agent_runtimes import base, driver, gateway  # noqa: E402
from jarvis.agent_runtimes.base import RuntimeTurn  # noqa: E402
from jarvis.agent_runtimes.model_map import route_for  # noqa: E402
from jarvis.core import runtime_refs  # noqa: E402
from jarvis.core.config import load_config  # noqa: E402


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _serve(port: int) -> None:
    import uvicorn
    from fastapi import FastAPI

    from jarvis.ui.web.runtime_gateway_routes import router
    from jarvis.ui.web.surface_security import SurfaceSecurity

    app = FastAPI()
    app.include_router(router)
    guarded = SurfaceSecurity(app, control_key_validator=lambda token: False)
    config = uvicorn.Config(guarded, host="127.0.0.1", port=port, log_level="warning")
    threading.Thread(target=uvicorn.Server(config).run, daemon=True).start()
    for _ in range(100):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise RuntimeError("the gateway did not start")


class _ReplayUpstream:
    """``--replay``: answers in the subscription's Responses event format
    without calling ChatGPT, to check the runtime side when the allowance is
    used up. It remembers a word the way a model reading its history would."""

    @staticmethod
    def _texts(items: list[dict], role: str = "") -> list[str]:
        out: list[str] = []
        for item in items:
            if role and item.get("role") != role:
                continue
            content = item.get("content")
            if isinstance(content, str):
                out.append(content)
                continue
            for part in content or []:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    out.append(part["text"])
        return out

    async def stream(self, **kwargs):  # noqa: ANN003, ANN201 — mirrors SubscriptionReasoning
        texts = self._texts(kwargs["input"])
        # The first turn has no earlier answer in its history; the second
        # must find the word there.
        if not self._texts(kwargs["input"], "assistant"):
            answer = "READY"
        else:
            answer = "PELICAN" if any("PELICAN" in text for text in texts) else "UNKNOWN"
        message = {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": answer, "annotations": []}],
        }
        response = {"id": "resp_1", "object": "response", "model": kwargs["model"]}
        yield {"type": "response.created", "response": {**response, "status": "in_progress"}}
        yield {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {**message, "status": "in_progress", "content": []},
        }
        yield {
            "type": "response.content_part.added",
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "part": {"type": "output_text", "text": "", "annotations": []},
        }
        yield {
            "type": "response.output_text.delta",
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "delta": answer,
        }
        yield {
            "type": "response.output_text.done",
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "text": answer,
        }
        yield {"type": "response.output_item.done", "output_index": 0, "item": message}
        yield {
            "type": "response.completed",
            "response": {
                **response,
                "status": "completed",
                "output": [message],
                "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
            },
        }

    async def list_models(self) -> list[dict]:
        return [{"id": "gpt-replay", "label": "Replay"}]


async def _pick_model(wanted: str) -> str:
    if wanted:
        return wanted
    rows = await gateway._client("").list_models()  # noqa: SLF001 — a catalog call, no inference
    ids = [row["id"] for row in rows]
    light = [model for model in ids if any(word in model for word in ("luna", "mini", "spark"))]
    return (light or ids)[0]


async def main(name: str, provider: str, wanted: str) -> int:
    if wanted == "--replay":
        wanted = "gpt-replay"
        upstream = _ReplayUpstream()
        gateway._client = lambda account_id: upstream  # noqa: SLF001 — the spike's stand-in
        gateway.subscription_ready = lambda account_id="": True
    tmp = Path(tempfile.mkdtemp(prefix=f"jarvis-{name}-sub-e2e-"))
    base.runtimes_root = lambda: tmp / "agent_runtimes"
    workspace = tmp / "workspace"
    workspace.mkdir()
    status = driver(name).detect(refresh=True)
    print("detect:", status.ready, status.problem)
    if not status.ready:
        return 2
    port = _free_port()
    _serve(port)
    runtime_refs.set_api_base_url(f"http://127.0.0.1:{port}")
    model = await _pick_model(wanted) if provider == "openai-codex" else wanted
    route = await asyncio.to_thread(
        route_for, load_config(), provider, model, agent_id="sub-e2e-agent"
    )
    print("route:", route.provider, route.model, route.transport, route.base_url)
    turn = RuntimeTurn(
        agent_id="sub-e2e-agent",
        agent_name="Probe",
        session_id="society:sub-e2e-agent",
        workspace=workspace,
        route=route,
        resume=None,
        auto_approve=True,
        mcp_url=None,
        control_key=None,
    )
    first, _io1 = await _run(name, turn, "Remember the word PELICAN. Reply with exactly: READY")
    print("turn 1:", first.status, first.error, repr(first.result_text[:120]))
    turn.resume = first.vendor_session
    second, _io2 = await _run(name, turn, "Which word did I ask you to remember? One word.")
    print("turn 2:", second.status, second.error, repr(second.result_text[:120]))
    await driver(name).stop()
    ok = (
        first.status == "done"
        and second.status == "done"
        and "pelican" in second.result_text.lower()
    )
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    args = [*sys.argv[1:], "", "", ""]
    sys.exit(asyncio.run(main(args[0] or "hermes", args[1] or "openai-codex", args[2])))
