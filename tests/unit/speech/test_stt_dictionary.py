"""STT dictionary — store CRUD, transcript correction, provider wrapper.

The dictionary is the Wispr-Flow-style custom-vocabulary feature: plain words
canonicalize casing and repair conservative near-misses; explicit misheard
pairs rewrite known misrecognitions. Corrections are pure string ops on the
voice hot path, so these tests also pin the false-positive guards that keep
ordinary words from being rewritten.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from jarvis.speech import stt_dictionary as sd
from jarvis.speech.stt_dictionary import (
    DictionaryCorrectingSTT,
    DictionaryStore,
    TranscriptCorrector,
    dictionary_bias_words,
    get_corrector,
    wrap_stt_with_dictionary,
)


@pytest.fixture()
def store(tmp_path: Path) -> DictionaryStore:
    return DictionaryStore(tmp_path / "stt_dictionary.json")


def corrector_for(*entries: tuple[str, list[str]]) -> TranscriptCorrector:
    built = [
        sd.DictionaryEntry(id=str(i), word=word, misheard=tuple(misheard))
        for i, (word, misheard) in enumerate(entries)
    ]
    return TranscriptCorrector(built)


# ----------------------------------------------------------------------
# Store CRUD
# ----------------------------------------------------------------------


class TestStore:
    def test_add_list_roundtrip(self, store: DictionaryStore) -> None:
        entry = store.add("GitHub", ["Gitter", "Git Hub"])
        assert entry.id
        listed = store.list_all()
        assert [e.word for e in listed] == ["GitHub"]
        assert listed[0].misheard == ("Gitter", "Git Hub")
        assert listed[0].created_at and listed[0].updated_at

    def test_add_rejects_empty_and_duplicate(self, store: DictionaryStore) -> None:
        with pytest.raises(ValueError):
            store.add("   ")
        store.add("Anthropic")
        with pytest.raises(ValueError):
            store.add("anthropic")  # duplicate is case-insensitive

    def test_misheard_variants_are_deduped_and_word_dropped(
        self, store: DictionaryStore
    ) -> None:
        entry = store.add("GitHub", ["Gitter", "gitter", "GitHub", "  "])
        assert entry.misheard == ("Gitter",)

    def test_update_word_and_misheard(self, store: DictionaryStore) -> None:
        entry = store.add("Ultrathink", ["UltraSync"])
        updated = store.update(
            entry.id, misheard=["UltraSync", "Ultra-think"], misheard_set=True
        )
        assert updated is not None
        assert updated.misheard == ("UltraSync", "Ultra-think")
        renamed = store.update(entry.id, word="Ultrathink 2")
        assert renamed is not None and renamed.word == "Ultrathink 2"

    def test_update_rejects_collision_and_unknown_id(
        self, store: DictionaryStore
    ) -> None:
        a = store.add("Fable")
        store.add("Opus")
        with pytest.raises(ValueError):
            store.update(a.id, word="opus")
        assert store.update("nope", word="X") is None

    def test_delete_is_idempotent(self, store: DictionaryStore) -> None:
        entry = store.add("Claude.md")
        assert store.delete(entry.id) is True
        assert store.delete(entry.id) is False
        assert store.list_all() == []

    def test_corrupt_sidecar_treated_as_empty(self, store: DictionaryStore) -> None:
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text("{not json", encoding="utf-8")
        assert store.list_all() == []
        # And the store recovers by writing a fresh file.
        store.add("GitHub")
        assert [e.word for e in store.list_all()] == ["GitHub"]

    def test_write_is_atomic_no_temp_left_behind(
        self, store: DictionaryStore
    ) -> None:
        store.add("GitHub")
        leftovers = [
            p for p in store.path.parent.iterdir() if p.suffix == ".tmp"
        ]
        assert leftovers == []
        payload = json.loads(store.path.read_text(encoding="utf-8"))
        assert payload["version"] == 1


# ----------------------------------------------------------------------
# Corrector — explicit pairs
# ----------------------------------------------------------------------


class TestExplicitReplacements:
    def test_basic_pair(self) -> None:
        c = corrector_for(("GitHub", ["Gitter"]))
        assert c.correct("please open Gitter now") == "please open GitHub now"

    def test_case_insensitive_source(self) -> None:
        c = corrector_for(("GitHub", ["git hub"]))
        assert c.correct("Open Git Hub now") == "Open GitHub now"

    def test_word_boundary_no_partial_hit(self) -> None:
        # German compounds are the classic boundary hazard: "Gittertor"
        # contains the misheard source "Gitter" but must stay untouched.
        c = corrector_for(("GitHub", ["Gitter"]))
        assert c.correct("Das Gittertor bleibt zu") == "Das Gittertor bleibt zu"  # i18n-allow: German compound under test

    def test_multiword_and_flexible_whitespace(self) -> None:
        c = corrector_for(("PersonalJarvi", ["personal  jarvi"]))
        assert c.correct("start personal jarvi now") == "start PersonalJarvi now"

    def test_hyphen_and_dot_sources(self) -> None:
        c = corrector_for(("Ultrathink", ["Ultra-think"]), ("Claude.md", ["cloud md"]))
        assert c.correct("use Ultra-think and cloud md") == "use Ultrathink and Claude.md"

    def test_longest_source_wins(self) -> None:
        c = corrector_for(("Ultrathink", ["Ultra"]), ("Ultrathink Pro", ["Ultra Sync"]))
        assert c.correct("enable Ultra Sync") == "enable Ultrathink Pro"

    def test_idempotent(self) -> None:
        c = corrector_for(("GitHub", ["Gitter"]))
        once = c.correct("opening Gitter")
        assert c.correct(once) == once


# ----------------------------------------------------------------------
# Corrector — plain vocabulary words
# ----------------------------------------------------------------------


class TestVocabularyWords:
    def test_casing_canonicalized(self) -> None:
        c = corrector_for(("GitHub", []))
        assert c.correct("open github and GITHUB") == "open GitHub and GitHub"

    def test_fuzzy_repairs_near_miss(self) -> None:
        c = corrector_for(("Aethroc", []))
        assert c.correct("write to Aethrok") == "write to Aethroc"

    def test_fuzzy_requires_same_first_letter(self) -> None:
        # "table" is distance 2 from "Fable" but starts differently — untouched.
        c = corrector_for(("Fable", []))
        assert c.correct("put it on the table") == "put it on the table"

    def test_fuzzy_leaves_distant_words_alone(self) -> None:
        c = corrector_for(("Fable", []))
        # "Farbe" (a common German word, the real-world STT garble of "Fable"
        # from the AP-27 forensics) is distance 2 at length 5 — beyond the
        # ≤7-char budget of 1, so it is NOT auto-rewritten (explicit pairs are
        # the tool for that).
        assert c.correct("welche Farbe hat das") == "welche Farbe hat das"  # i18n-allow: German STT-garble token under test

    def test_fuzzy_ambiguous_between_entries_is_left_alone(self) -> None:
        # "Nicko" is within distance 1 of BOTH entries — ambiguous, so the
        # corrector must not guess.
        c = corrector_for(("Nico", []), ("Niko", []))
        assert c.correct("call Nicko today") == "call Nicko today"

    def test_multiword_casing_with_umlaut(self) -> None:
        # The umlaut pins Unicode word-boundary handling.
        c = corrector_for(("Nico Maintainer", []))  # i18n-allow: umlauted proper name under test
        assert c.correct("write to nico maintainer") == "write to Nico Maintainer"  # i18n-allow: umlauted proper name under test

    def test_email_entry_matches_boundary(self) -> None:
        c = corrector_for(("aethroc@gmail.com", []))
        assert (
            c.correct("send it to Aethroc@Gmail.com please")
            == "send it to aethroc@gmail.com please"
        )

    def test_empty_dictionary_is_noop(self) -> None:
        c = corrector_for()
        assert c.correct("nothing to do here") == "nothing to do here"


# ----------------------------------------------------------------------
# Live-reloading shared corrector + bias words
# ----------------------------------------------------------------------


class TestSharedCorrector:
    def test_reloads_after_store_write(
        self, store: DictionaryStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Collapse the stat-throttle so the test does not sleep.
        monkeypatch.setattr(sd, "_RELOAD_CHECK_INTERVAL_S", 0.0)
        assert get_corrector(store).correct("Gitter") == "Gitter"
        store.add("GitHub", ["Gitter"])
        assert get_corrector(store).correct("Gitter") == "GitHub"

    def test_bias_words_capped_and_canonical_only(
        self, store: DictionaryStore
    ) -> None:
        store.add("GitHub", ["Gitter"])
        store.add("Anthropic")
        words = dictionary_bias_words(store)
        assert words == ["GitHub", "Anthropic"]
        assert "Gitter" not in words


# ----------------------------------------------------------------------
# Provider wrapper
# ----------------------------------------------------------------------


@dataclass
class _FakeTranscript:
    text: str
    language: str = "de"
    confidence: float = 0.9
    is_partial: bool = False
    segments: tuple = field(default_factory=tuple)


class _FakeSTT:
    """Duck-typed STT provider fake (tests/fakes convention, no mock lib)."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.is_warm = True
        self.recover_calls = 0

    async def transcribe_pcm(
        self, pcm: bytes, sample_rate: int = 16_000, **kwargs: Any
    ) -> _FakeTranscript:
        return _FakeTranscript(text=self.text)

    async def transcribe(self, audio: Any) -> _FakeTranscript:
        return _FakeTranscript(text=self.text)

    async def stream_transcribe(self, audio: Any):
        yield _FakeTranscript(text=self.text, is_partial=True)
        yield _FakeTranscript(text=self.text)

    def recover(self) -> None:
        self.recover_calls += 1


class TestWrapper:
    @pytest.fixture()
    def wrapped(
        self, store: DictionaryStore, monkeypatch: pytest.MonkeyPatch
    ) -> DictionaryCorrectingSTT:
        monkeypatch.setattr(sd, "_RELOAD_CHECK_INTERVAL_S", 0.0)
        store.add("GitHub", ["Gitter"])
        return DictionaryCorrectingSTT(_FakeSTT("open Gitter"), store=store)

    async def test_transcribe_pcm_corrected(
        self, wrapped: DictionaryCorrectingSTT
    ) -> None:
        result = await wrapped.transcribe_pcm(b"\x00\x00")
        assert result.text == "open GitHub"
        assert result.language == "de"  # other fields untouched

    async def test_stream_transcribe_corrects_each_item(
        self, wrapped: DictionaryCorrectingSTT
    ) -> None:
        texts = [t.text async for t in wrapped.stream_transcribe(None)]
        assert texts == ["open GitHub", "open GitHub"]

    async def test_empty_text_passthrough(
        self, store: DictionaryStore
    ) -> None:
        wrapped = DictionaryCorrectingSTT(_FakeSTT(""), store=store)
        result = await wrapped.transcribe_pcm(b"")
        assert result.text == ""

    def test_delegates_unknown_attributes(
        self, wrapped: DictionaryCorrectingSTT
    ) -> None:
        assert wrapped.is_warm is True
        wrapped.recover()
        assert wrapped._inner.recover_calls == 1
        assert wrapped.provider_label == "_FakeSTT"

    def test_wrap_helper_guards(self, store: DictionaryStore) -> None:
        assert wrap_stt_with_dictionary(None) is None
        once = wrap_stt_with_dictionary(_FakeSTT("x"))
        assert wrap_stt_with_dictionary(once) is once

    async def test_frozen_dataclass_transcript_supported(
        self, store: DictionaryStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from jarvis.core.protocols import Transcript

        monkeypatch.setattr(sd, "_RELOAD_CHECK_INTERVAL_S", 0.0)
        store.add("GitHub", ["Gitter"])

        class _FrozenSTT:
            async def transcribe_pcm(self, pcm: bytes, **kwargs: Any) -> Transcript:
                return Transcript(text="Gitter auf", language="de", confidence=1.0)

        wrapped = DictionaryCorrectingSTT(_FrozenSTT(), store=store)
        result = await wrapped.transcribe_pcm(b"")
        assert result.text == "GitHub auf"
        assert result.language == "de"
