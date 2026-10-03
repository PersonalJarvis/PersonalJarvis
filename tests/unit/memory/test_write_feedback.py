"""Memory write receipts: pending before the write, saved only after it persisted.

Real files under ``tmp_path`` and a recording sink; no provider, no mocks.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import Event, MemoryFileWrite
from jarvis.memory import write_feedback
from jarvis.memory.learning import notebook as notebook_module
from jarvis.memory.learning.notebook import OWNER_ID, JarvisNotebook
from jarvis.memory.templates import render_soul_md
from jarvis.ui.web.schema import event_to_ws_envelope

PLAIN_FACT = "The user likes short answers in the morning."


class RecordingSink:
    def __init__(self, inspect: Path | None = None) -> None:
        self.calls: list[tuple[str, str, str, str, str]] = []
        self.content_when: dict[str, str] = {}
        self._inspect = inspect

    def __call__(self, update_id: str, agent_id: str, file: str, phase: str, op: str) -> None:
        self.calls.append((update_id, agent_id, file, phase, op))
        if self._inspect is not None:
            path = self._inspect
            self.content_when[phase] = path.read_text(encoding="utf-8") if path.is_file() else ""

    def phases(self) -> list[tuple[str, str]]:
        return [(file, phase) for _id, _agent, file, phase, _op in self.calls]


@pytest.fixture(autouse=True)
def _isolated():
    write_feedback.disconnect()
    notebook_module.set_active(None)
    yield
    write_feedback.disconnect()
    notebook_module.set_active(None)


@pytest.fixture
def book(tmp_path: Path) -> JarvisNotebook:
    soul = tmp_path / "workspace" / "SOUL.md"
    soul.parent.mkdir()
    soul.write_text(render_soul_md(), encoding="utf-8")
    return JarvisNotebook(tmp_path / "vault", name="George", soul_path=soul)


def test_without_a_sink_the_write_runs_and_nothing_is_announced():
    with write_feedback.announce_write("jarvis", "MEMORY.md", operation="add") as receipt:
        receipt.changed = True
    assert receipt.file == "MEMORY.md"


def test_saved_is_announced_only_after_the_file_holds_the_entry(book: JarvisNotebook):
    memory = book.paths()["memory"]
    sink = RecordingSink(inspect=memory)
    write_feedback.set_sink(sink)

    book.apply(target="memory", operation="add", text=PLAIN_FACT)

    assert sink.phases() == [("MEMORY.md", "pending"), ("MEMORY.md", "saved")]
    assert PLAIN_FACT not in sink.content_when["pending"]
    assert PLAIN_FACT in sink.content_when["saved"]
    first, second = sink.calls
    assert first[0] == second[0] and len(first[0]) == 32  # one update id for both steps
    assert first[1] == OWNER_ID and first[4] == "add"


def test_each_target_names_its_real_file(book: JarvisNotebook):
    sink = RecordingSink()
    write_feedback.set_sink(sink)
    book.apply(target="user", operation="add", text="The user is called Ruben.")
    book.apply(target="soul", operation="add", text="The assistant keeps jokes dry.")
    saved = [file for file, phase in sink.phases() if phase == "saved"]
    assert saved == ["USER.md", "SOUL.md"]


def test_a_duplicate_settles_as_unchanged_never_saved(book: JarvisNotebook):
    book.apply(target="memory", operation="add", text=PLAIN_FACT)
    sink = RecordingSink()
    write_feedback.set_sink(sink)
    assert book.apply(target="memory", operation="add", text=PLAIN_FACT) is None
    assert sink.phases() == [("MEMORY.md", "pending"), ("MEMORY.md", "unchanged")]


def test_a_failed_write_is_announced_as_failed_and_still_raises(tmp_path: Path):
    missing = tmp_path / "workspace" / "SOUL.md"  # never created: the write must fail
    book = JarvisNotebook(tmp_path / "vault", name="George", soul_path=missing)
    sink = RecordingSink()
    write_feedback.set_sink(sink)
    with pytest.raises(FileNotFoundError):
        book.apply(target="soul", operation="add", text="The assistant is calm.")
    assert sink.phases() == [("SOUL.md", "pending"), ("SOUL.md", "failed")]


def test_a_refused_change_announces_nothing(book: JarvisNotebook):
    sink = RecordingSink()
    write_feedback.set_sink(sink)
    with pytest.raises(ValueError):
        book.apply(target="memory", operation="remove", entry_id="does-not-exist")
    assert sink.calls == []


def test_a_broken_sink_never_breaks_the_write(book: JarvisNotebook):
    def boom(*_args: Any) -> None:
        raise RuntimeError("ui gone")

    write_feedback.set_sink(boom)
    change = book.apply(target="memory", operation="add", text=PLAIN_FACT)
    assert change is not None
    assert PLAIN_FACT in book.paths()["memory"].read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("MEMORY.md", "MEMORY.md"),
        ("C:\\Users\\someone\\vault\\society\\jarvis\\USER.md", "USER.md"),
        ("/home/someone/data/workspace/SOUL.md", "SOUL.md"),
        ("", ""),
        ("..", ""),
        ("bad name with spaces.md", ""),
    ],
)
def test_only_a_bare_file_name_ever_leaves(raw: str, shown: str):
    assert write_feedback.safe_file_name(raw) == shown


def test_a_malformed_agent_id_is_never_forwarded():
    sink = RecordingSink()
    write_feedback.set_sink(sink)
    with write_feedback.announce_write("../etc", "MEMORY.md"):
        pass
    assert sink.calls == []


async def test_connect_publishes_ordered_events_from_a_worker_thread(book: JarvisNotebook):
    bus = EventBus()
    seen: list[Event] = []

    async def record(event: Event) -> None:
        seen.append(event)

    bus.subscribe(MemoryFileWrite, record)
    write_feedback.connect(bus)

    worker = threading.Thread(
        target=book.apply, kwargs={"target": "memory", "operation": "add", "text": PLAIN_FACT}
    )
    worker.start()
    await asyncio.to_thread(worker.join)
    for _ in range(50):
        if len(seen) >= 2:
            break
        await asyncio.sleep(0.01)

    assert [e.phase for e in seen] == ["pending", "saved"]  # type: ignore[attr-defined]
    envelope = event_to_ws_envelope(seen[1])
    assert envelope["event_name"] == "MemoryFileWrite"
    assert set(envelope["payload"]) == {"update_id", "agent_id", "file", "phase", "operation"}
    assert envelope["payload"]["file"] == "MEMORY.md"
    assert PLAIN_FACT not in str(envelope)


async def test_remember_tool_announces_the_memory_file(book: JarvisNotebook):
    from types import SimpleNamespace

    from jarvis.plugins.tool.remember import RememberTool

    notebook_module.set_active(book)
    sink = RecordingSink()
    write_feedback.set_sink(sink)
    result = await RememberTool().execute(
        {"fact": "Ruben's sister is called Mia."}, SimpleNamespace(user_utterance="")
    )
    assert result.success
    assert sink.phases() == [("MEMORY.md", "pending"), ("MEMORY.md", "saved")]


async def test_an_agent_notebook_write_names_its_file(tmp_path: Path):
    from jarvis.society.runtime import SocietyRuntime

    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    await runtime.ensure_started()
    try:
        await runtime.roster.create(name="Scout")
        scout = await runtime.roster.get("scout")
        sink = RecordingSink()
        write_feedback.set_sink(sink)
        await runtime.memory.remember(
            scout, "The user deploys on Fridays.", target="memory", root=tmp_path / "vault"
        )
        assert sink.phases() == [("MEMORY.md", "pending"), ("MEMORY.md", "saved")]
        assert {call[1] for call in sink.calls} == {"scout"}
    finally:
        await runtime.close()
