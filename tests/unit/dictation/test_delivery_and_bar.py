"""Delivery of a finished dictation, and the bar's new coarse mode.

``_finish_dictation`` is the half of the dictation session that has nothing to
do with a microphone: clean, publish, insert, record. Testing it directly is
what makes the delivery contract verifiable without audio hardware.

The bar tests pin the promise made when the mode was added: the four existing
voice modes behave EXACTLY as before, and a click during dictation cannot start
a voice session.
"""
from __future__ import annotations

import pytest

from jarvis.core.config import DictationConfig
from jarvis.core.events import DictationCompleted, DictationTranscript
from jarvis.dictation.insert import InsertResult
from jarvis.speech.pipeline import SpeechPipeline
from jarvis.ui.jarvisbar import interaction, renderer

# --------------------------------------------------------------------------
# Delivery
# --------------------------------------------------------------------------


def _pipeline(cfg: DictationConfig | None = None, *, insert: InsertResult | None = None):
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._dictation_cfg = cfg or DictationConfig(history_enabled=False)
    events: list[object] = []

    async def _publish(event: object) -> None:
        events.append(event)

    pipe._publish_event = _publish  # type: ignore[assignment]
    pipe._insert_dictation = lambda text: insert or InsertResult(  # type: ignore[assignment]
        status="inserted", detail="", clipboard_holds_text=False,
        method="clipboard+ctrl_v",
    )
    return pipe, events


@pytest.mark.asyncio
async def test_cleans_then_inserts() -> None:
    pipe, events = _pipeline()
    text = await pipe._finish_dictation(
        raw_text="Ähm, das ist äh wirklich gut.",  # i18n-allow: German fixture under test (§1 list #4)
        language="de",
        duration_s=3.0,
        target="insert",
        hung_up=False,
    )
    assert text == "Das ist wirklich gut."  # i18n-allow: German fixture under test (§1 list #4)

    transcript = next(e for e in events if isinstance(e, DictationTranscript))
    completed = next(e for e in events if isinstance(e, DictationCompleted))
    assert transcript.is_final is True
    assert transcript.text == "Das ist wirklich gut."  # i18n-allow: German fixture under test (§1 list #4)
    assert completed.outcome == "inserted"
    assert completed.raw_text == "Ähm, das ist äh wirklich gut."  # i18n-allow: German fixture under test (§1 list #4)
    assert completed.removed_words == 2


@pytest.mark.asyncio
async def test_chat_target_never_inserts() -> None:
    """The chat composer's mic button must not type into other apps."""
    inserted: list[str] = []
    pipe, events = _pipeline()
    pipe._insert_dictation = lambda text: inserted.append(text)  # type: ignore[assignment]

    await pipe._finish_dictation(
        raw_text="hello there", language="en", duration_s=1.0,
        target="chat", hung_up=False,
    )
    assert inserted == []
    completed = next(e for e in events if isinstance(e, DictationCompleted))
    assert completed.outcome == "chat"


@pytest.mark.asyncio
async def test_blocked_insertion_surfaces_the_reason() -> None:
    blocked = InsertResult(
        status="clipboard_only",
        detail="The window in front is running as administrator.",
        clipboard_holds_text=True,
    )
    pipe, events = _pipeline(insert=blocked)
    await pipe._finish_dictation(
        raw_text="hello there", language="en", duration_s=1.0,
        target="insert", hung_up=False,
    )
    completed = next(e for e in events if isinstance(e, DictationCompleted))
    assert completed.outcome == "clipboard_only"
    assert "administrator" in completed.detail


@pytest.mark.asyncio
async def test_hangup_cancels_without_inserting() -> None:
    inserted: list[str] = []
    pipe, events = _pipeline()
    pipe._insert_dictation = lambda text: inserted.append(text)  # type: ignore[assignment]

    await pipe._finish_dictation(
        raw_text="", language="", duration_s=0.4, target="insert", hung_up=True,
    )
    assert inserted == []
    completed = next(e for e in events if isinstance(e, DictationCompleted))
    assert completed.outcome == "cancelled"


@pytest.mark.asyncio
async def test_empty_transcript_is_reported_as_empty() -> None:
    pipe, events = _pipeline()
    await pipe._finish_dictation(
        raw_text="", language="en", duration_s=0.2, target="insert", hung_up=False,
    )
    completed = next(e for e in events if isinstance(e, DictationCompleted))
    assert completed.outcome == "empty"


@pytest.mark.asyncio
async def test_cleanup_can_be_switched_off() -> None:
    pipe, _events = _pipeline(
        DictationConfig(remove_fillers=False, history_enabled=False)
    )
    text = await pipe._finish_dictation(
        raw_text="Um, hello there friend.", language="en", duration_s=1.0,
        target="chat", hung_up=False,
    )
    assert text == "Um, hello there friend."


@pytest.mark.asyncio
async def test_auto_target_is_resolved_at_DELIVERY_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Start in the app, switch to the target, speak — the switch must count.

    Resolving "auto" when recording STARTS would send that text to the chat box,
    because Jarvis was in front at the moment the button was clicked.
    """
    import jarvis.dictation.insert as insert_mod

    inserted: list[str] = []
    pipe, events = _pipeline()
    pipe._insert_dictation = lambda text: inserted.append(text) or InsertResult(  # type: ignore[assignment]
        status="inserted", detail="", clipboard_holds_text=False, method="clipboard+ctrl_v",
    )

    monkeypatch.setattr(insert_mod, "foreground_is_this_app", lambda: False)
    await pipe._finish_dictation(
        raw_text="into the other app", language="en", duration_s=1.0,
        target="auto", hung_up=False,
    )
    assert inserted == ["into the other app"]

    inserted.clear()
    monkeypatch.setattr(insert_mod, "foreground_is_this_app", lambda: True)
    await pipe._finish_dictation(
        raw_text="into our own box", language="en", duration_s=1.0,
        target="auto", hung_up=False,
    )
    assert inserted == []
    assert events[-1].outcome == "chat"


@pytest.mark.asyncio
async def test_a_broken_cleanup_never_loses_the_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a, **_k):
        raise RuntimeError("rule exploded")

    monkeypatch.setattr("jarvis.dictation.cleanup.clean_transcript", _boom)
    pipe, _events = _pipeline()
    text = await pipe._finish_dictation(
        raw_text="the raw words", language="en", duration_s=1.0,
        target="chat", hung_up=False,
    )
    assert text == "the raw words"


# --------------------------------------------------------------------------
# The Jarvis Bar
# --------------------------------------------------------------------------


def test_dictate_renders_as_the_equalizer() -> None:
    """Dictation is the user talking — never the thinking indicator."""
    assert renderer.visual_mode("dictate", 99.0, hold_s=0.4) == "speak"
    assert renderer.visual_mode("dictate", 0.0, hold_s=0.4) == "speak"


@pytest.mark.parametrize(
    ("mode", "seconds_since_audible", "playback", "expected"),
    [
        ("idle", 99.0, False, "idle"),
        ("listen", 99.0, False, "speak"),
        ("listen", 0.0, False, "speak"),
        ("think", 99.0, False, "think"),
        ("think", 0.0, False, "speak"),
        ("speak", 99.0, False, "speak"),
        ("speak", 0.0, True, "speak"),
    ],
)
def test_existing_visual_modes_are_unchanged(
    mode: str, seconds_since_audible: float, playback: bool, expected: str
) -> None:
    assert (
        renderer.visual_mode(
            mode, seconds_since_audible, hold_s=0.4, playback_active=playback
        )
        == expected
    )


@pytest.mark.parametrize("x", [10, 100, 400, 700])
def test_clicking_the_bar_during_dictation_does_nothing(x: int) -> None:
    """Without this, a stray click would start a voice session mid-dictation."""
    assert interaction.resolve_click(x, 800, "dictate", hovered=True, pill_w=400) == "none"
    assert interaction.resolve_click(x, 800, "dictate", hovered=False) == "none"


def test_existing_click_zones_are_unchanged() -> None:
    assert interaction.resolve_click(100, 800, "idle") == "talk"
    assert interaction.resolve_click(700, 800, "idle") == "mute"
    assert interaction.resolve_click(700, 800, "listen") == "mute"
    assert interaction.resolve_click(400, 800, "listen", hovered=True, pill_w=400) == "none"
