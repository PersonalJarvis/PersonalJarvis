"""User dictionary for speech-to-text — custom vocabulary + misrecognition fixes.

Wispr-Flow-style feature: the user registers words the STT keeps getting wrong
(proper nouns, brand names, e-mail addresses). An entry is one canonical
``word`` plus optional ``misheard`` variants:

- ``misheard`` empty → plain vocabulary word. The corrector canonicalizes the
  casing of exact (case-insensitive) hits and repairs conservative near-misses
  of single tokens ("Veltrok" → "Veltroc").
- ``misheard`` non-empty → explicit replacement pairs ("Gitter" → "GitHub"),
  word-boundary + case-insensitive, multi-word capable.

Design constraints (see the plan file and CLAUDE.md):

- Pure string ops — regex + a bounded edit distance. NO LLM call, NO network:
  this runs on the voice hot path for every utterance (AP-11 doctrine).
- Provider-agnostic: :class:`DictionaryCorrectingSTT` wraps ANY STTProvider,
  so every provider (local faster-whisper, Groq, OpenRouter, future ones)
  benefits identically (AP-21/22 — never pin a feature to one provider).
- Storage is a JSON sidecar under ``user_data_dir()/data/`` (pattern:
  ``skill_prefs.json``), written atomically. Deliberately NOT ``jarvis.toml``:
  a growing user list does not belong in the three-way config sync and stays
  clear of parallel-session config drift (BUG-010).
- Live reload: the compiled corrector is rebuilt when the sidecar changes on
  disk, so REST edits apply on the next utterance without a restart.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jarvis.core.paths import user_data_dir

log = logging.getLogger(__name__)

# Abuse guards, not product limits — generous enough for heavy real use.
MAX_ENTRIES = 2_000
MAX_WORD_LEN = 100
MAX_MISHEARD_PER_ENTRY = 20

# Conservative fuzzy-repair gates (plain vocabulary words only). A token is
# rewritten toward a dictionary word only when ALL hold: same first letter,
# minimum length, and edit distance within the length-scaled budget below.
# This keeps "table" from becoming "Fable" while still fixing "Veltrok".
_FUZZY_MIN_TOKEN_LEN = 4
_FUZZY_DISTANCE_BUDGET = ((8, 2), (_FUZZY_MIN_TOKEN_LEN, 1))

# Re-stat the sidecar at most this often — the corrector is consulted per
# STT call (including the live-preview probe at a few calls/second).
_RELOAD_CHECK_INTERVAL_S = 1.0

# Cap for the decoder-bias word list handed to prompt-capable cloud STT
# providers (Groq trims its whisper prompt to 1024 chars; leave headroom for
# the user's own [stt].bias_prompt).
_BIAS_WORDS_CHAR_CAP = 700


def stt_dictionary_path() -> Path:
    """JSON sidecar holding the user's STT dictionary entries."""
    return user_data_dir() / "data" / "stt_dictionary.json"


# ----------------------------------------------------------------------
# Data model + store
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DictionaryEntry:
    """One canonical word plus the misheard variants that map onto it."""

    id: str
    word: str
    misheard: tuple[str, ...] = ()
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "word": self.word,
            "misheard": list(self.misheard),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _clean_word(raw: str) -> str:
    word = " ".join((raw or "").split())
    if not word:
        raise ValueError("Word must not be empty.")
    if len(word) > MAX_WORD_LEN:
        raise ValueError(f"Word is too long (max {MAX_WORD_LEN} characters).")
    return word


def _clean_misheard(raw: Any, word: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ValueError("misheard must be a list of strings.")
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        variant = " ".join(str(item or "").split())
        if not variant:
            continue
        if len(variant) > MAX_WORD_LEN:
            raise ValueError(
                f"Misheard variant is too long (max {MAX_WORD_LEN} characters)."
            )
        key = variant.casefold()
        # A variant equal to the word itself is a no-op rule; drop silently.
        if key == word.casefold() or key in seen:
            continue
        seen.add(key)
        out.append(variant)
    if len(out) > MAX_MISHEARD_PER_ENTRY:
        raise ValueError(
            f"Too many misheard variants (max {MAX_MISHEARD_PER_ENTRY})."
        )
    return tuple(out)


class DictionaryStore:
    """CRUD over the JSON sidecar, atomic writes, corrupt-file tolerant.

    Instances are cheap (path resolution at call time so test sandboxes that
    move ``LOCALAPPDATA`` work); cross-instance write races are serialized by
    the REST layer's lock. Readers (:func:`get_corrector`) tolerate torn
    states by simply reloading on the next mtime tick.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or stt_dictionary_path()

    @property
    def path(self) -> Path:
        return self._path

    # -- read ----------------------------------------------------------

    def list_all(self) -> list[DictionaryEntry]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except Exception as exc:  # noqa: BLE001 — a corrupt sidecar must never crash voice
            log.warning("STT dictionary unreadable (%s); treating as empty.", exc)
            return []
        entries: list[DictionaryEntry] = []
        for item in raw.get("entries", []) if isinstance(raw, dict) else []:
            try:
                word = _clean_word(str(item.get("word", "")))
                entries.append(
                    DictionaryEntry(
                        id=str(item.get("id") or uuid.uuid4().hex[:12]),
                        word=word,
                        misheard=_clean_misheard(item.get("misheard"), word),
                        created_at=str(item.get("created_at", "")),
                        updated_at=str(item.get("updated_at", "")),
                    )
                )
            except ValueError:
                continue  # skip malformed rows, keep the rest usable
        return entries

    def get(self, entry_id: str) -> DictionaryEntry | None:
        for entry in self.list_all():
            if entry.id == entry_id:
                return entry
        return None

    # -- write ---------------------------------------------------------

    def add(self, word: str, misheard: Any = None) -> DictionaryEntry:
        word = _clean_word(word)
        entries = self.list_all()
        if len(entries) >= MAX_ENTRIES:
            raise ValueError(f"Dictionary is full (max {MAX_ENTRIES} entries).")
        if any(e.word.casefold() == word.casefold() for e in entries):
            raise ValueError(f"'{word}' is already in the dictionary.")
        now = _now_iso()
        entry = DictionaryEntry(
            id=uuid.uuid4().hex[:12],
            word=word,
            misheard=_clean_misheard(misheard, word),
            created_at=now,
            updated_at=now,
        )
        self._write(entries + [entry])
        return entry

    def update(
        self,
        entry_id: str,
        *,
        word: str | None = None,
        misheard: Any = None,
        misheard_set: bool = False,
    ) -> DictionaryEntry | None:
        entries = self.list_all()
        for i, entry in enumerate(entries):
            if entry.id != entry_id:
                continue
            new_word = _clean_word(word) if word is not None else entry.word
            if word is not None and any(
                e.word.casefold() == new_word.casefold()
                for j, e in enumerate(entries)
                if j != i
            ):
                raise ValueError(f"'{new_word}' is already in the dictionary.")
            new_misheard = (
                _clean_misheard(misheard, new_word)
                if misheard_set
                else _clean_misheard(entry.misheard, new_word)
            )
            updated = dataclasses.replace(
                entry,
                word=new_word,
                misheard=new_misheard,
                updated_at=_now_iso(),
            )
            entries[i] = updated
            self._write(entries)
            return updated
        return None

    def delete(self, entry_id: str) -> bool:
        entries = self.list_all()
        kept = [e for e in entries if e.id != entry_id]
        if len(kept) == len(entries):
            return False
        self._write(kept)
        return True

    def _write(self, entries: list[DictionaryEntry]) -> None:
        payload = {"version": 1, "entries": [e.to_dict() for e in entries]}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic tempfile + os.replace so a crash mid-write never leaves a
        # torn sidecar (same discipline as the config writer, AP-7).
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=".stt_dictionary_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_name, self._path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise


# ----------------------------------------------------------------------
# Corrector
# ----------------------------------------------------------------------


def _boundary_pattern(phrase: str) -> re.Pattern[str]:
    """Case-insensitive, word-boundary, whitespace-flexible phrase pattern.

    Lookarounds instead of ``\\b`` so phrases with non-word edge characters
    (e-mail addresses, "Claude.md") still anchor at real token boundaries.
    """
    parts = [re.escape(tok) for tok in phrase.split()]
    body = r"\s+".join(parts)
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE | re.UNICODE)


def _edit_distance_within(a: str, b: str, budget: int) -> bool:
    """Bounded Levenshtein — True iff distance(a, b) <= budget."""
    if abs(len(a) - len(b)) > budget:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        row_min = i
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            val = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            cur.append(val)
            row_min = min(row_min, val)
        if row_min > budget:
            return False
        prev = cur
    return prev[-1] <= budget


def _fuzzy_budget(length: int) -> int:
    for min_len, budget in _FUZZY_DISTANCE_BUDGET:
        if length >= min_len:
            return budget
    return 0


class TranscriptCorrector:
    """Compiled correction rules over a fixed snapshot of entries."""

    def __init__(self, entries: list[DictionaryEntry]) -> None:
        # Explicit misheard → word replacements, longest source first so an
        # overlapping shorter rule never shadows a longer phrase.
        self._replacements: list[tuple[re.Pattern[str], str]] = []
        pairs: list[tuple[str, str]] = []
        for entry in entries:
            for variant in entry.misheard:
                pairs.append((variant, entry.word))
        pairs.sort(key=lambda p: len(p[0]), reverse=True)
        for source, target in pairs:
            self._replacements.append((_boundary_pattern(source), target))

        # Canonical casing rules for every word (single- and multi-word).
        self._casing: list[tuple[re.Pattern[str], str]] = [
            (_boundary_pattern(e.word), e.word)
            for e in sorted(entries, key=lambda e: len(e.word), reverse=True)
        ]

        # Fuzzy-repair index for SINGLE-token canonical words, keyed by the
        # casefolded first letter. Multi-token phrases are excluded — near-miss
        # repair across token splits is what explicit pairs are for.
        self._fuzzy_index: dict[str, list[str]] = {}
        self._canonical_tokens: set[str] = set()
        for entry in entries:
            word = entry.word
            self._canonical_tokens.add(word.casefold())
            if " " in word or len(word) < _FUZZY_MIN_TOKEN_LEN:
                continue
            if not word[0].isalpha():
                continue
            self._fuzzy_index.setdefault(word[0].casefold(), []).append(word)

        self._token_re = re.compile(r"[^\W\d_][\w''\-]*", re.UNICODE)
        self.rule_count = len(self._replacements) + len(self._casing)

    def correct(self, text: str) -> str:
        if not text or self.rule_count == 0:
            return text
        # 1) Explicit replacements ("Gitter" → "GitHub").
        for pattern, target in self._replacements:
            text = pattern.sub(target, text)
        # 2) Canonical casing ("github" → "GitHub"). sub() is a no-op when the
        #    casing already matches, so this is idempotent.
        for pattern, target in self._casing:
            text = pattern.sub(target, text)
        # 3) Conservative fuzzy repair of single tokens toward vocabulary
        #    words ("Veltrok" → "Veltroc").
        if self._fuzzy_index:
            text = self._token_re.sub(self._fix_token, text)
        return text

    def _fix_token(self, match: re.Match[str]) -> str:
        token = match.group(0)
        folded = token.casefold()
        if len(token) < _FUZZY_MIN_TOKEN_LEN or folded in self._canonical_tokens:
            return token
        candidates = self._fuzzy_index.get(token[0].casefold())
        if not candidates:
            return token
        best: str | None = None
        for word in candidates:
            allowed = min(_fuzzy_budget(len(token)), _fuzzy_budget(len(word)))
            if allowed <= 0:
                continue
            if _edit_distance_within(folded, word.casefold(), allowed):
                if best is not None and best != word:
                    return token  # ambiguous between two entries — leave it
                best = word
        return best if best is not None else token


# ----------------------------------------------------------------------
# Shared, live-reloading corrector
# ----------------------------------------------------------------------

_cache_lock = threading.Lock()
_cached_corrector: TranscriptCorrector | None = None
_cached_signature: tuple[int, int] | None = None
_cached_path: Path | None = None
_last_check_monotonic: float = 0.0


def _file_signature(path: Path) -> tuple[int, int]:
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return (0, 0)


def get_corrector(store: DictionaryStore | None = None) -> TranscriptCorrector:
    """Process-wide corrector, rebuilt when the sidecar changes on disk.

    One ``stat()`` at most every ``_RELOAD_CHECK_INTERVAL_S`` keeps the hot
    path cheap while REST edits still apply on the next utterance.
    """
    global _cached_corrector, _cached_signature, _cached_path, _last_check_monotonic
    path = (store or DictionaryStore()).path
    now = time.monotonic()
    with _cache_lock:
        fresh_path = path != _cached_path
        if (
            _cached_corrector is not None
            and not fresh_path
            and now - _last_check_monotonic < _RELOAD_CHECK_INTERVAL_S
        ):
            return _cached_corrector
        _last_check_monotonic = now
        signature = _file_signature(path)
        if (
            _cached_corrector is None
            or fresh_path
            or signature != _cached_signature
        ):
            entries = (store or DictionaryStore(path)).list_all()
            _cached_corrector = TranscriptCorrector(entries)
            _cached_signature = signature
            _cached_path = path
            if entries:
                log.info(
                    "STT dictionary loaded: %d entries, %d rules.",
                    len(entries),
                    _cached_corrector.rule_count,
                )
        return _cached_corrector


def dictionary_bias_words(store: DictionaryStore | None = None) -> list[str]:
    """Canonical words for decoder bias, capped for prompt-capable providers.

    Handed to cloud STT providers that accept a whisper ``prompt``; providers
    without that capability rely on post-correction alone (AP-21).
    """
    words: list[str] = []
    total = 0
    for entry in (store or DictionaryStore()).list_all():
        cost = len(entry.word) + 2
        if total + cost > _BIAS_WORDS_CHAR_CAP:
            break
        words.append(entry.word)
        total += cost
    return words


# ----------------------------------------------------------------------
# Provider wrapper
# ----------------------------------------------------------------------


class DictionaryCorrectingSTT:
    """Transparent STTProvider decorator applying dictionary corrections.

    Wraps any provider and rewrites the ``text`` of every Transcript that the
    transcribe methods return; every other attribute (``recover()``,
    ``is_warm``, model fields, …) delegates to the wrapped instance so
    duck-typed callers keep working.
    """

    def __init__(self, inner: Any, store: DictionaryStore | None = None) -> None:
        self._inner = inner
        self._store = store

    @property
    def provider_label(self) -> str:
        """Human-readable inner provider name for log lines."""
        return type(self._inner).__name__

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __repr__(self) -> str:  # pragma: no cover — logging nicety
        return f"DictionaryCorrectingSTT({self.provider_label})"

    def _apply(self, transcript: Any) -> Any:
        try:
            text = getattr(transcript, "text", None)
            if not text:
                return transcript
            corrected = get_corrector(self._store).correct(text)
            if corrected == text:
                return transcript
            log.debug("STT dictionary corrected: %r -> %r", text, corrected)
            if dataclasses.is_dataclass(transcript):
                return dataclasses.replace(transcript, text=corrected)
            transcript.text = corrected  # duck-typed fakes in tests
            return transcript
        except Exception as exc:  # noqa: BLE001 — corrections must never break STT
            log.warning("STT dictionary correction failed (%s); using raw text.", exc)
            return transcript

    async def transcribe_pcm(self, *args: Any, **kwargs: Any) -> Any:
        return self._apply(await self._inner.transcribe_pcm(*args, **kwargs))

    async def transcribe(self, *args: Any, **kwargs: Any) -> Any:
        return self._apply(await self._inner.transcribe(*args, **kwargs))

    async def stream_transcribe(
        self, *args: Any, **kwargs: Any
    ) -> AsyncIterator[Any]:
        async for transcript in self._inner.stream_transcribe(*args, **kwargs):
            yield self._apply(transcript)


def wrap_stt_with_dictionary(provider: Any) -> Any:
    """Wrap ``provider`` unless it is None or already wrapped."""
    if provider is None or isinstance(provider, DictionaryCorrectingSTT):
        return provider
    return DictionaryCorrectingSTT(provider)


__all__ = [
    "DictionaryEntry",
    "DictionaryStore",
    "TranscriptCorrector",
    "DictionaryCorrectingSTT",
    "dictionary_bias_words",
    "get_corrector",
    "stt_dictionary_path",
    "wrap_stt_with_dictionary",
    "MAX_ENTRIES",
    "MAX_WORD_LEN",
    "MAX_MISHEARD_PER_ENTRY",
]
