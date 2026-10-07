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

``--replay`` uses a scripted provider: ``openai-codex`` checks Responses and
``ollama`` checks Chat Completions (no network to a model, no allowance used).
Failed runs retain their diagnostic log under ``eval-results/`` (gitignored).
"""

from __future__ import annotations

import asyncio
import socket
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "spikes"))

from agent_runtimes_e2e import (  # noqa: E402
    _run,
    keep_user_path,
    prepare,
    user_path_snapshot,
)

from jarvis.agent_runtimes import base, driver, gateway  # noqa: E402
from jarvis.agent_runtimes.base import RuntimeTurn  # noqa: E402
from jarvis.agent_runtimes.model_map import prepare_route  # noqa: E402
from jarvis.core import runtime_refs  # noqa: E402
from jarvis.core.config import load_config  # noqa: E402


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _serve(port: int):
    import uvicorn
    from fastapi import FastAPI

    from jarvis.ui.web.runtime_gateway_routes import router
    from jarvis.ui.web.surface_security import SurfaceSecurity

    app = FastAPI()
    app.include_router(router)
    guarded = SurfaceSecurity(app, control_key_validator=lambda token: False)
    config = uvicorn.Config(guarded, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                yield
                return
            time.sleep(0.05)
        raise RuntimeError("the gateway did not start")
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("the gateway did not stop")


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
        return [{"id": "gpt-replay", "label": "Replay", "context_length": 128000,
                 "max_output_tokens": 8192}]


async def _pick_model(wanted: str) -> str:
    if wanted:
        return wanted
    rows = await gateway._client("").list_models()  # noqa: SLF001 — a catalog call, no inference
    ids = [row["id"] for row in rows]
    light = [model for model in ids if any(word in model for word in ("luna", "mini", "spark"))]
    return (light or ids)[0]


async def main(name: str, provider: str, wanted: str) -> int:
    # This two-turn smoke may cause runtime retries. It cannot enforce a paid
    # one-call budget; refuse paid providers instead of silently overspending.
    if provider not in {"ollama", "local-openai", "openai-codex"}:
        raise ValueError("This multi-turn smoke supports local models and subscriptions only.")
    replay = wanted == "--replay"
    from jarvis.agent_chat import runner_api
    from jarvis.brain.model_catalog import ModelCatalog
    from jarvis.costs import ledger

    previous_client = gateway._client
    previous_ready = gateway.subscription_ready
    previous_build = runner_api.build_brain
    previous_catalog = gateway._CATALOG
    previous_ledger = ledger.ledger_path()
    previous_root = base.runtimes_root
    previous_url = runtime_refs.get_api_base_url()
    if wanted == "--replay":
        wanted = "gpt-replay"
        upstream = _ReplayUpstream()
        gateway._client = lambda account_id: upstream  # noqa: SLF001 — the spike's stand-in
        gateway.subscription_ready = lambda account_id="": True
        from tests.fakes.fake_runtime_brain import ReplayRuntimeBrain

        runner_api.build_brain = lambda provider, model: ReplayRuntimeBrain()
    try:
        with tempfile.TemporaryDirectory(prefix=f"jarvis-{name}-gateway-e2e-") as directory:
            tmp = Path(directory)
            base.runtimes_root = lambda: tmp / "agent_runtimes"
            ledger.set_ledger_path(tmp / "usage.db")
            gateway._CATALOG = ModelCatalog(cache_path=tmp / "catalog.json")
            result = 1
            try:
                result = await _check(name, provider, wanted, tmp, replay=replay)
                return result
            finally:
                await driver(name).stop()
                await asyncio.to_thread(ledger.flush)
                # Gateway calls are metered: the ledger writer holds usage.db in
                # this folder open, so it is closed (pointed back) before the
                # folder is removed — on Windows an open file blocks that.
                await asyncio.to_thread(ledger.set_ledger_path, previous_ledger)
                gateway._CATALOG = previous_catalog
                if result != 0:
                    log = tmp / "runtime-stderr.log"
                    if log.exists():
                        target = ROOT / "eval-results" / f"{name}-{time.time_ns()}.log"
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(log.read_bytes())
                        print("diagnostic:", target)
    finally:
        gateway._client = previous_client
        gateway.subscription_ready = previous_ready
        runner_api.build_brain = previous_build
        gateway._CATALOG = previous_catalog
        ledger.set_ledger_path(previous_ledger)
        base.runtimes_root = previous_root
        runtime_refs.set_api_base_url(previous_url or "")


async def _check(name: str, provider: str, wanted: str, tmp: Path, *, replay: bool) -> int:
    workspace = tmp / "workspace"
    workspace.mkdir()
    keep_user_path()  # before detect(): it would run the app's PATH cleanup
    status = driver(name).detect(refresh=True)
    print("detect:", status.ready, status.problem)
    if not status.ready:
        return 2
    path_before = user_path_snapshot()
    await prepare(name)
    port = _free_port()
    with _serve(port):
        result = await _turns(name, provider, wanted, workspace, port, replay=replay)
    if user_path_snapshot() != path_before:
        print("user PATH changed: FAIL")
        return 1
    print("user PATH unchanged: yes")
    return result


async def _turns(name, provider, wanted, workspace, port, *, replay):
    runtime_refs.set_api_base_url(f"http://127.0.0.1:{port}")
    model = await _pick_model(wanted) if provider == "openai-codex" else wanted
    if replay:
        from jarvis.core.config import BrainProviderConfig, JarvisConfig, OllamaModelOptions

        replay_config = JarvisConfig()
        replay_config.brain.providers["ollama"] = BrainProviderConfig(
            models={model: OllamaModelOptions(num_ctx=128000)}
        )

        # No catalog lookup to a real server on the Chat Completions replay.
        if provider != "openai-codex":
            from jarvis.agent_runtimes.model_map import route_for

            route = route_for(replay_config, provider, model, agent_id="sub-e2e-agent")
        else:
            route = await prepare_route(replay_config, provider, model, agent_id="sub-e2e-agent")
    else:
        route = await prepare_route(load_config(), provider, model, agent_id="sub-e2e-agent")
    print("route:", route.provider, route.model, route.transport, route.base_url)
    print("limits:", route.context_window, route.max_output_tokens)
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
    started = time.monotonic()
    first, _io1 = await _run(name, turn, "Remember the word PELICAN. Reply with exactly: READY")
    print(f"turn 1 took {time.monotonic() - started:.1f} s")
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
