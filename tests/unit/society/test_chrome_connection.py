"""The extension transport and task adapter are tested without a real browser."""

import asyncio
import json

import pytest

from jarvis.society.browser.chrome import ChromeConnections
from jarvis.society.browser.extension_session import ChromeSession


class FakeSocket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = asyncio.Queue()
        self.closed = False

    async def receive_json(self):
        value = await self.incoming.get()
        if isinstance(value, BaseException):
            raise value
        return value

    async def send_json(self, value):
        await self.sent.put(value)

    async def close(self, code=1000):
        if not self.closed:
            self.closed = True
            await self.incoming.put(ConnectionError("closed"))


async def test_disconnect_fails_pending_and_never_replays_on_reconnect():
    broker, first = ChromeConnections(), FakeSocket()
    reader = asyncio.create_task(broker.attach("profile", first))
    await asyncio.sleep(0)
    command = asyncio.create_task(broker.request("profile", "action", {}))
    await first.sent.get()
    await first.incoming.put(ConnectionError("connection lost"))
    await reader
    with pytest.raises(RuntimeError, match="not replayed"):
        await command
    assert not broker.connected("profile")
    second = FakeSocket()
    newer = asyncio.create_task(broker.attach("profile", second))
    await asyncio.sleep(0)
    assert second.sent.empty()
    await broker.close()
    await newer


async def test_replacement_does_not_let_old_cleanup_remove_new_transport():
    broker, first, second = ChromeConnections(), FakeSocket(), FakeSocket()
    reader = asyncio.create_task(broker.attach("profile", first))
    await asyncio.sleep(0)
    async def authorize():
        return True

    newer = asyncio.create_task(broker.attach("profile", second, authorize=authorize))
    assert (await second.sent.get())["kind"] == "connected"
    await reader
    assert broker.connected("profile")
    request = asyncio.create_task(broker.request("profile", "observe", {}))
    sent = await second.sent.get()
    await second.incoming.put(
        {
            "kind": "response",
            "id": sent["id"],
            "ok": True,
            "result": {"url": "https://example.com/"},
        }
    )
    assert (await request)["url"] == "https://example.com/"
    await broker.close()
    await newer


async def test_invalid_protocol_is_terminal_and_closes_socket():
    broker, socket = ChromeConnections(), FakeSocket()
    reader = asyncio.create_task(broker.attach("profile", socket))
    await socket.incoming.put({"kind": "evaluate", "expression": "untrusted"})
    await reader
    assert socket.closed
    assert not broker.connected("profile")


async def test_action_response_timeout_revokes_transport_without_replaying():
    broker, socket = ChromeConnections(), FakeSocket()
    reader = asyncio.create_task(broker.attach("profile", socket))
    await asyncio.sleep(0)
    command = asyncio.create_task(broker.request("profile", "action", {}, timeout=0.02))
    dispatched = await socket.sent.get()
    assert dispatched["op"] == "action"
    with pytest.raises(TimeoutError):
        await command
    await reader
    assert not broker.connected("profile")
    assert socket.closed and socket.sent.empty()


async def test_timeout_closes_socket_before_its_session_cancels_the_request(tmp_path):
    class YieldingCloseSocket(FakeSocket):
        async def close(self, code=1000):
            await asyncio.sleep(0)
            await super().close(code)

    broker, socket = ChromeConnections(), YieldingCloseSocket()
    reader = asyncio.create_task(broker.attach("profile", socket))
    await asyncio.sleep(0)
    session = ChromeSession("agent", "profile", broker, ["example.com"], tmp_path)
    session._run_task = asyncio.create_task(broker.request("profile", "action", {}, timeout=0.02))
    await socket.sent.get()
    with pytest.raises((TimeoutError, asyncio.CancelledError)):
        await session._run_task
    await asyncio.wait_for(reader, timeout=1)
    assert socket.closed and session.closed and not broker.connected("profile")
    session._run_task = None
    await session.close()


async def test_credential_rotation_rejects_an_older_waiting_handshake():
    broker, socket = ChromeConnections(), FakeSocket()
    entered, release = asyncio.Event(), asyncio.Event()
    authorized = True

    async def rotation():
        nonlocal authorized
        entered.set()
        await release.wait()
        authorized = False

    async def authenticate():
        return authorized

    rotating = asyncio.create_task(broker.change_credentials("profile", rotation))
    await entered.wait()
    older_handshake = asyncio.create_task(broker.attach("profile", socket, authorize=authenticate))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(rotating, older_handshake)
    assert socket.closed and not broker.connected("profile")
    assert socket.sent.empty()


class FakeConnection:
    def __init__(self, blocked=False):
        self.operations = []
        self.blocked = blocked
        self.callback = None

    def subscribe(self, _profile_id, callback):
        self.callback = callback
        return lambda: None

    async def request(self, _profile_id, op, args):
        self.operations.append((op, args))
        if op == "observe":
            return {
                "blocked": self.blocked,
                "url": "https://example.com/",
                "observation_id": "fresh",
                "identity": "@example",
            }
        return {"ok": True}


class TimeoutAfterDispatch(FakeConnection):
    async def request(self, profile_id, op, args):
        result = await super().request(profile_id, op, args)
        if op == "action":
            raise TimeoutError("The action response was lost")
        return result


async def test_uncertain_action_stops_planner_trace_even_if_executor_catches_timeout(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from jarvis.society.browser.live import LiveSessions

    connection = TimeoutAfterDispatch()
    session = await ChromeSession(
        "agent", "profile", connection, ["example.com"], tmp_path
    ).initialize()
    session.profile_binding = SimpleNamespace(kind="chrome")
    live = LiveSessions(tmp_path)
    live.sessions["agent"] = session

    async def ensure(_agent):
        return session

    async def llm(_payload):
        return {"ok": True, "text": '{"action":{"click":{"index":1}}}'}

    async def executor(payload):
        try:
            await payload["apply"]()
        except TimeoutError:
            return {"ok": False, "error": "timeout"}
        raise AssertionError("Expected the fixture timeout")

    monkeypatch.setattr(live, "ensure", ensure)
    result = await live.run(
        SimpleNamespace(agent_id="agent", model="fake"),
        task="Publish",
        max_steps=1,
        llm=llm,
        action=executor,
        trace_id="turn",
    )
    assert result["uncertain"] and "may have completed" in result["error"]
    assert ("agent", "turn") in live.stopped_turns
    assert len([op for op, _ in connection.operations if op == "action"]) == 1
    await live.close()


async def test_model_action_requires_gate_apply_and_carries_fresh_identity(tmp_path):
    connection = FakeConnection()
    session = await ChromeSession(
        "agent", "profile", connection, ["example.com"], tmp_path
    ).initialize()
    answers = iter([{"action": {"click": {"index": 1}}}, {"done": "Observed completion"}])
    approvals = []

    async def llm(_payload):
        return {"ok": True, "text": json.dumps(next(answers))}

    async def action(payload):
        approvals.append(payload)
        result = await payload["apply"]()
        with pytest.raises(RuntimeError, match="expired"):
            await payload["apply"]()
        return result

    session.rpc = {"llm": llm, "action": action}
    result = await session.command("run", {"task": "Click the approved button", "max_steps": 3})
    assert result["ok"]
    applied = [args for op, args in connection.operations if op == "action"]
    assert len(applied) == len(approvals) == 1
    assert applied[0]["expected_identity"] == "@example"
    assert applied[0]["observation_id"] == "fresh"
    await session.close()


@pytest.mark.parametrize("blocked", [True, False])
async def test_login_or_denied_action_cannot_reach_browser(tmp_path, blocked):
    connection = FakeConnection(blocked)
    session = await ChromeSession("agent", "profile", connection, [], tmp_path).initialize()
    llm_calls = []

    async def llm(payload):
        llm_calls.append(payload)
        return {"ok": True, "text": '{"action":{"click":{"index":1}}}'}

    async def denied(_payload):
        return {"ok": False}

    session.rpc = {"llm": llm, "action": denied}
    result = await session.command("run", {"task": "Continue"})
    assert not result["ok"]
    assert not any(op == "action" for op, _ in connection.operations)
    assert bool(llm_calls) is not blocked
    await session.close()


async def test_manual_takeover_cancels_inference_before_allowing_login(tmp_path):
    connection = FakeConnection()
    session = await ChromeSession("agent", "profile", connection, [], tmp_path).initialize()
    entered = asyncio.Event()

    async def llm(_payload):
        entered.set()
        await asyncio.Event().wait()

    session.rpc = {"llm": llm, "action": llm}
    run = asyncio.create_task(session.command("run", {"task": "Read page"}))
    await entered.wait()
    await session.command("takeover", {"enabled": True})
    with pytest.raises(asyncio.CancelledError):
        await run
    assert session.state["manual"] and session.state["preview_paused"]
    assert not any(op == "action" for op, _ in connection.operations)
    await session.close()


async def test_manual_pause_keeps_viewer_alive_without_reading_chrome(tmp_path):
    connection = FakeConnection()
    session = await ChromeSession("agent", "profile", connection, [], tmp_path).initialize()
    await session.command("takeover", {"enabled": True})
    viewer = asyncio.Queue()
    session.subscribers.add(viewer)
    await session.command("subscribe", {"enabled": True})
    for _ in range(2):
        event = await asyncio.wait_for(viewer.get(), 1.5)
        assert event["kind"] == "state"
        assert event["manual"] and event["preview_paused"]
        assert event["url"] == "" and event["tabs"] == []
    assert not any(op in {"snapshot", "observe"} for op, _ in connection.operations)
    await session.close()


@pytest.mark.parametrize(
    "action",
    [
        {"evaluate": {"code": "1"}},
        {"click": {"index": True}},
        {"navigate": {"url": "https://example.com", "cdp": {}}},
        {"scroll": {"dy": float("inf")}},
    ],
)
def test_model_cannot_expand_action_schema(action):
    with pytest.raises(ValueError):
        ChromeSession._validate_action(action)
