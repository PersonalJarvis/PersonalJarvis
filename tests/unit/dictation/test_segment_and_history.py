"""Segment cutting and the local dictation history.

The cut matters because a closed segment is transcribed ONCE and never
revisited — a cut through the middle of a word is permanent damage, which is
why it lands at the quietest point near the nominal length rather than exactly
on it.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from jarvis.dictation.history import DictationHistory, _prune
from jarvis.dictation.segment import quietest_cut

SR = 16_000
BPS = SR * 2  # 16 kHz mono int16


def _tone(seconds: float, amplitude: int = 3000) -> np.ndarray:
    t = np.arange(int(seconds * SR))
    return (amplitude * np.sin(2 * np.pi * 220 * t / SR)).astype(np.int16)


# --------------------------------------------------------------------------
# quietest_cut
# --------------------------------------------------------------------------


def test_cut_lands_in_the_silent_gap() -> None:
    audio = _tone(10.0)
    audio[int(6.8 * SR) : int(7.3 * SR)] = 0  # a clear pause
    cut = quietest_cut(audio.tobytes(), int(8 * BPS), BPS)
    assert 6.8 * BPS <= cut <= 7.3 * BPS


def test_cut_is_sample_aligned() -> None:
    audio = _tone(10.0)
    audio[int(7.0 * SR) : int(7.4 * SR)] = 0
    assert quietest_cut(audio.tobytes(), int(8 * BPS), BPS) % 2 == 0


def test_cut_never_exceeds_the_buffer() -> None:
    audio = _tone(3.0)
    cut = quietest_cut(audio.tobytes(), int(8 * BPS), BPS)
    assert cut <= len(audio.tobytes())


def test_short_buffer_falls_back_to_the_nominal_cut() -> None:
    pcm = b"\x00" * 1000
    assert quietest_cut(pcm, 8 * BPS, BPS) == 1000


def test_zero_nominal_is_zero() -> None:
    assert quietest_cut(_tone(2.0).tobytes(), 0, BPS) == 0


def test_uniformly_loud_audio_still_returns_a_usable_cut() -> None:
    """No pause anywhere — the cut must still be inside the search window."""
    audio = _tone(10.0)
    cut = quietest_cut(audio.tobytes(), int(8 * BPS), BPS)
    assert 6.4 * BPS <= cut <= 8 * BPS


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------


@pytest.fixture()
def history(tmp_path: Path) -> DictationHistory:
    return DictationHistory(tmp_path / "dictation_history.json")


def test_empty_history_reads_as_empty_list(history: DictationHistory) -> None:
    assert history.list_all() == []


def test_add_stores_raw_and_cleaned(history: DictationHistory) -> None:
    entry = history.add(
        raw_text="Ähm das ist gut",  # i18n-allow: German fixture under test (§1 list #4)
        text="Das ist gut",  # i18n-allow: German fixture under test (§1 list #4)
        language="de",
        outcome="inserted",
        removed_words=1,
    )
    assert entry is not None
    stored = history.list_all()
    assert len(stored) == 1
    assert stored[0].raw_text == "Ähm das ist gut"  # i18n-allow: German fixture under test (§1 list #4)
    assert stored[0].text == "Das ist gut"  # i18n-allow: German fixture under test (§1 list #4)
    assert stored[0].removed_words == 1


def test_newest_first_and_capped(history: DictationHistory) -> None:
    for i in range(6):
        history.add(raw_text=f"x{i}", text=f"x{i}", max_entries=3)
    assert [e.text for e in history.list_all()] == ["x5", "x4", "x3"]


def test_delete_and_clear(history: DictationHistory) -> None:
    history.add(raw_text="a", text="a")
    history.add(raw_text="b", text="b")
    target = history.list_all()[0].id
    assert history.delete(target) is True
    assert history.delete(target) is False  # idempotent
    assert len(history.list_all()) == 1
    assert history.clear() is True
    assert history.list_all() == []


def test_corrupt_file_reads_as_empty_instead_of_raising(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json at all", encoding="utf-8")
    assert DictationHistory(path).list_all() == []


def test_a_single_bad_row_does_not_invalidate_the_rest(tmp_path: Path) -> None:
    path = tmp_path / "mixed.json"
    path.write_text(
        '{"version": 1, "entries": ['
        '"not-an-object",'
        '{"id": "a", "created_at": "2026-07-28T00:00:00+00:00", "text": "kept"}'
        "]}",
        encoding="utf-8",
    )
    entries = DictationHistory(path).list_all()
    assert [e.text for e in entries] == ["kept"]


def test_empty_add_is_a_no_op(history: DictationHistory) -> None:
    assert history.add(raw_text="", text="") is None
    assert history.list_all() == []


# --------------------------------------------------------------------------
# Pruning
# --------------------------------------------------------------------------


def _entry(days_old: float, text: str = "x"):
    from jarvis.dictation.history import DictationEntry

    created = datetime.now(UTC) - timedelta(days=days_old)
    return DictationEntry(
        id=text, created_at=created.isoformat(), raw_text=text, text=text
    )


def test_retention_drops_old_entries() -> None:
    kept = _prune(
        [_entry(1, "fresh"), _entry(40, "old")], max_entries=100, retention_days=30
    )
    assert [e.text for e in kept] == ["fresh"]


def test_retention_zero_keeps_everything_up_to_the_cap() -> None:
    kept = _prune(
        [_entry(1, "a"), _entry(400, "b")], max_entries=100, retention_days=0
    )
    assert len(kept) == 2


def test_unparseable_timestamp_is_kept_not_silently_discarded() -> None:
    from jarvis.dictation.history import DictationEntry

    broken = DictationEntry(id="b", created_at="not-a-date", raw_text="b", text="b")
    kept = _prune([broken], max_entries=100, retention_days=30)
    assert [e.text for e in kept] == ["b"]


def test_zero_cap_keeps_nothing() -> None:
    assert _prune([_entry(1)], max_entries=0, retention_days=0) == []
