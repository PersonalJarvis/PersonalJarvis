"""Pixels, identity and receipts survive George -> workspace -> coding CLI."""

from __future__ import annotations

import asyncio
import base64
import io
import re
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from jarvis.agent_chat.attachments import handoff_context
from jarvis.agentic_ide import fleet_actions, prompt_history
from jarvis.agentic_ide.control import CodingSessionControl
from jarvis.agentic_ide.drop_analysis import DropAnalysis
from jarvis.brain.dispatcher import BrainDispatcher
from jarvis.brain.workspace_tool import WorkspaceOrchestrationTool
from jarvis.core import image_references
from jarvis.core.protocols import BrainDelta, ExecutionContext, ImageBlock, ToolResult
from jarvis.plugins.tool.appshot import AppshotTool
from tests.contract.test_workspace_orchestration import rig, runnable  # noqa: F401
from tests.fakes.fake_visual_sftp import FakeImagePool, FakeSftp


def png(color="red"):
    out = io.BytesIO()
    Image.new("RGB", (24, 16), color).save(out, format="PNG")
    return out.getvalue()


@pytest.fixture(autouse=True)
def image_store(monkeypatch):
    now = [0.0]
    store = image_references.ImageReferences(clock=lambda: now[0], schedule_expiry=False)
    monkeypatch.setattr(image_references, "_STORE", store)
    return store, now


@pytest.fixture
def delivery(rig, runnable, monkeypatch, tmp_path):  # noqa: F811
    orchestrator, registry, _ = rig
    registry._pty.tui_echo = True
    control = CodingSessionControl(registry)
    orchestrator.sessions = control
    monkeypatch.setattr(prompt_history, "_store_dir", lambda: tmp_path / "prompt-history")

    async def ready(owner, names, **kwargs):
        return set(names)

    async def start(owner, term):
        # No real provider or subprocess. The registry still performs the PTY
        # paste, Enter, verification and durable prompt receipt itself.
        async def output(terminal_id, data):
            term.transcript.feed(data)

        async def closed(*args):
            pass  # Fake process has no independent lifecycle.

        process = await registry._pty.spawn(
            ("codex",), "codex", owner.folder, 120, 40, output, closed
        )
        term.pty_id = process.terminal_id
        term.status = "live"

    monkeypatch.setattr(control, "_start", start)
    monkeypatch.setattr(fleet_actions, "wait_for_input_line", ready)
    monkeypatch.setattr(fleet_actions, "wait_for_prompt_ready", ready)
    ctx = ExecutionContext(uuid4(), "Fix the pictured bug", {"live_session_id": "call-A"}, None)
    return WorkspaceOrchestrationTool(orchestrator), registry, ctx


async def source_ref(source, delivery, monkeypatch, tmp_path):
    tool, registry, ctx = delivery
    data = png()
    if source == "upload":
        from jarvis.agent_chat import attachments, runner_brain
        from jarvis.agent_chat.service import AgentChatService
        from jarvis.agent_chat.store import AgentChatStore
        from jarvis.agentic_ide.drops import dereference

        async def describe(readable):
            # Analysis is optional; an unavailable vision model must not cost the pixels.
            return [DropAnalysis(name, reference, "image") for name, _, reference in readable]

        monkeypatch.setattr(attachments, "_analyze", describe)
        monkeypatch.setattr(runner_brain, "brain_manager", lambda: None)
        attached = await attachments.ingest(tmp_path, uploads=[("source.png", data)])
        service = AgentChatService(AgentChatStore(tmp_path / "chat.sqlite"))
        chat = service.store.create_session(
            provider="fakeprov", model="", effort="", cwd=str(tmp_path), surface="jarvis",
        )
        await service.send(chat.session_id, "Fix the pictured bug", [a.to_dict() for a in attached])
        await service.cancel(chat.session_id)
        note = next(
            event["payload"]["text"] for event in service.store.list_events(chat.session_id)
            if event["kind"] == "user_message"
        )
        ctx = ExecutionContext(
            ctx.trace_id, ctx.user_utterance,
            {"approval_ref": "agent-chat:" + chat.session_id}, None,
        )
        ref = re.search(r"img_[0-9a-f]{32}", note)[0]
        # Original upload may disappear; the selected bytes remain bound.
        (tmp_path / dereference(attached[0].reference)).unlink()
    else:
        from jarvis.appshot import service
        from jarvis.plugins.tool import appshot

        shot = SimpleNamespace(
            image=data,
            mime="image/png",
            label="active window",
            width=24,
            height=16,
            app_name="Test",
            note="Filtered capture",
        )

        async def take(**kwargs):
            assert kwargs["deliver"] is False
            return service.AppshotResult(status="captured", shot=shot)

        monkeypatch.setattr(service, "take_appshot", take)
        monkeypatch.setattr(appshot, "_app_bus", lambda: None)
        monkeypatch.setattr(
            appshot,
            "_load_config",
            lambda: SimpleNamespace(screen_context=SimpleNamespace(ttl_s=120)),
        )
        result = await AppshotTool().execute({}, ctx)
        ref = result.output["image_ref"]
        assert base64.b64decode(result.output["_image"]["data"]) == data
    return ref, data, ctx


async def assignment(delivery, ctx, action, refs, *, prompt="Fix the pictured bug"):
    tool, registry, _ = delivery
    args = {"action": action, "prompt": prompt, "image_refs": refs}
    if action == "create":
        args.update(cli="Codex", workspace="Personal Jarvis")
    else:
        resolved = await tool.execute({"action": "resolve", "workspace": "Personal Jarvis"}, ctx)
        args.update(resolved.output["target"], request_id=resolved.output["request_id"])
    return await tool.execute(args, ctx), args


@pytest.mark.parametrize("source", ["upload", "appshot"])
@pytest.mark.parametrize("action", ["create", "send"])
async def test_actual_pixels_reach_new_and_existing_cli(
    source, action, delivery, monkeypatch, tmp_path
):
    ref, data, ctx = await source_ref(source, delivery, monkeypatch, tmp_path)
    # A screenshot from a previous topic must not be included by recency.
    old = image_references.get_store().add(
        image_references.scope_for(ctx.config), png("blue"), "image/png", source="unrelated"
    )
    result, args = await assignment(delivery, ctx, action, [ref])
    assert result.success, result
    receipt = result.output["deliveries"][0] if action == "create" else result.output
    assert receipt["status"] == "accepted", receipt
    images = receipt["images"]
    assert [row["image_ref"] for row in images] == [ref]
    path = Path(images[0]["path"])
    assert path.read_bytes() == data  # noqa: ASYNC240 - tiny local test image
    with Image.open(path) as actual:
        assert actual.getpixel((0, 0)) == (255, 0, 0)
    registry = delivery[1]
    writes = "".join(text for _, text in registry._pty.writes)
    assert path.as_posix() in writes and "Open every listed file" in writes
    assert old not in writes and "\r" in writes
    found = registry.find_terminal(
        receipt["target"]["terminal_id"], receipt["target"]["workspace_id"]
    )
    assert found[1].prompt_records[-1].attachments[0]["kind"] == "image"
    if action == "send":
        before = len(registry._pty.writes)
        repeated = await delivery[0].execute(args, ctx)
        assert repeated.output == result.output
        assert len(registry._pty.writes) == before


async def test_multiple_images_and_explicit_selection_required(delivery):
    tool, registry, ctx = delivery
    store = image_references.get_store()
    refs = [
        store.add(image_references.scope_for(ctx.config), png(c), "image/png", source="upload")
        for c in ("red", "green")
    ]
    missing = await tool.execute({"action": "create", "prompt": "Fix this", "cli": "Codex"}, ctx)
    assert not missing.success and missing.output["status"] == "image_selection_required"
    assert not registry._pty.writes
    result, _ = await assignment(delivery, ctx, "send", refs)
    assert result.success, result
    assert [r["image_ref"] for r in result.output["images"]] == refs
    assert len({r["path"] for r in result.output["images"]}) == 2


@pytest.mark.parametrize("failure", ["expired", "other_conversation", "missing", "invalid_bytes"])
async def test_unavailable_image_refuses_whole_task(failure, delivery, image_store):
    store, now = image_store
    tool, registry, ctx = delivery
    scope = (
        "chat:other" if failure == "other_conversation" else image_references.scope_for(ctx.config)
    )
    ref = store.add(
        scope, b"broken" if failure == "invalid_bytes" else png(), "image/png", source="appshot"
    )
    if failure == "expired":
        now[0] = 121
    if failure == "missing":
        ref = "img_missing"
    result, _ = await assignment(delivery, ctx, "send", [ref])
    assert not result.success, result
    assert not registry._pty.writes
    assert result.output["status"] == "not_accepted"


async def test_expired_create_does_not_open_empty_pane(delivery, image_store):
    store, now = image_store
    _, registry, ctx = delivery
    ref = store.add(image_references.scope_for(ctx.config), png(), "image/png", source="appshot")
    before = sum(len(owner.terminals) for owner in registry.sessions)
    now[0] = 121
    result, _ = await assignment(delivery, ctx, "create", [ref])
    assert not result.success and "expired" in result.error
    assert sum(len(owner.terminals) for owner in registry.sessions) == before


def test_upload_cannot_read_outside_chat_folder(tmp_path, image_store):
    root = tmp_path / "chat"
    root.mkdir()
    (tmp_path / "private.png").write_bytes(png())
    note = handoff_context(
        str(root), [DropAnalysis("outside", '"../private.png"', "image")], session_id="isolated"
    )
    assert "unavailable" in note
    assert not image_store[0].available("chat:isolated")


async def test_image_paths_cannot_be_truncated(delivery):
    _, registry, ctx = delivery
    ref = image_references.get_store().add(
        image_references.scope_for(ctx.config), png(), "image/png", source="appshot"
    )
    result, _ = await assignment(delivery, ctx, "send", [ref], prompt="x" * 20000)
    assert not result.success and "truncate" in result.output["reason"]
    assert not registry._pty.writes


async def test_regular_loop_keeps_appshot_pixels_out_of_text_and_into_vision():
    class Brain:
        def __init__(self):
            self.requests = []

        async def complete(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                yield BrainDelta(tool_call={"id": "shot", "name": "take_appshot", "input": {}})
                yield BrainDelta(finish_reason="tool_use")
            else:
                yield BrainDelta(content="Captured", finish_reason="stop")

    data = base64.b64encode(png()).decode("ascii")

    class Executor:
        async def execute(self, *args, **kwargs):
            return ToolResult(
                True, {"image_ref": "img_test", "_image": {"mime": "image/png", "data": data}}
            )

    brain = Brain()
    await BrainDispatcher(
        brain, tools={"take_appshot": AppshotTool()}, executor=Executor()
    ).dispatch("Take an appshot")
    messages = brain.requests[-1].messages
    assert any(image.data_b64 == data for message in messages for image in message.images)
    assert all(data not in str(message.content) for message in messages)


async def test_user_image_input_has_scoped_ids_before_tool_routing(image_store):
    class Brain:
        async def complete(self, request):
            message = request.messages[-1]
            ref = re.search(r"img_[0-9a-f]{32}", message.content)[0]
            assert image_store[0].resolve("turn:upload", [ref])[0].data == png()
            yield BrainDelta(content="Seen", finish_reason="stop")

    await BrainDispatcher(Brain()).dispatch(
        "Fix this",
        trace_id="upload",
        images=(ImageBlock("image/png", base64.b64encode(png()).decode()),),
    )


@pytest.mark.parametrize("corrupt", [False, True])
async def test_remote_session_gets_verified_remote_bytes(delivery, monkeypatch, corrupt):
    from jarvis.computers import remote_terminal

    tool, registry, ctx = delivery
    owner = registry.sessions[0]
    term = owner.terminals[0]
    term.computer_id, term.remote_folder = "connected-host", "/work/project"
    sftp = FakeSftp()
    sftp.corrupt = corrupt
    monkeypatch.setattr(remote_terminal, "pool_for", lambda computer_id: FakeImagePool(sftp))
    # The remote PTY is the same recorded boundary; SFTP stays a separate channel.
    monkeypatch.setattr(registry, "_pool", lambda term: registry._pty)
    ref = image_references.get_store().add(
        image_references.scope_for(ctx.config), png(), "image/png", source="appshot"
    )
    result, _ = await assignment(delivery, ctx, "send", [ref])
    if corrupt:
        assert not result.success and not registry._pty.writes
        assert "verification failed" in result.output["reason"]
    else:
        assert result.success, result
        path = result.output["images"][0]["path"]
        assert path.startswith("/work/project/.jarvis/visual-references/")
        assert sftp.files["/sftp" + path] == png()
        assert sftp.files["/sftp/work/project/.jarvis/visual-references/.gitignore"] == b"*\n"
        assert path in "".join(text for _, text in registry._pty.writes)


async def test_changed_image_selection_is_a_new_assignment(delivery):
    tool, registry, ctx = delivery
    store = image_references.get_store()
    refs = [
        store.add(image_references.scope_for(ctx.config), png(c), "image/png", source="upload")
        for c in ("red", "blue")
    ]
    first, args = await assignment(delivery, ctx, "send", [refs[0]])
    assert first.success
    term = registry.sessions[0].terminals[0]
    # A finished CLI is ready for a second independent work order.
    term.transcript.feed("\x1b[2J\x1b[H❯ ")
    term.last_submit_at = None
    reused = await tool.execute({**args, "image_refs": [refs[1]]}, ctx)
    assert not reused.success  # A spent request ID cannot authorize different bytes.
    second, _ = await assignment(delivery, ctx, "send", [refs[1]])
    assert second.success, second
    assert second.output["images"][0]["image_ref"] == refs[1]
    assert len(term.prompt_records) == 2


async def test_destination_move_during_transfer_refuses_delivery(delivery, monkeypatch):
    from jarvis.agentic_ide import visual_handoff

    tool, registry, ctx = delivery
    original = visual_handoff.prepare

    async def moving(owner, term, images):
        result = await original(owner, term, images)
        term.folder = owner.folder + "/moved"
        return result

    monkeypatch.setattr(visual_handoff, "prepare", moving)
    ref = image_references.get_store().add(
        image_references.scope_for(ctx.config), png(), "image/png", source="appshot"
    )
    result, _ = await assignment(delivery, ctx, "send", [ref])
    assert not result.success and "destination changed" in result.output["reason"]
    assert not registry._pty.writes


async def test_local_copy_lives_in_panes_own_worktree(delivery, tmp_path):
    _, registry, ctx = delivery
    own = tmp_path / "isolated-worktree"
    own.mkdir()
    registry.sessions[0].terminals[0].folder = str(own)
    ref = image_references.get_store().add(
        image_references.scope_for(ctx.config), png(), "image/png", source="upload"
    )
    result, _ = await assignment(delivery, ctx, "send", [ref])
    assert result.success
    assert Path(result.output["images"][0]["path"]).is_relative_to(own)


async def test_sftp_transfer_against_real_loopback_server(tmp_path, monkeypatch):
    """Exercise binary mode, exclusive creation, permissions and readback on real SFTP."""
    ssh = pytest.importorskip("asyncssh")
    from jarvis.agentic_ide.visual_handoff import prepare
    from jarvis.computers import remote_terminal

    class Server(ssh.SSHServer):
        def begin_auth(self, username):
            return False  # Test-only loopback server has no user accounts or credentials.

    project = tmp_path / "project"
    project.mkdir()
    key = ssh.generate_private_key("ssh-ed25519")
    server = await ssh.listen(
        "127.0.0.1",
        0,
        server_factory=Server,
        server_host_keys=[key],
        sftp_factory=lambda channel: ssh.SFTPServer(channel, chroot=str(tmp_path)),
    )
    try:
        async with ssh.connect("127.0.0.1", server.get_port(), known_hosts=None) as conn:

            class Pool:
                async def sftp_path(self, path):
                    return path

                async def connection(self):
                    return SimpleNamespace(conn=conn)

            monkeypatch.setattr(remote_terminal, "pool_for", lambda computer_id: Pool())
            ref = image_references.get_store().add("test", png(), "image/png", source="upload")
            images = image_references.get_store().resolve("test", [ref])
            term = SimpleNamespace(
                computer_id="test-loopback", remote_folder="/project", placing=False, agent="codex"
            )
            _, _, receipts = await prepare(None, term, images)
            local_path = tmp_path / receipts[0]["path"].lstrip("/")
            assert await asyncio.to_thread(local_path.read_bytes) == png()
    finally:
        server.close()
        await server.wait_closed()


async def test_idle_reference_bytes_expire_without_another_read():
    store = image_references.ImageReferences()
    store.add("short", png(), "image/png", source="appshot", ttl_s=0.02)
    for _ in range(100):
        if not store._entries:
            break
        await asyncio.sleep(0.01)
    assert not store._entries
