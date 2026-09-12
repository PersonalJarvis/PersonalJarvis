"""The Live contract must behave identically on every OS, without a microphone."""

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.core.protocols import SupervisorToolDescriptor, ToolResult
from jarvis.live.config import LiveConfig
from jarvis.live.session import LiveVoiceSession
from jarvis.live.state import LiveLedger, TranscriptFragment
from jarvis.live.tools import LiveTools


class Gateway:
    def __init__(self):
        self.calls = []

    def catalog(self):
        return (
            SupervisorToolDescriptor(
                "write-file",
                "Write a file",
                {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                "monitor",
            ),
        )

    async def execute(self, name, args, request):
        self.calls.append((name, args, request))
        return ToolResult(True, {"verified": True})

    async def cancel_pending(self, trace):
        return True


@pytest.fixture
def ledger(tmp_path):
    value = LiveLedger(tmp_path / "live.db")
    yield value
    value.close()


def test_explicit_selection_and_single_backend():
    with pytest.raises(ValueError, match="Choose"):
        LiveConfig().session_config(language="en", tools=[])
    config = LiveConfig(configured=True, backend_model="chosen-model")
    wire = config.session_config(language="en", tools=[])
    assert wire["delegation"]["responses"]["model"] == "chosen-model"
    assert wire["store"] is False
    assert "turn_detection" not in wire


def test_receipts_survive_reopening(tmp_path):
    path = tmp_path / "live.db"
    first = LiveLedger(path)
    assert first.claim("s", "c", "write", {}, 1) is None
    first.close()
    second = LiveLedger(path)
    assert second.claim("s", "c", "write", {}, 1)["status"] == "uncertain"
    second.finish("s", "c", {"success": True})
    assert second.claim("s", "c", "write", {}, 1) == {"success": True}
    assert second.claim("s", "c", "write", {"different": True}, 1)["success"] is False
    second.close()


def test_transcript_preserves_spaces_and_repetition(ledger):
    for index, delta in enumerate(["Hello", " hello", " again"]):
        f = TranscriptFragment("s", str(index), "user", delta, index, index + 1)
        assert ledger.append(f)
        assert not ledger.append(f)
    rows = ledger._db.execute("SELECT delta FROM live_transcripts ORDER BY start_ms").fetchall()
    assert "".join(r[0] for r in rows) == "Hello hello again"


def test_usage_is_cumulative_not_additive(ledger):
    ledger.usage("s", 12)
    ledger.usage("s", 15)
    ledger.usage("s", 14)
    ledger.usage("s", 16, finalized=True)
    assert ledger._db.execute("SELECT seconds, finalized FROM live_usage").fetchone() == (16, 1)


@pytest.mark.asyncio
async def test_duplicate_calls_execute_once_and_revisions_block(ledger):
    gateway = Gateway()
    runtime = LiveTools(gateway, ledger, "s", language="en", backend_model="chosen")
    runtime.revision = 1
    args = {"name": "write-file", "arguments_json": '{"text":"hello"}'}
    first, second = await asyncio.gather(
        runtime.execute("c", "call_tool", args, 1),
        runtime.execute("c", "call_tool", args, 1),
    )
    assert first == second
    assert len(gateway.calls) == 1
    assert gateway.calls[0][2].config_snapshot["live_backend_model"] == "chosen"
    assert (await runtime.execute("c2", "call_tool", args, 0))["status"] == "superseded"
    assert len(gateway.calls) == 1


@pytest.mark.asyncio
async def test_empty_completion_snapshot_keeps_collected_calls(ledger):
    sent = []

    async def send(event):
        sent.append(event)

    cfg = SimpleNamespace(brain=SimpleNamespace(reply_language="en"))
    session = LiveVoiceSession(
        session_id="s",
        send_binary=send,
        send_json=send,
        providers=[SimpleNamespace(name="test")],
        config=cfg,
    )
    session._ledger = ledger
    gateway = Gateway()
    session._tools = LiveTools(gateway, ledger, "s", language="en", backend_model="chosen")
    session._connection = SimpleNamespace(send=send)

    async def event(payload):
        await session._event({"type": "response.event", "delegation_id": "d", "event": payload})

    await event({"type": "response.created", "response": {"id": "r"}})
    for index in range(2):
        await event(
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "function_call",
                    "call_id": str(index),
                    "name": "call_tool",
                    "arguments": '{"name":"write-file","arguments_json":"{\\"text\\":\\"ok\\"}"}',
                },
            }
        )
    await event({"type": "response.completed", "response": {"id": "r", "output": []}})
    await asyncio.gather(*session._jobs)
    outputs = [e for e in sent if e["type"] == "response.item.create"]
    assert len(outputs) == 2
    assert sent[-1] == {"type": "response.create"}
    assert len(gateway.calls) == 2


def test_profile_migration_backup(tmp_path, monkeypatch):
    from jarvis.core import config_writer

    path = tmp_path / "jarvis.toml"
    original = '[brain.realtime]\nprovider = "openai-realtime"\n[voice]\nmode = "realtime"\n'
    path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(config_writer, "_update_config_soll_section", lambda *a: None)  # i18n-allow
    profile = LiveConfig(configured=True, backend_model="chosen-model")
    config_writer.set_live_profile(profile.model_dump(), path=path)
    assert path.with_name("jarvis.toml.pre-live.bak").read_text() == original
    import tomllib

    saved = tomllib.loads(path.read_text())
    assert saved["brain"]["realtime"]["provider"] == "openai-live"
    assert saved["live"]["backend_model"] == "chosen-model"


def test_config_matches_frontend_interface():
    import re
    from pathlib import Path

    source = Path("jarvis/ui/web/frontend/src/components/providers/LiveProfile.tsx").read_text()
    body = source.split("export interface LiveProfileValue {", 1)[1].split("}", 1)[0]
    assert set(re.findall(r"(\w+):", body)) == set(LiveConfig.model_fields)


@pytest.mark.asyncio
async def test_operation_model_does_not_change_other_tasks():
    from jarvis.core.model_selection import ModelSelection, operation_model, use_operation_model

    async def work(name):
        with use_operation_model(ModelSelection("openai", name)):
            await asyncio.sleep(0)
            return operation_model.get().model

    assert await asyncio.gather(work("first"), work("second")) == ["first", "second"]
    assert operation_model.get() is None


@pytest.mark.asyncio
async def test_approval_requires_new_unambiguous_confirmation(ledger):
    from jarvis.safety.tool_executor import VOICE_CONFIRM_SENTINEL

    class ApprovalGateway(Gateway):
        async def execute(self, name, args, request):
            self.calls.append((name, args, request))
            return ToolResult(False, {}, VOICE_CONFIRM_SENTINEL)

        async def execute_confirmed(self, trace, request):
            self.calls.append(("confirmed", {}, request))
            return ToolResult(True, {"verified": True})

    gateway = ApprovalGateway()
    runtime = LiveTools(gateway, ledger, "s", language="en", backend_model="chosen")
    runtime.user_text = "yes"
    request = {"name": "write-file", "arguments_json": '{"text":"hello"}'}
    result = await runtime.execute("c", "call_tool", request, 0)
    approval = {"approval_id": result["approval_id"]}
    assert not (await runtime.execute("early", "confirm_action", approval, 0))["success"]
    runtime.revision = 1
    runtime.user_text = "yes change the target"
    assert not (await runtime.execute("changed", "confirm_action", approval, 1))["success"]
    runtime.user_text = "yes please"
    assert (await runtime.execute("confirmed", "confirm_action", approval, 1))["success"]
    assert len(gateway.calls) == 2


@pytest.mark.asyncio
async def test_gemini_native_core_does_not_dispatch_a_second_model(ledger):
    runtime = LiveTools(Gateway(), ledger, "s", language="en", backend_model="")
    result = await runtime.execute(
        "c",
        "call_tool",
        {
            "name": "write-file",
            "arguments_json": '{"text":"direct"}',
        },
        0,
    )
    assert result["success"]
    assert runtime.gateway.calls[0][2].config_snapshot["live_backend_model"] == ""


@pytest.mark.asyncio
async def test_vertex_namespaced_call_uses_the_same_gateway(ledger):
    runtime = LiveTools(Gateway(), ledger, "s", language="en", backend_model="")
    declarations = runtime.declarations()
    name = next(d["name"] for d in declarations if d["name"].startswith("jarvis_"))
    result = await runtime.execute("c", "default:" + name, {"text": "hello"}, 0)
    assert result["success"]
    assert runtime.gateway.calls[0][0] == "write-file"


def test_browser_voice_request_actions_have_frontend_consumers():
    from pathlib import Path
    from typing import get_args, get_type_hints

    from jarvis.core.events import BrowserVoiceRequested

    source = Path(
        "jarvis/ui/web/frontend/src/components/voice/BrowserRealtimeControl.tsx"
    ).read_text()
    for action in get_args(get_type_hints(BrowserVoiceRequested)["action"]):
        assert f'action === "{action}"' in source


@pytest.mark.asyncio
async def test_closing_voice_does_not_cancel_started_work(ledger):
    started, release = asyncio.Event(), asyncio.Event()

    class SlowGateway(Gateway):
        async def execute(self, name, args, request):
            started.set()
            await release.wait()
            assert not request.cancel_token.is_cancelled()
            return ToolResult(True, "completed")

    runtime = LiveTools(SlowGateway(), ledger, "s", language="en", backend_model="chosen")
    args = {"name": "write-file", "arguments_json": '{"text":"work"}'}
    job = asyncio.create_task(runtime.execute("started", "call_tool", args, 0))
    await started.wait()
    await runtime.close()
    release.set()
    assert (await job)["success"]
    assert not (await runtime.execute("queued", "call_tool", args, 0))["success"]


def test_native_declarations_are_accepted_by_google_sdk(ledger):
    types = pytest.importorskip("google.genai.types")
    from jarvis.plugins.realtime.gemini_live import _sanitize_declarations

    tools = LiveTools(Gateway(), ledger, "s", language="en", backend_model="")
    declarations = tuple({k: v for k, v in d.items() if k != "type"} for d in tools.declarations())
    assert len(_sanitize_declarations(declarations, types=types)) == len(declarations)


@pytest.mark.asyncio
async def test_local_manual_response_does_not_block_receiving_audio(ledger):
    from jarvis.core.protocols import AudioChunk
    from jarvis.live.native import NativeLiveVoiceSession
    from jarvis.realtime.protocol import RealtimeEvent

    received, release = asyncio.Event(), asyncio.Event()

    async def sink(data):
        if isinstance(data, bytes):
            received.set()

    class Connection:
        creates_responses_automatically = False

        async def request_response(self):
            await release.wait()

        async def receive(self):
            yield RealtimeEvent(type="input_transcript", text="hello", is_final=True)
            yield RealtimeEvent(
                type="audio_delta", audio=AudioChunk(pcm=b"\0\0", sample_rate=24000, timestamp_ns=0)
            )
            await release.wait()

        async def close(self):
            release.set()

    cfg = SimpleNamespace(brain=SimpleNamespace(reply_language="en"))
    session = NativeLiveVoiceSession(
        session_id="s",
        send_json=sink,
        send_binary=sink,
        config=cfg,
        providers=[SimpleNamespace(name="local")],
    )
    session._connection = Connection()
    session._ledger = ledger
    session._tools = LiveTools(Gateway(), ledger, "s", language="en", backend_model="")
    session._pump_task = asyncio.create_task(session._pump())
    try:
        await asyncio.wait_for(received.wait(), 2)
    finally:
        await session.end()
