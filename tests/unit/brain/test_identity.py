"""Every surface states the same identity: the wake-word name plus SOUL.md.

Regression: a GPT-Live call answered "I'm Personal Jarvis" although the wake
word made the assistant George (2026-10-02) — the live instructions
hardcoded the product name and never read SOUL.md.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.brain import identity
from jarvis.brain.identity import character_block, identity_block, name_directive
from jarvis.memory.templates import render_soul_md


def _config(phrase: str) -> SimpleNamespace:
    return SimpleNamespace(trigger=SimpleNamespace(wake_word=SimpleNamespace(phrase=phrase)))


@pytest.fixture(autouse=True)
def _fresh_cache():
    identity.invalidate_cache()
    yield
    identity.invalidate_cache()


@pytest.fixture
def soul(tmp_path: Path) -> Path:
    path = tmp_path / "SOUL.md"
    path.write_text(render_soul_md(), encoding="utf-8")
    return path


def test_the_wake_word_name_is_the_assistants_name_not_the_app() -> None:
    text = name_directive("George")
    assert text.startswith("YOUR NAME IS GEORGE.")
    assert "Personal Jarvis is the name of the app you run inside, not your name" in text
    assert "never introduce yourself as Personal Jarvis, as Jarvis" in text


def test_a_jarvis_wake_word_does_not_forbid_its_own_name() -> None:
    text = name_directive("Jarvis")
    assert "YOUR NAME IS JARVIS" in text
    assert "as Jarvis or" not in text


def test_no_wake_word_gives_no_invented_name() -> None:
    text = name_directive("Assistant")
    assert "no personal name yet" in text
    assert "YOUR NAME IS" not in text


def test_the_block_carries_name_and_character(soul: Path) -> None:
    text = identity_block(_config("Hey George"), path=soul)
    assert text.index("YOUR NAME IS GEORGE") < text.index("## Your character (SOUL.md)")
    assert "Direct, precise, with dry humor." in text


def test_a_missing_soul_still_states_the_name(tmp_path: Path) -> None:
    text = identity_block(_config("Hey George"), path=tmp_path / "missing.md")
    assert text == name_directive("George")


def test_an_edit_reaches_the_next_prompt(soul: Path, monkeypatch) -> None:
    assert "Learned fact" not in character_block(path=soul)
    soul.write_text(
        render_soul_md().replace(
            "<!-- curator:calibration:start -->\n",
            "<!-- curator:calibration:start -->\n- Learned fact about the assistant\n",
        ),
        encoding="utf-8",
    )
    stat = soul.stat()
    os.utime(soul, (stat.st_atime, stat.st_mtime + 5))
    # Past the stat interval, the changed mtime is noticed.
    monkeypatch.setattr(identity, "_STAT_INTERVAL_S", 0.0)
    assert "Learned fact about the assistant" in character_block(path=soul)


def test_the_gpt_live_session_states_the_identity(soul: Path) -> None:
    from jarvis.live.config import LiveConfig

    block = identity_block(_config("Hey George"), path=soul)
    wire = LiveConfig(configured=True, backend_model="m").session_config(
        language="de", tools=[], identity=block
    )
    assert wire["instructions"].startswith("YOUR NAME IS GEORGE")
    assert wire["delegation"]["responses"]["instructions"].startswith("YOUR NAME IS GEORGE")
    assert "You are Personal Jarvis" not in wire["instructions"]


def test_the_gpt_live_session_without_identity_never_claims_the_app_name() -> None:
    from jarvis.live.config import LiveConfig

    wire = LiveConfig(configured=True, backend_model="m").session_config(language="de", tools=[])
    assert "You are Personal Jarvis" not in wire["instructions"]
    assert "no personal name yet" in wire["instructions"]


def test_the_realtime_instructions_open_with_the_identity() -> None:
    from jarvis.realtime.session import _session_instructions

    block = name_directive("George")
    for compact in (False, True):
        text = _session_instructions("en", identity=block, compact=compact)
        assert text.startswith(block)
