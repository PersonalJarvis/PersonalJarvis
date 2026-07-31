"""The dictation store repairs what older writers left behind.

Two defects share one shape: a value that was written wrong ONCE stays wrong
forever, because every write rewrites the whole file from whatever the reader
just produced. So a default that is not repaired on read is a default that gets
persisted again on the next dictation.

* ``word_count`` was added after the first release. Rows written before it read
  as zero, the lifetime counters skip anything at or below zero, and the
  maintainer's 41-row history therefore reported 26 words dictated.
* ``language`` was stored the way each provider happened to spell it — the same
  live history holds ``"English"``, ``"German"``, ``"de"``, ``"en"`` and ``""``
  for two languages, so any consumer indexing by code misses three rows in four
  (the AP-4 / BUG-008 shape).

Both are fixed at the store boundary rather than in the four writers, and both
are asserted here to heal on read AND to stay healed on disk.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from jarvis.dictation.history import DictationHistory


def _row(row_id: str, **fields: Any) -> dict[str, Any]:
    """One on-disk row, spelled the way an older install would have written it."""
    row: dict[str, Any] = {
        "id": row_id,
        "created_at": datetime.now(UTC).isoformat(),
        "raw_text": "",
        "text": "",
    }
    row.update(fields)
    return row


def _legacy_history(path: Path, *rows: dict[str, Any]) -> DictationHistory:
    """A history file placed on disk directly, bypassing every writer."""
    path.write_text(
        json.dumps({"version": 1, "entries": list(rows)}, ensure_ascii=False),
        encoding="utf-8",
    )
    return DictationHistory(path)


def _stored(store: DictationHistory) -> list[dict[str, Any]]:
    """What is actually in the file, not what the reader made of it."""
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    return list(payload["entries"])


def _by_id(store: DictationHistory, row_id: str) -> dict[str, Any]:
    return next(row for row in _stored(store) if row["id"] == row_id)


@pytest.fixture()
def history(tmp_path: Path) -> DictationHistory:
    return DictationHistory(tmp_path / "dictation_history.json")


# --------------------------------------------------------------------------
# F9 — word_count self-heals on read
# --------------------------------------------------------------------------


def test_a_row_written_before_word_count_existed_heals_on_read(
    tmp_path: Path,
) -> None:
    store = _legacy_history(
        tmp_path / "old.json",
        _row("a", raw_text="send the report today", text="send the report today"),
    )
    assert store.list_all()[0].word_count == 4


def test_a_stored_zero_on_a_row_with_text_is_treated_as_never_measured(
    tmp_path: Path,
) -> None:
    """The live shape: the key is present, and its value is a durable lie."""
    store = _legacy_history(
        tmp_path / "zeroed.json",
        _row("a", raw_text="one two three", text="one two three", word_count=0),
    )
    assert store.list_all()[0].word_count == 3


def test_the_healed_count_is_written_back_and_stays_healed(tmp_path: Path) -> None:
    """Healing only on read would recompute the same repair on every load."""
    store = _legacy_history(
        tmp_path / "old.json",
        _row("a", raw_text="one two three four", text="one two three four"),
    )
    # Any write rewrites the whole file from what the reader produced.
    assert store.add(raw_text="later", text="later") is not None
    assert _by_id(store, "a")["word_count"] == 4
    # And a second reader sees the persisted value, not a fresh recomputation.
    assert DictationHistory(store.path).get("a").word_count == 4  # type: ignore[union-attr]


def test_a_row_with_no_text_keeps_its_zero(tmp_path: Path) -> None:
    """A failed dictation really did produce no words — inventing one is worse."""
    store = _legacy_history(
        tmp_path / "failed.json",
        _row("a", outcome="failed", error="provider 401"),
    )
    entry = store.list_all()[0]
    assert entry.word_count == 0
    assert entry.outcome == "failed"


def test_whitespace_only_text_is_not_words(tmp_path: Path) -> None:
    store = _legacy_history(tmp_path / "blank.json", _row("a", text="   \n  "))
    assert store.list_all()[0].word_count == 0


def test_a_measured_count_is_never_recomputed(tmp_path: Path) -> None:
    """An explicit count is the caller's answer and outranks the transcript."""
    store = _legacy_history(
        tmp_path / "explicit.json",
        _row("a", raw_text="a b c", text="a b c", word_count=99),
    )
    assert store.list_all()[0].word_count == 99


def test_the_raw_text_carries_the_heal_when_the_cleaned_text_is_gone(
    tmp_path: Path,
) -> None:
    """Same fallback order the writer uses: ``text or raw_text``."""
    store = _legacy_history(
        tmp_path / "rawonly.json",
        _row("a", raw_text="uh send the report", text=""),
    )
    assert store.list_all()[0].word_count == 4


def test_a_broken_word_count_heals_instead_of_dropping_the_row(
    tmp_path: Path,
) -> None:
    """One unusable field must never cost the user the whole entry."""
    store = _legacy_history(
        tmp_path / "broken.json",
        _row("a", raw_text="one two", text="one two", word_count="not-a-number"),
    )
    entries = store.list_all()
    assert [e.id for e in entries] == ["a"]
    assert entries[0].word_count == 2


def test_the_healed_count_reaches_the_api_shape(tmp_path: Path) -> None:
    store = _legacy_history(
        tmp_path / "old.json", _row("a", raw_text="one two three", text="one two three")
    )
    assert store.list_all()[0].to_dict()["word_count"] == 3


# --------------------------------------------------------------------------
# F10 — one row, one language code
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("written", "stored"),
    [
        ("de", "de"),
        ("DE", "de"),
        ("de-DE", "de"),
        ("de_DE", "de"),
        ("German", "de"),
        ("deutsch", "de"),  # i18n-allow: provider language NAME under test (§1 list #4)
        ("en", "en"),
        ("English", "en"),
        ("en-US", "en"),
        ("es", "es"),
        ("Spanish", "es"),
        ("español", "es"),  # i18n-allow: provider language NAME under test (§1 list #4)
    ],
)
def test_add_collapses_every_spelling_of_a_known_language(
    history: DictationHistory, written: str, stored: str
) -> None:
    entry = history.add(raw_text="x", text="x", language=written)
    assert entry is not None
    assert entry.language == stored
    assert history.list_all()[0].language == stored


@pytest.mark.parametrize("written", ["", "auto", "AUTO", "unknown", "und", "   "])
def test_a_tag_that_is_not_a_language_stores_as_empty(
    history: DictationHistory, written: str
) -> None:
    """"detect it" and "I could not tell" are answers, not languages."""
    entry = history.add(raw_text="x", text="x", language=written)
    assert entry is not None
    assert entry.language == ""


@pytest.mark.parametrize(
    ("written", "stored"),
    [
        ("ja", "ja"),
        ("ja-JP", "ja"),
        ("JA", "ja"),
        ("Japanese", "japanese"),
        ("Polish", "polish"),
        ("zh", "zh"),
        ("yue", "yue"),
    ],
)
def test_a_language_outside_the_product_locales_is_kept_never_coerced(
    history: DictationHistory, written: str, stored: str
) -> None:
    """The canonical resolver answers "unknown" for ~96 of the 99 recognition
    languages, so folding an unresolved tag into the default locale would
    relabel a Japanese dictation as English. The tag survives instead.
    """
    entry = history.add(raw_text="x", text="x", language=written)
    assert entry is not None
    assert entry.language == stored
    assert entry.language != "en"


def test_the_four_live_spellings_collapse_on_read_and_are_written_back(
    tmp_path: Path,
) -> None:
    """The exact shape of the maintainer's history: four spellings, two languages."""
    store = _legacy_history(
        tmp_path / "drifted.json",
        _row("english-name", text="hello there", language="English"),
        _row("german-name", text="the second row", language="German"),
        _row("iso-de", text="the third row", language="de"),
        _row("iso-en", text="good morning", language="en"),
        _row("nothing", text="ok", language=""),
    )
    assert {e.id: e.language for e in store.list_all()} == {
        "english-name": "en",
        "german-name": "de",
        "iso-de": "de",
        "iso-en": "en",
        "nothing": "",
    }
    # ...and the collapse is durable, so the drift cannot come back on the next read.
    fresh = store.add(raw_text="later", text="later", language="German")
    assert fresh is not None
    on_disk = {row["id"]: row["language"] for row in _stored(store)}
    assert on_disk == {
        "english-name": "en",
        "german-name": "de",
        "iso-de": "de",
        "iso-en": "en",
        "nothing": "",
        fresh.id: "de",
    }


def test_a_legacy_non_product_language_survives_the_read_heal(tmp_path: Path) -> None:
    store = _legacy_history(
        tmp_path / "jp.json", _row("a", text="konnichiwa", language="Japanese")
    )
    assert store.list_all()[0].language == "japanese"


def test_update_normalizes_the_language_the_restore_route_writes(
    history: DictationHistory,
) -> None:
    """Re-transcribing writes a provider tag straight back into the row."""
    entry = history.add(raw_text="", text="", outcome="failed")
    assert entry is not None
    updated = history.update(entry.id, text="recovered text", language="German")
    assert updated is not None
    assert updated.language == "de"
    assert history.get(entry.id).language == "de"  # type: ignore[union-attr]


def test_update_can_clear_the_language_without_inventing_one(
    history: DictationHistory,
) -> None:
    entry = history.add(raw_text="x", text="x", language="German")
    assert entry is not None
    updated = history.update(entry.id, language="unknown")
    assert updated is not None
    assert updated.language == ""


def test_the_normalized_language_reaches_the_api_shape(
    history: DictationHistory,
) -> None:
    entry = history.add(raw_text="x", text="x", language="English")
    assert entry is not None
    assert entry.to_dict()["language"] == "en"


def test_normalizing_the_language_leaves_every_other_field_alone(
    tmp_path: Path,
) -> None:
    """A read-time repair must not become a read-time rewrite."""
    store = _legacy_history(
        tmp_path / "intact.json",
        _row(
            "a",
            raw_text="uh one two three",
            text="one two three",
            language="English",
            duration_s=4.25,
            outcome="inserted",
            method="clipboard+ctrl_v",
            removed_words=1,
            cleanup_reason="",
            discarded=False,
        ),
    )
    entry = store.list_all()[0]
    assert entry.raw_text == "uh one two three"
    assert entry.text == "one two three"
    assert entry.duration_s == 4.25
    assert entry.outcome == "inserted"
    assert entry.method == "clipboard+ctrl_v"
    assert entry.removed_words == 1
    assert entry.discarded is False
