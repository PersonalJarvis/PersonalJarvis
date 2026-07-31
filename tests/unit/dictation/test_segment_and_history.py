"""Segment cutting and the local dictation history.

The cut matters because a closed segment is transcribed ONCE and never
revisited — a cut through the middle of a word is permanent damage, which is
why it lands at the quietest point near the nominal length rather than exactly
on it.
"""
from __future__ import annotations

import threading
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


def _missing_wav() -> Path:
    """A path that is guaranteed not to exist, for the availability check."""
    return Path("definitely") / "not" / "here.wav"


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
    # i18n-allow: German fixture under test (§1 list #4)
    assert stored[0].raw_text == "Ähm das ist gut"  # i18n-allow
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
# The fields added for restore/statistics
# --------------------------------------------------------------------------


def test_word_count_is_derived_from_the_inserted_text(
    history: DictationHistory,
) -> None:
    entry = history.add(raw_text="uh send the report", text="send the report")
    assert entry is not None
    assert entry.word_count == 3


def test_an_explicit_word_count_wins_over_the_derived_one(
    history: DictationHistory,
) -> None:
    entry = history.add(raw_text="a b c", text="a b c", word_count=99)
    assert entry is not None
    assert entry.word_count == 99


def test_a_failed_dictation_with_no_text_is_still_recorded(
    history: DictationHistory,
) -> None:
    """The worst failure must stop being the most invisible one."""
    entry = history.add(raw_text="", text="", outcome="failed", error="provider 401")
    assert entry is not None
    stored = history.list_all()
    assert [e.outcome for e in stored] == ["failed"]
    assert stored[0].error == "provider 401"
    assert stored[0].word_count == 0


def test_an_unrecoverable_outcome_with_no_text_stays_a_no_op(
    history: DictationHistory,
) -> None:
    assert history.add(raw_text="", text="", outcome="inserted") is None


def test_get_returns_one_entry_or_none(history: DictationHistory) -> None:
    entry = history.add(raw_text="a", text="a")
    assert entry is not None
    assert history.get(entry.id) is not None
    assert history.get("no-such-id") is None


def test_set_discarded_hides_the_entry_without_deleting_it(
    history: DictationHistory,
) -> None:
    entry = history.add(raw_text="a", text="a")
    assert entry is not None
    assert history.set_discarded(entry.id) is not None
    assert history.list_all(include_discarded=False) == []
    assert len(history.list_all()) == 1
    # ...and it comes back, which is the whole point of a soft delete.
    history.set_discarded(entry.id, False)
    assert len(history.list_all(include_discarded=False)) == 1


def test_set_discarded_on_an_unknown_id_is_none(history: DictationHistory) -> None:
    assert history.set_discarded("nope") is None


def test_update_ignores_unknown_fields_instead_of_raising(
    history: DictationHistory, tmp_path: Path
) -> None:
    sidecar = str(tmp_path / "x.wav")
    entry = history.add(raw_text="a", text="a")
    assert entry is not None
    assert history.update(entry.id, not_a_field="x") is None
    updated = history.update(entry.id, audio_path=sidecar, error="boom")
    assert updated is not None
    assert updated.audio_path == sidecar
    assert updated.error == "boom"


def test_update_never_rewrites_the_id_or_timestamp(history: DictationHistory) -> None:
    entry = history.add(raw_text="a", text="a")
    assert entry is not None
    assert history.update(entry.id, id="hijacked", created_at="1999") is None
    assert history.get(entry.id) is not None


def test_history_written_before_the_new_fields_reads_as_defaults(
    tmp_path: Path,
) -> None:
    """An install that predates word_count/discarded/audio/error still loads.

    ``word_count`` is deliberately the one field that does NOT read back as its
    default (F9): a zero on a row that plainly has text is indistinguishable
    from a measured "this dictation had no words", and the lifetime counters
    skip anything at or below zero — so the legacy row is repaired on read
    instead of persisting its zero forever. The other three stay defaults,
    because for them the default IS the honest answer for an old row.
    """
    path = tmp_path / "old.json"
    path.write_text(
        '{"version": 1, "entries": ['
        '{"id": "a", "created_at": "2026-07-28T00:00:00+00:00",'
        ' "raw_text": "hi", "text": "hi", "outcome": "inserted"}'
        "]}",
        encoding="utf-8",
    )
    entry = DictationHistory(path).list_all()[0]
    assert entry.word_count == 1
    assert entry.discarded is False
    assert entry.audio_path is None
    assert entry.error is None


def test_a_legacy_row_with_no_text_keeps_its_zero_word_count(
    tmp_path: Path,
) -> None:
    """The other half of the F9 self-heal: a failed dictation really did produce
    no words, so inventing one would break the counters in the other direction.
    """
    path = tmp_path / "old-empty.json"
    path.write_text(
        '{"version": 1, "entries": ['
        '{"id": "a", "created_at": "2026-07-28T00:00:00+00:00",'
        ' "raw_text": "", "text": "", "outcome": "failed"}'
        "]}",
        encoding="utf-8",
    )
    assert DictationHistory(path).list_all()[0].word_count == 0


def test_public_dict_never_leaks_the_audio_path(history: DictationHistory) -> None:
    entry = history.add(raw_text="a", text="a")
    assert entry is not None
    history.update(entry.id, audio_path=str(_missing_wav()))
    stored = history.get(entry.id)
    assert stored is not None
    payload = stored.to_dict()
    assert "audio_path" not in payload
    assert payload["audio_available"] is False
    assert set(payload) == {
        "id", "created_at", "raw_text", "text", "language", "duration_s",
        "outcome", "method", "removed_words", "cleanup_reason", "word_count",
        "discarded", "audio_available", "error",
    }


def test_public_dict_reports_audio_that_is_actually_on_disk(
    history: DictationHistory, tmp_path: Path
) -> None:
    from jarvis.dictation.audio import save_dictation_audio

    entry = history.add(raw_text="", text="", outcome="failed")
    assert entry is not None
    path = save_dictation_audio(entry.id, b"\x00\x01" * 800, directory=tmp_path / "a")
    assert path is not None
    history.update(entry.id, audio_path=str(path))
    stored = history.get(entry.id)
    assert stored is not None
    assert stored.to_dict()["audio_available"] is True


def test_public_dict_exposes_only_safe_quality_telemetry(
    history: DictationHistory,
) -> None:
    entry = history.add(
        raw_text="hello",
        text="hello",
        stt_providers=("openai-api",),
        stt_models=("gpt-4o-transcribe",),
        detected_languages=("en", "ja"),
        stt_latency_ms=123,
        stt_calls=2,
        stt_errors=("rate_limited",),
        stt_audit=("final_pass:applied",),
        audio_sample_rate_hz=16_000,
        audio_rms=0.0125,
        audio_clipping_ratio=0.001,
        audio_dropouts=2,
        audio_dropout_ms=64,
        internal_debug_path="private/path",
    )
    assert entry is not None

    payload = entry.to_dict()

    assert payload["stt_providers"] == ("openai-api",)
    assert payload["stt_models"] == ("gpt-4o-transcribe",)
    assert payload["detected_languages"] == ("en", "ja")
    assert payload["stt_latency_ms"] == 123
    assert payload["stt_calls"] == 2
    assert payload["stt_errors"] == ("rate_limited",)
    assert payload["stt_audit"] == ("final_pass:applied",)
    assert payload["audio_sample_rate_hz"] == 16_000
    assert payload["audio_rms"] == 0.0125
    assert payload["audio_clipping_ratio"] == 0.001
    assert payload["audio_dropouts"] == 2
    assert payload["audio_dropout_ms"] == 64
    assert "internal_debug_path" not in payload


def test_delete_also_removes_the_audio_sidecar(
    history: DictationHistory, tmp_path: Path
) -> None:
    from jarvis.dictation.audio import save_dictation_audio

    entry = history.add(raw_text="", text="", outcome="failed")
    assert entry is not None
    path = save_dictation_audio(entry.id, b"\x00\x01" * 800, directory=tmp_path / "a")
    assert path is not None
    history.update(entry.id, audio_path=str(path))
    assert history.delete(entry.id) is True
    assert path.exists() is False


def test_clear_purges_history_audio_and_statistics(
    history: DictationHistory, tmp_path: Path
) -> None:
    from jarvis.dictation.audio import save_dictation_audio

    history.add(raw_text="a b c", text="a b c")
    failed = history.add(raw_text="", text="", outcome="failed")
    assert failed is not None
    path = save_dictation_audio(
        failed.id, b"\x00\x01" * 800, directory=history.audio_dir
    )
    assert path is not None
    history.update(failed.id, audio_path=str(path))
    assert history.stats().summary()["totals"]["words"] == 3

    assert history.clear() is True
    assert history.list_all() == []
    assert path.exists() is False
    assert history.stats().summary()["totals"]["words"] == 0
    assert history.stats().summary()["streak"]["current_days"] == 0


def test_statistics_sidecar_sits_next_to_the_history_file(
    history: DictationHistory,
) -> None:
    """A history in a temp directory must not write into the real user data."""
    assert history.stats_path.parent == history.path.parent
    assert history.stats_path.name == "dictation_stats.json"
    assert history.audio_dir.parent == history.path.parent


def test_add_feeds_the_lifetime_counters(history: DictationHistory) -> None:
    history.add(raw_text="one two three", text="one two three", duration_s=6.0)
    history.add(raw_text="four five", text="four five", duration_s=3.0)
    summary = history.stats().summary()
    assert summary["source"] == "lifetime"
    assert summary["totals"]["dictations"] == 2
    assert summary["totals"]["words"] == 5
    assert summary["today"]["words"] == 5
    assert summary["streak"]["current_days"] == 1


def test_a_failed_dictation_does_not_move_the_words_per_minute(
    history: DictationHistory,
) -> None:
    history.add(raw_text="one two three", text="one two three", duration_s=60.0)
    history.add(raw_text="", text="", outcome="failed", duration_s=60.0)
    totals = history.stats().summary()["totals"]
    assert totals["dictations"] == 1
    assert totals["wpm"] == 3.0


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


# --------------------------------------------------------------------------
# Pruning must not strand a pending Restore
# --------------------------------------------------------------------------


def _recoverable(tmp_path: Path, name: str, *, discarded: bool = False):
    """An entry that ended badly and whose audio is really on disk."""
    from jarvis.dictation.audio import save_dictation_audio
    from jarvis.dictation.history import DictationEntry

    path = save_dictation_audio(name, b"\x00\x01" * 800, directory=tmp_path)
    assert path is not None
    return DictationEntry(
        id=name,
        created_at=datetime.now(UTC).isoformat(),
        raw_text="",
        text="",
        outcome="failed",
        discarded=discarded,
        audio_path=str(path),
    )


def test_count_cap_never_evicts_an_entry_whose_audio_a_restore_still_needs(
    tmp_path: Path,
) -> None:
    """Otherwise the row vanishes while the recording stays on disk."""
    pending = _recoverable(tmp_path, "pending")
    ordinary = [_entry(0.1, f"x{i}") for i in range(5)]
    kept = _prune([*ordinary, pending], max_entries=2, retention_days=0)
    assert pending in kept
    assert len([e for e in kept if e.audio_path is None]) == 2


def test_a_discarded_entry_with_audio_survives_the_cap_too(tmp_path: Path) -> None:
    pending = _recoverable(tmp_path, "discarded-one", discarded=True)
    kept = _prune([_entry(0.1, "a"), pending], max_entries=1, retention_days=0)
    assert pending in kept


def test_the_exemption_needs_the_audio_to_actually_exist(tmp_path: Path) -> None:
    """A stored path whose file is gone is not a pending recovery."""
    from jarvis.dictation.history import DictationEntry

    ghost = DictationEntry(
        id="ghost",
        created_at=datetime.now(UTC).isoformat(),
        raw_text="",
        text="",
        outcome="failed",
        audio_path=str(tmp_path / "never-written.wav"),
    )
    kept = _prune([_entry(0.1, "a"), ghost], max_entries=1, retention_days=0)
    assert ghost not in kept


def test_a_successful_entry_with_audio_is_not_exempt(tmp_path: Path) -> None:
    """Only outcomes the user lost something to hold a Restore open."""
    from jarvis.dictation.audio import save_dictation_audio
    from jarvis.dictation.history import DictationEntry

    path = save_dictation_audio("ok", b"\x00\x01" * 800, directory=tmp_path)
    assert path is not None
    fine = DictationEntry(
        id="ok",
        created_at=datetime.now(UTC).isoformat(),
        raw_text="hi",
        text="hi",
        outcome="inserted",
        audio_path=str(path),
    )
    kept = _prune([_entry(0.1, "a"), fine], max_entries=1, retention_days=0)
    assert fine not in kept


def test_the_retention_window_does_not_orphan_a_recoverable_recording(
    tmp_path: Path,
) -> None:
    """The two retention keys are independent, so the row follows the audio.

    ``history_retention_days`` and ``audio_retention_days`` are separate
    settings with no cross-field validation, so "1 day of history, a year of
    audio" is a legal configuration. If the time cutoff dropped the row anyway,
    the WAV would still be on disk with nothing left pointing at it: audio the
    user can neither restore from nor find.
    """
    from jarvis.dictation.audio import save_dictation_audio
    from jarvis.dictation.history import DictationEntry

    path = save_dictation_audio("old", b"\x00\x01" * 800, directory=tmp_path)
    assert path is not None
    stale = DictationEntry(
        id="old",
        created_at=(datetime.now(UTC) - timedelta(days=40)).isoformat(),
        raw_text="",
        text="",
        outcome="failed",
        audio_path=str(path),
    )
    # history_retention_days = 1 against an audio window measured in months.
    assert _prune([stale], max_entries=100, retention_days=1) == [stale]
    assert path.is_file()


def test_the_row_ages_out_once_the_audio_window_has_taken_the_file(
    tmp_path: Path,
) -> None:
    """The exemption ends with the recording, which is what bounds it."""
    from jarvis.dictation.audio import prune_audio, save_dictation_audio
    from jarvis.dictation.history import DictationEntry

    path = save_dictation_audio("old", b"\x00\x01" * 800, directory=tmp_path)
    assert path is not None
    stale = DictationEntry(
        id="old",
        created_at=(datetime.now(UTC) - timedelta(days=40)).isoformat(),
        raw_text="",
        text="",
        outcome="failed",
        audio_path=str(path),
    )
    assert prune_audio(max_files=0, retention_days=0, directory=tmp_path) == 0
    path.unlink()  # what the audio retention would have done on its schedule
    assert _prune([stale], max_entries=100, retention_days=30) == []


def test_an_expired_entry_without_audio_still_ages_out(tmp_path: Path) -> None:
    """The exemption is about a stranded recording, not about failing at all."""
    from jarvis.dictation.history import DictationEntry

    stale = DictationEntry(
        id="old",
        created_at=(datetime.now(UTC) - timedelta(days=40)).isoformat(),
        raw_text="",
        text="",
        outcome="failed",
    )
    assert _prune([stale], max_entries=100, retention_days=30) == []


def test_add_keeps_a_recoverable_row_a_short_history_window_would_drop(
    tmp_path: Path,
) -> None:
    """The same configuration, driven through the real write path."""
    from jarvis.dictation.audio import save_dictation_audio

    store = DictationHistory(tmp_path / "dictation_history.json")
    entry = store.add(raw_text="", text="", outcome="failed", retention_days=1)
    assert entry is not None
    wav = save_dictation_audio(entry.id, b"\x00\x01" * 800, directory=store.audio_dir)
    assert wav is not None
    assert store.update(entry.id, audio_path=str(wav)) is not None

    # Backdate it well past the one-day history window, then write again so the
    # prune runs over it.
    _backdate(store, entry.id, days=30)
    store.add(raw_text="later", text="later", retention_days=1)

    kept = {e.id for e in store.list_all()}
    assert entry.id in kept
    assert wav.is_file()


def _backdate(store: DictationHistory, entry_id: str, *, days: float) -> None:
    """Rewrite one entry's timestamp on disk. ``created_at`` is immutable."""
    import json

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    when = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    for item in payload["entries"]:
        if item.get("id") == entry_id:
            item["created_at"] = when
    store.path.write_text(json.dumps(payload), encoding="utf-8")


# --------------------------------------------------------------------------
# The write lock belongs to the path, not to the instance
# --------------------------------------------------------------------------


def test_two_stores_at_one_path_share_one_lock(tmp_path: Path) -> None:
    """Nothing keeps an instance alive, so a per-object lock guards nothing.

    Every caller builds a fresh store per operation, and two spellings of the
    same file have to end up on the same lock or the read-modify-write cycle
    is unguarded.
    """
    nested = tmp_path / "sub"
    nested.mkdir()
    direct = DictationHistory(tmp_path / "dictation_history.json")
    roundabout = DictationHistory(nested / ".." / "dictation_history.json")
    assert direct._lock is roundabout._lock
    assert direct.stats()._lock is roundabout.stats()._lock


def test_two_stores_at_different_paths_do_not_share_a_lock(tmp_path: Path) -> None:
    """Two profiles must not serialise against each other for no reason."""
    one = DictationHistory(tmp_path / "one.json")
    two = DictationHistory(tmp_path / "two.json")
    assert one._lock is not two._lock


def test_a_restore_is_not_clobbered_by_a_dictation_finishing(tmp_path: Path) -> None:
    """The race the REST endpoints introduced: two writers, one file.

    A restore (read, change one row, write it all back) overlapping a
    dictation being recorded used to end with whichever ``os.replace`` landed
    last: the file stayed readable, one of the two updates just quietly
    vanished. Both must survive.
    """
    path = tmp_path / "dictation_history.json"
    seed = DictationHistory(path).add(raw_text="", text="", outcome="failed")
    assert seed is not None

    rounds = 30
    start = threading.Barrier(2, timeout=15)
    errors: list[Exception] = []

    def restoring() -> None:
        try:
            start.wait()
            for i in range(rounds):
                assert DictationHistory(path).update(seed.id, text=f"restored-{i}")
        except Exception as exc:
            errors.append(exc)

    def dictating() -> None:
        try:
            start.wait()
            for i in range(rounds):
                assert DictationHistory(path).add(raw_text=f"n{i}", text=f"n{i}")
        except Exception as exc:
            errors.append(exc)

    workers = [threading.Thread(target=restoring), threading.Thread(target=dictating)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=60)
        assert not worker.is_alive()
    assert not errors, errors

    entries = DictationHistory(path).list_all()
    assert len([e for e in entries if e.text.startswith("n")]) == rounds
    restored = [e for e in entries if e.id == seed.id]
    assert [e.text for e in restored] == [f"restored-{rounds - 1}"]
