"""The notebooks stay small: free deduplication, safe merges, bounded files.

Real notebook files under ``tmp_path`` and a scripted compactor; no provider.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from jarvis.memory.learning import compact
from jarvis.memory.learning import notebook as notebook_module
from jarvis.memory.learning.loop import JarvisLearningLoop
from jarvis.memory.learning.notebook import JarvisNotebook
from jarvis.memory.learning.review import Turn


class Scripted:
    def __init__(self, *replies: Any) -> None:
        self._replies = list(replies)
        self.prompts: list[dict[str, Any]] = []

    async def __call__(self, prompt: str) -> Any:
        self.prompts.append(json.loads(prompt))
        return self._replies.pop(0) if self._replies else None


@pytest.fixture
def book(tmp_path: Path) -> JarvisNotebook:
    return JarvisNotebook(tmp_path / "vault", budgets={"user": 600, "memory": 600})


def _texts(book: JarvisNotebook, target: str = "user") -> list[str]:
    return [e.text for e in book.entries()[target]]


def _ids(book: JarvisNotebook, target: str = "user") -> dict[str, str]:
    return {e.text: e.id for e in book.entries()[target]}


def _fill(book: JarvisNotebook) -> None:
    """About 440 characters: past the 80 % trigger of a 500-character budget."""
    book.budgets["user"] = 500
    for text in (
        "The user lives in Berlin.",
        "The user moved to Berlin in 2024.",
        "The user works as a carpenter.",
        "The user runs a small carpentry workshop in Berlin-Kreuzberg.",
        "The user's launch event is on 2020-03-01.",
        "The user prefers short spoken answers without small talk.",
        "The user's daughter Mia was born on 2019-05-02.",
        "The user likes to hear the weather first thing in the morning.",
        "The user usually works late on weekdays and stops at noon on Fridays.",
    ):
        book.apply(target="user", operation="add", text=text, importance=7)


async def test_duplicates_are_dropped_for_free(book: JarvisNotebook) -> None:
    book.apply(target="user", operation="add", text="The user lives in Berlin.")
    book.apply(target="user", operation="add", text="The user lives in Berlin, Kreuzberg.")
    book.apply(target="user", operation="add", text="the user lives in berlin")
    loop = JarvisLearningLoop(book, Scripted(), idle_review_seconds=0)
    assert await loop.compact() == 2
    assert _texts(book) == ["The user lives in Berlin, Kreuzberg."]
    assert loop.compact_calls == 0


async def test_a_full_notebook_is_merged_safely(book: JarvisNotebook) -> None:
    _fill(book)
    ids = _ids(book)
    compactor = Scripted(
        {
            "merged": [
                {
                    "sources": [
                        ids["The user lives in Berlin."],
                        ids["The user moved to Berlin in 2024."],
                    ],
                    "text": "The user has lived in Berlin since 2024.",
                    "importance": 7,
                },
                {  # invents a number: rejected
                    "sources": [
                        ids["The user works as a carpenter."],
                        ids["The user runs a small carpentry workshop in Berlin-Kreuzberg."],
                    ],
                    "text": "The user is a carpenter with 12 employees in Berlin-Kreuzberg.",
                },
            ],
            "outdated": [
                ids["The user's launch event is on 2020-03-01."],  # past plan: dropped
                ids["The user's daughter Mia was born on 2019-05-02."],  # no: never a plan
            ],
        }
    )
    loop = JarvisLearningLoop(book, Scripted(), idle_review_seconds=0, compactor=compactor)
    before = book.usage()["user"][0]
    await loop.compact()

    texts = _texts(book)
    assert "The user has lived in Berlin since 2024." in texts
    assert "The user lives in Berlin." not in texts
    assert "The user works as a carpenter." in texts  # the inventive merge was refused
    assert "The user's launch event is on 2020-03-01." not in texts
    assert book.usage()["user"][0] < before
    assert compactor.prompts[0]["notebook"] == "USER.md"
    ledger = (book.folder / ".learning-ledger.jsonl").read_text("utf-8")
    assert "compaction: merged" in ledger and "The user lives in Berlin." in ledger


def test_the_birthday_is_not_outdated_but_a_past_plan_is() -> None:
    from types import SimpleNamespace as NS

    rows = [
        NS(id="a", text="The user's dentist appointment is on 2026-01-10.", importance=5),
        NS(id="b", text="The user's launch is on 2099-01-01.", importance=5),
    ]
    _, outdated, rejected = compact.validate(
        {"outdated": ["a", "b"]}, rows, today=date(2026, 9, 30)
    )
    assert outdated == ["a"]
    assert rejected == ["outdated entry names no past date"]


@pytest.mark.parametrize(
    "merge",
    [
        {"sources": ["a"], "text": "x"},  # one source
        {"sources": ["a", "zzz"], "text": "The user likes tea."},  # unknown source
        {"sources": ["a", "b"], "text": "The user likes tea and coffee and cake and much more."},
        {"sources": ["a", "b"], "text": "The user adores expensive yachts."},  # invented
    ],
)
def test_unsafe_merges_are_refused(merge: dict[str, Any]) -> None:
    from types import SimpleNamespace as NS

    rows = [
        NS(id="a", text="The user likes tea.", importance=5),
        NS(id="b", text="The user likes coffee.", importance=6),
    ]
    merges, _, rejected = compact.validate({"merged": [merge]}, rows)
    assert merges == [] and len(rejected) == 1


async def test_merging_waits_between_passes(book: JarvisNotebook) -> None:
    _fill(book)
    compactor = Scripted({"merged": [], "outdated": []}, {"merged": [], "outdated": []})
    loop = JarvisLearningLoop(book, Scripted(), idle_review_seconds=0, compactor=compactor)
    await loop.compact()
    await loop.compact()
    assert loop.compact_calls == 1  # the second pass is inside the cooldown
    fresh = JarvisLearningLoop(book, Scripted(), idle_review_seconds=0, compactor=compactor)
    await fresh.compact()
    assert fresh.compact_calls == 0  # the cooldown survives a restart
    await fresh.compact(force=True)
    assert fresh.compact_calls == 1


async def test_a_below_threshold_notebook_costs_nothing(book: JarvisNotebook) -> None:
    book.apply(target="user", operation="add", text="The user lives in Berlin.")
    compactor = Scripted()
    loop = JarvisLearningLoop(book, Scripted(), idle_review_seconds=0, compactor=compactor)
    await loop.compact()
    assert compactor.prompts == []


def test_a_replace_may_not_grow_a_full_notebook(book: JarvisNotebook) -> None:
    for index in range(14):
        book.apply(target="user", operation="add", text=f"Fact {index:02}: " + "x" * 40)
    first = book.entries()["user"][0]
    with pytest.raises(ValueError, match="full"):
        book.apply(target="user", operation="replace", entry_id=first.id, text="y" * 200)
    book.apply(target="user", operation="replace", entry_id=first.id, text="Fact 00: short")


async def test_an_explicit_request_is_kept_even_when_full(book: JarvisNotebook) -> None:
    for index in range(14):
        book.apply(target="user", operation="add", text=f"Fact {index:02}: " + "x" * 40)
    loop = JarvisLearningLoop(book, Scripted(), idle_review_seconds=0)
    loop.record("voice:a", Turn(user="Merk dir, dass mein Chef Herr Adler heißt"))  # i18n-allow
    for _ in range(100):
        if not loop._tasks:
            break
        import asyncio

        await asyncio.sleep(0.01)
    texts = _texts(book) + _texts(book, "memory")
    assert any("Herr Adler" in t for t in texts)
    assert any(t.startswith(date.today().isoformat() + " (the user's words): ") for t in texts)


def test_the_ledger_stays_bounded(book: JarvisNotebook, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notebook_module, "_LEDGER_MAX_BYTES", 400)
    for index in range(12):
        book.apply(target="memory", operation="add", text=f"The user's device number {index}.")
    ledger = book.folder / ".learning-ledger.jsonl"
    assert ledger.stat().st_size < 1_000
    assert (book.folder / ".learning-ledger.jsonl.1").is_file()
