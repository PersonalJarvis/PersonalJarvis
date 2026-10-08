"""Cloud ownership stays single-writer across transfer and lost acknowledgments."""

from __future__ import annotations

import json
import sqlite3
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from jarvis.agent_chat.store import AgentChatStore
from jarvis.society.cloud_bundle import CloudBundleError, build_bundle, import_bundle
from jarvis.society.cloud_host import CloudHost, CloudHostError, placement_for
from jarvis.society.events import MsgType, SocietyEnvelope
from jarvis.society.roster import Roster
from jarvis.society.store import SocietyStore
from jarvis.tasks.schema import AgentAction, TaskSpec, TriggerEvery
from jarvis.tasks.store import TaskStore


@pytest.fixture
async def world(tmp_path):
    store = SocietyStore(tmp_path / "society.db")
    await store.open()
    roster = Roster(store)
    agent, _ = await roster.create(
        name="Scout",
        provider="openai-codex",
        model="test-model",
        approval_mode="ask",
        grant_mode="allowlist",
        grants=["core:shell"],
    )
    chats = AgentChatStore(tmp_path / "agent_chat.db")
    session = chats.create_session(
        session_id=agent.session_id,
        provider=agent.provider,
        model="test-model",
        effort="",
        cwd=str(tmp_path),
        surface="society",
        permission_mode="read-only",
    )
    chats.update_session(session.session_id, vendor_session="local-vendor")
    chats.set_permission_override(session.session_id, "read-only")
    chats.append_event(
        session.session_id,
        {"kind": "user_message", "payload": {"text": "Keep my context", "turn_id": "t1"}},
    )
    tasks = TaskStore(tmp_path / "jarvis.db")
    await tasks.init()
    task = TaskSpec(
        title="Watch",
        trigger=TriggerEvery(interval_seconds=3600),
        action=AgentAction(prompt="Check updates", provider=agent.provider),
        tags=("society", "agent:scout"),
    )
    await tasks.insert(task)
    vault = tmp_path / "wiki"
    book = vault / "society" / "scout" / "MEMORY.md"
    book.parent.mkdir(parents=True)
    book.write_text("Remember the project", encoding="utf-8")
    workspace = tmp_path / "society" / "scout" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "work.txt").write_text("unfinished", encoding="utf-8")
    (workspace / ".env").write_text("PRIVATE=never-transferred", encoding="utf-8")
    config = SimpleNamespace(memory=SimpleNamespace(data_dir=tmp_path))
    chat = SimpleNamespace(store=chats, is_running=lambda _: False)
    runtime = SimpleNamespace(
        _data_dir=tmp_path,
        roster=roster,
        store=store,
        chat_service=lambda: chat,
        config=lambda: config,
        task_services=lambda: (tasks, None),
        memory=SimpleNamespace(root=lambda: vault),
    )
    yield runtime, task
    chats.close()
    await tasks.close()
    await store.close()


async def test_bundle_preserves_context_routines_and_restrictions(world, tmp_path):
    runtime, task = world
    bundle = await build_bundle(runtime, "scout")
    assert "PRIVATE" not in json.dumps(bundle)
    record = bundle["tables"]["society.db"]["society_agents"][0]
    assert record["approval_mode"] == "ask" and record["grant_mode"] == "allowlist"
    dest = tmp_path / "host"
    await import_bundle(dest, bundle)
    store = AgentChatStore(dest / "data" / "agent_chat.db")
    try:
        session = store.get_session("society:scout")
        assert session.permission_mode == "read-only"
        assert store.permission_override(session.session_id) == "read-only"
        assert session.vendor_session is None
        assert "Keep my context" in json.dumps(store.list_events(session.session_id))
    finally:
        store.close()
    with sqlite3.connect(dest / "data" / "jarvis.db") as db:
        row = db.execute("SELECT state,spec_json FROM tasks WHERE id=?", (str(task.id),)).fetchone()
    assert row[0] == "paused"
    assert json.loads(row[1])["trigger"]["interval_seconds"] == 3600
    assert (dest / "data/wiki/society/scout/MEMORY.md").read_text() == "Remember the project"
    assert (dest / "data/society/scout/workspace/work.txt").read_text() == "unfinished"


async def test_busy_agent_is_not_snapshotted(world):
    runtime, _ = world
    runtime.chat_service().is_running = lambda _: True
    with pytest.raises(CloudBundleError, match="current turn"):
        await build_bundle(runtime, "scout")


async def test_import_rejects_path_escape_before_writing(world, tmp_path):
    runtime, _ = world
    bundle = await build_bundle(runtime, "scout")
    bundle["files"]["../stolen"] = "YQ=="
    with pytest.raises(CloudBundleError, match="leaves"):
        await import_bundle(tmp_path / "destination", bundle)
    assert not (tmp_path / "stolen").exists()


class FakeHost(CloudHost):
    def __init__(self, runtime, *, fail_before=False, lose_activation=False):
        super().__init__(runtime)
        self.fail_before = fail_before
        self.lose_activation = lose_activation

    async def _prepare(self, row, bundle):
        assert self.placement("scout")["state"] == "preparing"
        if self.fail_before:
            raise CloudHostError("No server login")
        row.update(remote_root="/host/agent", port=48221)

    async def request(self, agent_id, method, path, body=None):
        row = self.placement(agent_id)
        if path == "/cloud/activate" and self.lose_activation:
            raise TimeoutError("acknowledgment lost")
        return 200, {
            "ready": True,
            "active": path == "/cloud/activate",
            "transfer_id": row["transfer_id"],
        }

    async def _discard_prepared(self, row):
        return None


async def test_pre_activation_failure_restores_local_owner(world):
    runtime, _ = world
    with pytest.raises(CloudHostError, match="login"):
        await FakeHost(runtime, fail_before=True).handoff("scout", "server")
    assert placement_for(runtime._data_dir, "scout") is None


async def test_lost_activation_ack_never_restores_local_owner(world):
    runtime, _ = world
    with pytest.raises(CloudHostError):
        await FakeHost(runtime, lose_activation=True).handoff("scout", "server")
    assert placement_for(runtime._data_dir, "scout")["state"] == "uncertain"


async def test_handoff_persists_active_locator_and_refuses_second_owner(world):
    runtime, _ = world
    host = FakeHost(runtime)
    row = await host.handoff("scout", "server")
    assert row["state"] == "active" and row["computer_id"] == "server"
    with pytest.raises(CloudHostError, match="already"):
        await host.handoff("scout", "different-server")


def test_corrupt_locator_fails_closed(tmp_path):
    folder = tmp_path / "cloud-agents"
    folder.mkdir()
    (folder / "scout.json").write_text("{broken")
    assert placement_for(tmp_path, "scout")["state"] == "uncertain"


@pytest.mark.parametrize(
    "name",
    ["id_ed25519", "id_rsa", ".npmrc", ".netrc", ".pypirc", "service-account-credentials.json"],
)
async def test_credential_files_are_not_transferred(world, name):
    runtime, _ = world
    folder = runtime._data_dir / "society/scout/workspace"
    (folder / name).write_text("never-transfer-this-secret", encoding="utf-8")
    bundle = await build_bundle(runtime, "scout")
    assert not any(key.endswith("/" + name) for key in bundle["files"])


async def test_historical_messages_keep_receipts_without_requeue(world, tmp_path):
    runtime, _ = world
    event = await runtime.store.append_and_publish(
        SocietyEnvelope(
            msg_type=MsgType.SAY,
            from_agent="scout",
            to_agent="jarvis",
            trace_id="trace",
            payload={"text": "Old completed result"},
        )
    )
    await runtime.store.conn.execute(
        "UPDATE society_deliveries SET status='delivered' WHERE event_id=?",
        (event.event_id,),
    )
    bundle = await build_bundle(runtime, "scout")
    root = tmp_path / "remote"
    await import_bundle(root, bundle)
    with sqlite3.connect(root / "data/society.db") as conn:
        assert conn.execute("SELECT status FROM society_deliveries").fetchall() == [("delivered",)]
        assert conn.execute(
            "SELECT value FROM society_meta WHERE key='kill_switch'"
        ).fetchone() == ("1",)


async def test_queued_board_messages_block_handoff(world):
    runtime, _ = world
    await runtime.store.append_and_publish(
        SocietyEnvelope(
            msg_type=MsgType.SAY,
            from_agent="jarvis",
            to_agent="scout",
            trace_id="trace",
            payload={"text": "Still pending"},
        )
    )
    with pytest.raises(CloudBundleError, match="pending messages"):
        await build_bundle(runtime, "scout")


async def test_preparing_seat_and_active_mission_block_snapshot(world):
    runtime, _ = world
    runtime.chat_service().running_session_ids = lambda: ["society:scout:routine:t:r"]
    with pytest.raises(CloudBundleError, match="current turn"):
        await build_bundle(runtime, "scout")
    runtime.chat_service().running_session_ids = lambda: []
    runtime.scheduler = SimpleNamespace(active_runs=lambda _: 1)
    with pytest.raises(CloudBundleError, match="active missions"):
        await build_bundle(runtime, "scout")


async def test_tunnel_is_reused_and_shutdown_only_detaches(world, monkeypatch):
    from jarvis.society.cloud_host import _save

    runtime, _ = world
    calls = []

    class Forward:
        def get_port(self):
            return 48123

        def close(self):
            calls.append("forward_closed")

        async def wait_closed(self):
            return None

    class Connection:
        def is_closed(self):
            return False

        async def forward_local_port(self, *args):
            calls.append("forward")
            return Forward()

    @asynccontextmanager
    async def session(_):
        calls.append("connected")
        try:
            yield SimpleNamespace(conn=Connection())
        finally:
            calls.append("disconnected")

    monkeypatch.setattr(
        "jarvis.computers.service.get_service", lambda: SimpleNamespace(session=session)
    )
    monkeypatch.setattr("jarvis.core.config.get_secret", lambda _: "private-test-token")
    _save(
        runtime._data_dir,
        {
            "agent_id": "scout",
            "computer_id": "server",
            "port": 48000,
            "transfer_id": "transfer",
            "state": "active",
        },
    )
    for _ in range(3):
        async with CloudHost(runtime).tunnel("scout") as (url, token):
            assert url == "http://127.0.0.1:48123" and token == "private-test-token"  # noqa: S105 -- fake
    assert calls == ["connected", "forward"]
    await CloudHost(runtime).aclose()
    assert calls == ["connected", "forward", "forward_closed", "disconnected"]


async def test_rollback_keeps_exact_due_time(world):
    runtime, task = world
    store, _ = runtime.task_services()
    original = await store.get(str(task.id))
    hydrated = []

    class Scheduler:
        async def pause(self, tid):
            await store.update_state(tid, "paused")

        async def hydrate(self):
            assert placement_for(runtime._data_dir, "scout") is None
            hydrated.append(True)

    runtime.task_services = lambda: (store, Scheduler())
    with pytest.raises(CloudHostError):
        await FakeHost(runtime, fail_before=True).handoff("scout", "server")
    after = await store.get(str(task.id))
    assert (after["state"], after["due_at_ns"]) == (original["state"], original["due_at_ns"])
    assert hydrated and placement_for(runtime._data_dir, "scout") is None


@pytest.mark.parametrize("abort_first", [False, True])
async def test_worker_boots_dormant_then_keeps_active_after_client_disconnect(
    world, tmp_path, monkeypatch, abort_first
):
    from jarvis.society import cloud_worker

    source, _ = world
    bundle = await build_bundle(source, "scout")
    root = tmp_path / "worker"
    root.mkdir()
    (root / "bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps({"agent_id": "scout", "port": 48101, "transfer_id": "transfer"}),
        encoding="utf-8",
    )
    (root / "access.token").write_text("test-token", encoding="utf-8")
    await import_bundle(root, bundle)
    observed = []

    class RosterStub:
        async def update(self, agent_id, fields):
            observed.append((agent_id, fields["state"]))

    class StoreStub:
        async def set_kill_switch(self, enabled):
            observed.append(("kill", enabled))

    class RuntimeStub:
        roster = RosterStub()
        store = StoreStub()
        memory = SimpleNamespace()

        async def ensure_started(self):
            observed.append("society_started")

    class SchedulerStub:
        async def hydrate(self):
            observed.append("routines_hydrated")

    runtime = RuntimeStub()

    class WebStub:
        def __init__(self, _):
            self.bus = object()
            self.app = SimpleNamespace(
                state=SimpleNamespace(society=runtime, task_scheduler=SchedulerStub())
            )

        async def start(self, **kwargs):
            observed.append("server_started")

        async def stop(self):
            observed.append("server_stopped")

    async def hit(app, path, method="GET"):
        messages = []

        async def send(value):
            messages.append(value)

        async def receive():
            return {"type": "http.disconnect"}

        await app(
            {
                "type": "http",
                "path": path,
                "method": method,
                "headers": [(b"authorization", b"Bearer test-token")],
            },
            receive,
            send,
        )
        return json.loads(messages[-1]["body"])

    class UvicornStub:
        def __init__(self, config):
            self.app = config.app

        async def serve(self):
            status = await hit(self.app, "/cloud/status")
            assert status["ready"] and not status["active"]
            assert "society_started" in observed and "routines_hydrated" not in observed
            if abort_first:
                assert (await hit(self.app, "/cloud/abort", "POST"))["aborted"]
                assert self.should_exit
                assert "detail" in await hit(self.app, "/cloud/activate", "POST")
                assert "routines_hydrated" not in observed
                return
            await hit(self.app, "/cloud/activate", "POST")
            assert "routines_hydrated" in observed and ("kill", False) in observed
            assert (await hit(self.app, "/cloud/status"))["active"]
            assert "server_stopped" not in observed

    monkeypatch.setattr("jarvis.ui.web.server.WebServer", WebStub)
    monkeypatch.setattr("jarvis.core.config.load_config", lambda: object())
    monkeypatch.setattr("jarvis.brain.factory.build_default_brain", lambda **_: object())
    monkeypatch.setattr("jarvis.core.control_key.ensure_control_key", lambda: "test-key")
    monkeypatch.setattr("jarvis.ui.web.agent_chat_routes._service_from_state", lambda _: object())
    monkeypatch.setattr("uvicorn.Server", UvicornStub)
    await cloud_worker.serve(root)
    if abort_first:
        with pytest.raises(RuntimeError, match="aborted"):
            await cloud_worker.serve(root)


@pytest.mark.parametrize("state", ["preparing", "ready"])
async def test_preparation_recovery_uses_ssh_without_http(world, monkeypatch, state):
    from jarvis.computers.remote_os import RemoteHost
    from jarvis.society.cloud_host import _save

    runtime, task = world
    store, _ = runtime.task_services()
    original = await store.get(str(task.id))
    await store.update_state(str(task.id), "paused")
    _save(
        runtime._data_dir,
        {
            "agent_id": "scout",
            "computer_id": "server",
            "port": 48111,
            "transfer_id": "receipt",
            "state": state,
            "remote_root": "/home/test/jarvis-agents/hosted/receipt",
            "routines": [
                {"id": str(task.id), "state": original["state"], "due_at_ns": original["due_at_ns"]}
            ],
        },
    )
    scripts = []

    @asynccontextmanager
    async def session(_):
        yield SimpleNamespace(conn=object())

    async def host(*_):
        return RemoteHost(home="/home/test")

    async def run(_opened, _host, script, **_):
        scripts.append(script)
        return SimpleNamespace(exit_status=0)

    class RecoveryHost(CloudHost):
        async def request(self, *args, **kwargs):
            pytest.fail("Pre-activation recovery cannot depend on an HTTP endpoint")

    monkeypatch.setattr(
        "jarvis.computers.service.get_service", lambda: SimpleNamespace(session=session)
    )
    monkeypatch.setattr("jarvis.computers.remote_os.remote_host", host)
    monkeypatch.setattr("jarvis.computers.remote_os.run_script", run)
    assert (await RecoveryHost(runtime).cancel_preparation("scout"))["cancelled"]
    assert "test ! -e" in scripts[0] and "/active.json" in scripts[0]
    assert "/aborted.json" in scripts[0] and "tmux kill-session" in scripts[0]
    restored = await store.get(str(task.id))
    assert (restored["state"], restored["due_at_ns"]) == (original["state"], original["due_at_ns"])
    assert placement_for(runtime._data_dir, "scout") is None


async def test_uncertain_ownership_cannot_be_cancelled_without_remote_proof(world):
    from jarvis.society.cloud_host import _save

    runtime, _ = world
    _save(
        runtime._data_dir,
        {
            "agent_id": "scout",
            "computer_id": "server",
            "port": 48111,
            "transfer_id": "receipt",
            "state": "uncertain",
        },
    )

    class OfflineHost(CloudHost):
        async def request(self, *args, **kwargs):
            raise CloudHostError("Offline")

    with pytest.raises(CloudHostError, match="Offline"):
        await OfflineHost(runtime).cancel_preparation("scout")
    assert placement_for(runtime._data_dir, "scout")["state"] == "uncertain"


async def test_worker_pins_the_remote_account_it_checked(world, tmp_path, monkeypatch):
    from jarvis.society import cloud_worker

    runtime, _ = world
    bundle = await build_bundle(runtime, "scout")
    root = tmp_path / "prepared"
    root.mkdir()
    (root / "bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
    monkeypatch.setattr(
        "jarvis.agent_chat.catalog.provider_row",
        lambda _: SimpleNamespace(agent="fake-cli", runner="api", label="Example"),
    )
    monkeypatch.setattr("jarvis.agent_chat.service.resolve_runner", lambda *_, **__: "fake-cli")
    monkeypatch.setattr("jarvis.agentic_ide.session.agent_argv", lambda _: ("fake",))
    monkeypatch.setattr(
        "jarvis.agent_accounts.active_account", lambda _: SimpleNamespace(id="remote-seat")
    )
    monkeypatch.setattr(
        "jarvis.agent_accounts.describe",
        lambda _: SimpleNamespace(connected=True, mode="subscription"),
    )
    await cloud_worker.prepare(root)
    with sqlite3.connect(root / "data/society.db") as conn:
        assert conn.execute("SELECT account_id,state FROM society_agents").fetchone() == (
            "remote-seat",
            "paused",
        )
