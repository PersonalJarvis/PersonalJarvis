"""Jarvis Bar drops become real attachments for the selected coding pane."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.agentic_ide import fanout
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.prompt_attachments import stage_items_for_prompt_target
from jarvis.agentic_ide.session import Registry
from jarvis.brain.drop_context import DroppedItem
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.mark.asyncio
async def test_bar_drop_is_copied_analysed_and_queued_for_prompt_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    registry = Registry(pty_manager=FakePtyManager())
    session = await registry.start(str(tmp_path), [{"agent": "claude"}, {"agent": "codex"}])
    registry.set_surface_context(
        workspace_id=session.id,
        view="grid",
        on_screen=True,
        terminal=None,
        prompt_target="T2",
    )

    staged = await stage_items_for_prompt_target(
        [
            DroppedItem(
                name="failure.txt",
                mime="text/plain",
                data=b"BAR_FILE_TOKEN_42",
            )
        ],
        registry=registry,
    )

    assert staged is not None
    assert staged.terminal == "T2"
    assert staged.files == ("failure.txt",)
    term = session.find("T2")
    assert term is not None
    assert len(term.pending_prompt_attachment_batches) == 1
    batch = term.pending_prompt_attachment_batches[0]
    assert batch.batch_id == staged.batch_id
    assert batch.attachments[0].detail == "BAR_FILE_TOKEN_42"
    copied = tmp_path / batch.attachments[0].reference.strip('"').lstrip("@")
    assert copied.read_bytes() == b"BAR_FILE_TOKEN_42"

    received: list = []

    async def _compose(_utterance: str, **kwargs):
        received.extend(kwargs["attachments"])
        return SimpleNamespace(
            text="Use the attached failure report.",
            files=(batch.attachments[0].reference,),
            composed_by="test",
        )

    async def _send(_name: str, _text: str):
        return SimpleNamespace(submitted=True)

    term.status = "live"
    term.pty_id = "pty-t2"
    delivered = await fanout.deliver(
        session=session,
        terminals=["T2"],
        utterance="Fix the failure in the file I dropped.",
        compose=_compose,
        send=_send,
        include_pending_attachments=True,
    )

    assert delivered.delivered
    assert received[0].detail == "BAR_FILE_TOKEN_42"
    assert term.pending_prompt_attachment_batches == []


@pytest.mark.asyncio
async def test_bar_drop_without_visible_prompt_target_stays_unstaged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    registry = Registry(pty_manager=FakePtyManager())
    await registry.start(str(tmp_path), [{"agent": "claude"}])

    staged = await stage_items_for_prompt_target(
        [DroppedItem(name="notes.txt", mime="text/plain", data=b"context")],
        registry=registry,
    )

    assert staged is None
    assert not (tmp_path / ".jarvis").exists()
