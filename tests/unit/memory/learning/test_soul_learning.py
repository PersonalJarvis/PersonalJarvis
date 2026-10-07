"""The learning loop keeps SOUL.md, the assistant's own character, up to date.

Real files under ``tmp_path`` and a scripted reviewer, like
``test_jarvis_learning``; no provider, no mocks.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.memory.learning import notebook as notebook_module
from jarvis.memory.learning.loop import JarvisLearningLoop
from jarvis.memory.learning.notebook import JarvisNotebook
from jarvis.memory.learning.review import Turn, validate
from jarvis.memory.soul import Soul
from jarvis.memory.templates import render_soul_md

NOTE = "The assistant introduces itself by its own name, never as the app."


class ScriptedReviewer:
    def __init__(self, *replies: list[dict[str, Any]] | None) -> None:
        self._replies = list(replies)
        self.prompts: list[dict[str, Any]] = []

    async def __call__(self, prompt: str) -> list[dict[str, Any]] | None:
        self.prompts.append(json.loads(prompt))
        return self._replies.pop(0) if self._replies else []


@pytest.fixture(autouse=True)
def _no_active_notebook():
    notebook_module.set_active(None)
    yield
    notebook_module.set_active(None)


@pytest.fixture
def soul_path(tmp_path: Path) -> Path:
    path = tmp_path / "workspace" / "SOUL.md"
    path.parent.mkdir()
    path.write_text(render_soul_md(), encoding="utf-8")
    return path


@pytest.fixture
def book(tmp_path: Path, soul_path: Path) -> JarvisNotebook:
    return JarvisNotebook(tmp_path / "vault", name="George", soul_path=soul_path)


async def _drain(loop: JarvisLearningLoop) -> None:
    for _ in range(50):
        if not loop._tasks:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("reviews did not finish")


def test_the_review_never_writes_soul() -> None:
    # The live conversation model keeps SOUL.md current with update_soul; a
    # review proposal for it is refused so there is exactly one writer.
    text = "The assistant never introduces itself as Personal Jarvis."
    accepted, rejected = validate(
        [
            {
                "target": "soul",
                "operation": "add",
                "text": text,
                "evidence": "Du heißt George, nicht Personal Jarvis",  # i18n-allow: user quote
                "importance": 9,
            }
        ],
        user_texts=["Du heißt George, nicht Personal Jarvis"],  # i18n-allow: user quote
        entries={"user": [], "memory": [], "soul": []},
    )
    assert accepted == []
    assert rejected and "invalid target" in rejected[0]


def test_an_unknown_target_is_still_rejected() -> None:
    accepted, rejected = validate(
        [{"target": "persona", "operation": "add", "text": NOTE, "evidence": "x" * 20}],
        user_texts=["x" * 20],
        entries={"user": [], "memory": [], "soul": []},
    )
    assert accepted == [] and rejected


def test_soul_changes_land_in_soul_md_and_the_ledger(book: JarvisNotebook, soul_path: Path) -> None:
    change = book.apply(target="soul", operation="add", text=NOTE, evidence="your name is George")

    assert change is not None
    assert [e.text for e in Soul.load(soul_path).learned()] == [NOTE]
    assert [e.text for e in book.entries()["soul"]] == [NOTE]
    ledger = (book.folder / notebook_module.LEDGER_NAME).read_text(encoding="utf-8")
    assert '"target": "soul"' in ledger

    entry = book.entries()["soul"][0]
    book.apply(
        target="soul",
        operation="replace",
        entry_id=entry.id,
        text="The assistant introduces itself as George.",
        expected=NOTE,
    )
    assert [e.text for e in Soul.load(soul_path).learned()] == [
        "The assistant introduces itself as George."
    ]
    # The editorial parts of the file are untouched.
    assert "## Tone rules" in soul_path.read_text(encoding="utf-8")


def test_the_learned_snapshot_leaves_soul_to_the_identity_block(book: JarvisNotebook) -> None:
    book.apply(target="soul", operation="add", text=NOTE)
    assert book.snapshot() == ""  # SOUL.md reaches prompts through jarvis.brain.identity


def test_warming_writes_the_live_name_into_soul_md(book: JarvisNotebook, soul_path: Path) -> None:
    book.warm()
    assert Soul.load(soul_path).name == "George"


def test_without_a_soul_file_the_target_is_empty(tmp_path: Path) -> None:
    book = JarvisNotebook(tmp_path / "vault")
    assert book.entries()["soul"] == []
    with pytest.raises(ValueError):
        book.apply(target="soul", operation="add", text=NOTE)


async def test_the_review_neither_shows_nor_writes_soul(
    book: JarvisNotebook, soul_path: Path
) -> None:
    book.apply(target="soul", operation="add", text=NOTE)
    said = "I am moving to Hamburg next month, and my sister lives there too"
    reviewer = ScriptedReviewer(
        [
            {
                "target": "soul",
                "operation": "add",
                "text": "The assistant is moving to Hamburg.",
                "evidence": "moving to Hamburg next month",
                "importance": 9,
            }
        ]
    )
    loop = JarvisLearningLoop(book, reviewer, review_every_turns=50, idle_review_seconds=0)
    loop.record("voice:s", Turn(user=said))
    await loop._on_voice_ended(SimpleNamespace(session_id="s"))
    await _drain(loop)

    assert loop.review_calls == 1
    [shown] = reviewer.prompts
    assert set(shown["notebooks"]) == {"user", "memory"}  # no tokens spent on SOUL.md
    assert [e.text for e in Soul.load(soul_path).learned()] == [NOTE]
