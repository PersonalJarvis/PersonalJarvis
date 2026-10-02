"""An explicit "remember this" lands in MEMORY.md and stays there.

Real notebook files under ``tmp_path``; the conversation model's side is the
real remember tool, the background side the real learning loop with a
scripted reviewer and compactor. No provider, no mocks.
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
from jarvis.memory.learning.notebook import (
    EXPLICIT_IMPORTANCE,
    EXPLICIT_ORIGIN,
    REMEMBER_DIRECTIVE,
    JarvisNotebook,
    memory_block,
)
from jarvis.memory.learning.review import Turn
from jarvis.plugins.tool.remember import RememberTool
from jarvis.society.memory_intent import requested_memory


@pytest.fixture
def book(tmp_path: Path) -> JarvisNotebook:
    return JarvisNotebook(tmp_path / "vault", budgets={"user": 600, "memory": 600})


@pytest.fixture(autouse=True)
def _no_active_notebook():
    notebook_module.set_active(None)
    yield
    notebook_module.set_active(None)


async def _no_review(prompt: str) -> list[dict[str, Any]]:
    del prompt
    return []


async def _drain(loop: JarvisLearningLoop) -> None:
    for _ in range(50):
        if not loop._tasks:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("background work did not finish")


def _memory(book: JarvisNotebook) -> list[Any]:
    return book.entries()["memory"]


# ── recognizing the request ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("said", "wanted"),
    [
        ("Remember that I take my coffee black.", "I take my coffee black."),
        # The request follows what it points at, in the same turn.
        (
            "I want agents briefed to work on their own. Remember that.",
            "I want agents briefed to work on their own",
        ),
        (
            "Ich will kurze Berichte. Merk dir das bitte!",  # i18n-allow
            "Ich will kurze Berichte",  # i18n-allow
        ),
        # Mid-turn, after something unrelated.
        ("Okay, great. Remember: my brother is called Tim.", "my brother is called Tim."),
        ("Remember not to call me after nine.", "not to call me after nine."),
        # A bare reference with nothing before it still asks the model.
        ("Remember that.", ""),
        # Declining or asking is no request.
        ("Merk dir das nicht.", None),  # i18n-allow
        ("Can you tell me what you remember about me?", None),
        ("What time is it? Thanks.", None),
    ],
)
def test_a_remember_request_is_found_where_people_put_it(said: str, wanted: str | None) -> None:
    assert requested_memory(said) == wanted


# ── the notebook ───────────────────────────────────────────────────────────


def test_an_explicit_request_always_lands_in_memory_md(book: JarvisNotebook) -> None:
    # Reads like a profile fact, but the user asked for it: MEMORY.md.
    book.keep_explicit("I am allergic to peanuts.", evidence="remember I am allergic")

    [entry] = _memory(book)
    assert book.entries()["user"] == []
    assert entry.text.endswith("(the user's words): I am allergic to peanuts.")
    assert entry.origin == EXPLICIT_ORIGIN
    assert entry.importance == EXPLICIT_IMPORTANCE
    assert (book.folder / "MEMORY.md").read_text(encoding="utf-8").count("peanuts") == 1


def test_a_secret_is_never_remembered(book: JarvisNotebook) -> None:
    with pytest.raises(ValueError):
        book.keep_explicit("my key is sk-" + "a" * 48)
    assert _memory(book) == []


def test_explicit_entries_reach_the_prompt_beyond_the_regular_budget(
    book: JarvisNotebook,
) -> None:
    for n in range(4):
        book.keep_explicit(f"Project {n} ships from the {'east ' * 25}warehouse.")
    book.apply(target="memory", operation="add", text="The user's printer is in the attic.")

    text = book.render()

    # 4 x ~180 chars is past MEMORY.md's 600-char budget, yet all are shown,
    # in their own section, before what a review inferred.
    assert text.count("ships from the") == 4
    assert text.index("What the user asked you to remember") < text.index(
        "The user's printer is in the attic."
    )


def test_compaction_never_merges_or_expires_an_explicit_entry(book: JarvisNotebook) -> None:
    seen: list[dict[str, Any]] = []

    async def compactor(prompt: str) -> dict[str, Any]:
        data = json.loads(prompt)
        seen.append(data)
        return {"merged": [], "outdated": [e["entry_id"] for e in data["entries"]]}

    for n in range(3):
        book.keep_explicit(f"Remember item {n}: {'details ' * 20}")
    # Enough inferred notes to pass the fill trigger on their own.
    for n in range(4):
        book.apply(
            target="memory", operation="add", text=f"A note from {'review ' * 15}number {n}."
        )
    loop = JarvisLearningLoop(book, _no_review, compactor=compactor)

    asyncio.run(loop.compact(force=True))

    explicit = [e for e in _memory(book) if e.origin == EXPLICIT_ORIGIN]
    assert len(explicit) == 3
    # The merging model is never even shown them (their date prefix would
    # read as a passed deadline).
    assert seen and all("Remember item" not in e["text"] for e in seen[0]["entries"])


def test_the_memory_block_states_the_contract_and_the_notes(book: JarvisNotebook) -> None:
    assert memory_block() == ""
    notebook_module.set_active(book)
    assert memory_block() == REMEMBER_DIRECTIVE
    book.keep_explicit("The user's dog is called Bruno.")

    block = memory_block()

    assert block.startswith(REMEMBER_DIRECTIVE)
    assert "The user's dog is called Bruno." in block


def test_a_gpt_live_call_carries_the_memory(book: JarvisNotebook) -> None:
    from jarvis.live.config import LiveConfig
    from jarvis.live.session import _memory as live_memory

    notebook_module.set_active(book)
    book.keep_explicit("The user's dog is called Bruno.")

    wire = LiveConfig(configured=True, backend_model="m").session_config(
        language="de", tools=[], identity="You are George." + live_memory()
    )

    for instructions in (wire["instructions"], wire["delegation"]["responses"]["instructions"]):
        assert "The user's dog is called Bruno." in instructions
        assert "remember tool" in instructions


# ── the remember tool ──────────────────────────────────────────────────────


async def test_the_remember_tool_saves_into_memory_md(book: JarvisNotebook) -> None:
    notebook_module.set_active(book)
    ctx = SimpleNamespace(user_utterance="remember that my dog is called Bruno")

    result = await RememberTool().execute({"fact": "The user's dog is called Bruno."}, ctx)
    again = await RememberTool().execute({"fact": "The user's dog is called Bruno."}, ctx)

    assert result.success and "MEMORY.md" in result.output
    assert again.success and again.output.startswith("Already")
    [entry] = _memory(book)
    assert "(asked to remember): The user's dog is called Bruno." in entry.text
    assert entry.origin == EXPLICIT_ORIGIN


async def test_without_a_notebook_the_tool_falls_back_to_core_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.memory import CORE_MEMORY_FILENAME, CoreMemory
    from jarvis.plugins.tool import remember

    monkeypatch.setattr(remember, "DATA_DIR", tmp_path)

    result = await RememberTool().execute({"fact": "Tea, not coffee."}, None)

    assert result.success
    assert "Tea, not coffee." in json.dumps(
        CoreMemory.load(tmp_path / CORE_MEMORY_FILENAME).model_dump()
        if hasattr(CoreMemory, "model_dump")
        else (tmp_path / CORE_MEMORY_FILENAME).read_text(encoding="utf-8"),
        ensure_ascii=False,
    )


async def test_a_call_the_tool_already_saved_is_not_filed_twice(book: JarvisNotebook) -> None:
    """GPT-Live reaches the loop as ONE turn when the call ends, long after the tool saved."""
    notebook_module.set_active(book)
    said = (
        "Can you open the workspace? Thanks. I want agents briefed so they can work "
        "on their own. Remember that. And now check the build."
    )
    await RememberTool().execute(
        {"fact": "The user wants agents briefed so they can work on their own."},
        SimpleNamespace(user_utterance="I want agents briefed so they can work on their own."),
    )
    loop = JarvisLearningLoop(book, _no_review, idle_review_seconds=0)

    loop.record("voice:call", Turn(user=said, assistant="Done."))
    await _drain(loop)

    assert len(_memory(book)) == 1


async def test_a_request_the_tool_did_not_see_is_still_saved(book: JarvisNotebook) -> None:
    loop = JarvisLearningLoop(book, _no_review, idle_review_seconds=0)

    loop.record("chat:c", Turn(user="I only drink tea. Remember that.", assistant="Okay."))
    await _drain(loop)

    [entry] = _memory(book)
    assert entry.text.endswith("(the user's words): I only drink tea")


async def test_a_turn_that_used_the_tool_is_not_saved_again(book: JarvisNotebook) -> None:
    loop = JarvisLearningLoop(book, _no_review, idle_review_seconds=0)

    loop.record(
        "chat:c",
        Turn(user="Remember that I only drink tea.", assistant="Saved.", tools=("remember",)),
    )
    await _drain(loop)

    assert _memory(book) == []


# ── the voice tool set ─────────────────────────────────────────────────────


def test_voice_calls_get_the_remember_tool_and_workers_do_not() -> None:
    from jarvis.brain.tool_gateway import BrainSupervisorToolGateway

    manager = SimpleNamespace(_tools={}, _config=SimpleNamespace(computer_use=None))
    gateway = BrainSupervisorToolGateway(manager)

    assert "remember" in {d.name for d in gateway.voice_catalog()}
    assert "remember" not in {d.name for d in gateway.catalog()}
