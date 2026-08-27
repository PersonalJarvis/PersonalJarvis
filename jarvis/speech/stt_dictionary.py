"""User dictionary for speech-to-text — custom vocabulary + misrecognition fixes.

Dictation-tool-style feature: the user registers words the STT keeps getting wrong
(proper nouns, brand names, e-mail addresses). An entry is one canonical
``word`` plus optional ``misheard`` variants:

- ``misheard`` empty → plain vocabulary word. The corrector canonicalizes the
  casing of exact (case-insensitive) hits and repairs single tokens that
  SOUND like the word ("Veltrok" → "Veltroc", "Klaude" → "Claude") — never a
  common word that merely sits one letter away ("grob" stays "grob").
- ``misheard`` non-empty → explicit replacement pairs ("Gitter" → "GitHub"),
  word-boundary + case-insensitive, multi-word capable.

The canonical words are also handed to prompt-capable Whisper providers as a
decoder bias, and Whisper answers silence by reciting that list back
("…, Claude, Agentic IDE, Claude, Agentic"). The provider wrapper drops such a
run from a transcript before correcting it (BUG-185) — from its end, and from
its middle too, because Whisper recites over a pause and then carries on with
the speech that followed it. The wrapper strips ``raw_text`` on its own, not
only when ``text`` lost something: the provider's cleanup has already folded a
fivefold "Agentic IDE" on ``text`` into one, which passes for a sentence, while
the dictation lane transcribes from ``raw_text``, where all five still stand
(BUG-185, second landing 2026-08-27).

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

# Fuzzy-repair gates (plain vocabulary words only). A token is rewritten
# toward a dictionary word only when the two SOUND the same: both fold to the
# same phonetic key (``_phonetic_fold``), or — for long tokens only — the
# folded forms are one edit apart. A raw edit distance used to be enough,
# which turned "grob" into "Grok" and "Clause" into "Claude": a person who
# registers a word wants it fixed when they SAID it, not whenever they say a
# common word that happens to be spelled nearby (BUG-185).
_FUZZY_MIN_TOKEN_LEN = 4
_FUZZY_LONG_TOKEN_LEN = 8
_FUZZY_LONG_TOKEN_BUDGET = 1

# Spelling variants that sound alike in German and English, applied in order
# on the casefolded token. Deliberately a short list: every rule makes MORE
# tokens equal, and the whole point of the fold is to stay strict. The two
# placeholders protect "sch"/"ch" from the c- and h-rules that follow.
_FOLD_RULES: tuple[tuple[str, str], ...] = (
    ("ä", "e"),
    ("ae", "e"),
    ("sch", "\x01"),
    ("ch", "\x02"),
    ("ph", "f"),
    ("th", "t"),
    ("ck", "k"),
    ("dt", "t"),
    ("tz", "ts"),
    ("z", "ts"),
    ("qu", "kv"),
    ("x", "ks"),
    ("c", "k"),
    ("v", "f"),
    ("w", "v"),
    ("y", "i"),
    ("ie", "i"),
    ("ei", "ai"),
    ("ou", "au"),  # English "ou" is German "au": "Cloude" is how STT spells "Claude"
)
_FOLD_SILENT_H_RE = re.compile(r"(?<!^)h")
_FOLD_REPEAT_RE = re.compile(r"(.)\1+")

# Prompt-echo guard. A run of dictionary items is Whisper reciting its bias
# prompt, never speech, when it is long enough or repeats itself. Where the run
# stands decides how much it takes: a whole transcript made of nothing else
# needs the fewest items, because a stretch of silence is exactly where the
# recitation happens; the END of a transcript is the next most likely place
# (the silence after the last word); a run in the MIDDLE — Whisper reciting
# over a pause and then transcribing the speech that followed — needs a repeat
# or a run longer than any sentence lists dictionary terms in a row.
_ECHO_MIN_TAIL_ITEMS = 3
_ECHO_MIN_WHOLE_ITEMS = 2
_ECHO_MIN_MID_ITEMS = 4

#: Characters that may follow a run without it being "followed by speech":
#: the recognizer's own punctuation, including the ellipsis it likes to end a
#: hallucination with (one code point, not three dots) and a dangling dash.
_ECHO_TRAIL_CHARS = " \t\r\n,.;:!?\u2026-\u2013\u2014"
_ECHO_LEAD_SEP_RE = re.compile(r"^[\s,;:]+")
_ECHO_TERMINATOR_RE = re.compile(r"^[.!?\u2026]+")
_SENTENCE_END_CHARS = ".!?\u2026:"

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


def _phonetic_fold(token: str) -> str:
    """Collapse a token to how it sounds, so spelling twins compare equal.

    "Veltrok"/"Veltroc", "Klaude"/"Claude", "Meier"/"Mayer" and
    "Anthropik"/"Anthropic" fold to one key each; "grob"/"Grok" and
    "Clause"/"Claude" do not. Only ever compared with another fold — the
    result is a key, not a spelling.
    """
    folded = token.casefold()
    for source, target in _FOLD_RULES:
        folded = folded.replace(source, target)
    folded = _FOLD_SILENT_H_RE.sub("", folded)
    folded = folded.replace("\x01", "sh").replace("\x02", "ch")
    return _FOLD_REPEAT_RE.sub(r"\1", folded)


def _sounds_like(token: str, token_fold: str, word: str, word_fold: str) -> bool:
    """True iff ``token`` may be repaired to ``word`` — they sound the same."""
    if token_fold == word_fold:
        return True
    if len(token) < _FUZZY_LONG_TOKEN_LEN or len(word) < _FUZZY_LONG_TOKEN_LEN:
        return False
    return _edit_distance_within(token_fold, word_fold, _FUZZY_LONG_TOKEN_BUDGET)


def _compile_echo_patterns(
    entries: list[DictionaryEntry],
) -> tuple[re.Pattern[str] | None, re.Pattern[str] | None]:
    """Patterns behind :meth:`TranscriptCorrector.strip_prompt_echo`.

    ``run`` matches a run of two or more dictionary items anywhere in the
    text — whole entries, plus the single tokens of multi-word entries,
    because a recitation may start mid-phrase ("IDE, Agentic IDE, …").
    ``item`` finds the WHOLE entries inside such a run; fragments never count
    as items. Where the run stands (whole, tail, middle) is the caller's
    reading of what surrounds the match.
    """
    phrases = sorted({e.word for e in entries}, key=len, reverse=True)
    if not phrases:
        return None, None
    whole = [r"\s+".join(re.escape(tok) for tok in p.split()) for p in phrases]
    known = {p.casefold() for p in phrases}
    fragments = sorted(
        {
            tok
            for p in phrases
            if " " in p
            for tok in p.split()
            if len(tok) >= 2 and tok.casefold() not in known
        },
        key=len,
        reverse=True,
    )
    item_alt = "|".join(whole)
    run_alt = "|".join(whole + [re.escape(f) for f in fragments])
    separator = r"(?:\s*[,;:]\s*|\s*\.\s+|\s+)"
    run = re.compile(
        rf"(?<!\w)(?P<run>(?:{run_alt})(?:{separator}(?:{run_alt}))+)(?!\w)",
        re.IGNORECASE | re.UNICODE,
    )
    item = re.compile(rf"(?<!\w)(?:{item_alt})(?!\w)", re.IGNORECASE | re.UNICODE)
    return run, item


def _splice_out_run(before: str, after: str) -> str:
    """``before`` and ``after`` rejoined as if the run between them was never
    said. A separator the run dragged along goes with it; a sentence mark
    right after the run belonged to the words before it, so it moves there
    unless those already end a sentence — "and then Agentic IDE, Agentic
    IDE. That was" reads "and then. That was", never "and then . That was".
    """
    left = before.rstrip()
    right = _ECHO_LEAD_SEP_RE.sub("", after)
    mark = _ECHO_TERMINATOR_RE.match(right)
    if mark is not None:
        right = right[mark.end() :].lstrip()
        if left and left[-1] not in _SENTENCE_END_CHARS:
            left = left.rstrip(",;:") + mark.group(0)
    if not left:
        return right
    if not right:
        return left
    return f"{left} {right}"


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
        # first letter of the phonetic fold. Multi-token phrases are excluded —
        # near-miss repair across token splits is what explicit pairs are for.
        self._fuzzy_index: dict[str, list[tuple[str, str]]] = {}
        self._canonical_tokens: set[str] = set()
        for entry in entries:
            word = entry.word
            self._canonical_tokens.add(word.casefold())
            if " " in word or len(word) < _FUZZY_MIN_TOKEN_LEN:
                continue
            if not word[0].isalpha():
                continue
            fold = _phonetic_fold(word)
            self._fuzzy_index.setdefault(fold[:1], []).append((word, fold))

        self._token_re = re.compile(r"[^\W\d_][\w''\-]*", re.UNICODE)
        self._echo_run_re, self._echo_item_re = _compile_echo_patterns(entries)
        self.rule_count = len(self._replacements) + len(self._casing)

    def strip_prompt_echo(self, text: str) -> str:
        """Drop a recited bias prompt from ``text``, wherever it stands.

        Prompt-capable Whisper providers receive the canonical words as a
        comma-separated decoder bias, and Whisper answers a stretch of silence
        by continuing that list — "…, Claude, Agentic IDE, Claude, Agentic".
        A run of dictionary items is that recitation when it repeats an item,
        or when it is longer than a sentence has reason to be: at least
        ``_ECHO_MIN_TAIL_ITEMS`` items when the run closes the transcript, and
        ``_ECHO_MIN_WHOLE_ITEMS`` when the transcript holds nothing else.

        The silence Whisper recites over is not only the one after the last
        word. A pause mid-dictation lands the run in the MIDDLE of a window,
        with the speech that followed the pause transcribed right after it
        ("… up there. IDE, Agentic IDE, Agentic IDE, Agentic IDE, Agentic So
        I cannot …"). A run there is dropped when it repeats an item or holds
        ``_ECHO_MIN_MID_ITEMS`` — a person may list three of their terms in
        one breath; the fourth, or the same one twice, is the prompt.

        One word, or two different ones, is a sentence and stays wherever it
        is: "…I use Claude, Agentic IDE." and "the Agentic IDE amounts" are
        things a person says.
        """
        if not text or self._echo_run_re is None or self._echo_item_re is None:
            return text
        matches = list(self._echo_run_re.finditer(text))
        if not matches:
            return text
        out = text
        # Back to front: a removal only ever shortens what FOLLOWS the earlier
        # matches, so their offsets into ``out`` stay valid — and a run that
        # becomes the tail once the run after it is gone is judged as one.
        for match in reversed(matches):
            run = match.group("run")
            items = [
                " ".join(m.group(0).casefold().split())
                for m in self._echo_item_re.finditer(run)
            ]
            before = out[: match.start("run")]
            after = out[match.end("run") :]
            closes = not after.strip(_ECHO_TRAIL_CHARS)
            whole = closes and not before.strip()
            if whole:
                needed = _ECHO_MIN_WHOLE_ITEMS
            elif closes:
                needed = _ECHO_MIN_TAIL_ITEMS
            else:
                needed = _ECHO_MIN_MID_ITEMS
            if len(items) < needed and len(items) == len(set(items)):
                continue
            out = before.rstrip() if closes else _splice_out_run(before, after)
        return out

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
        token_fold = _phonetic_fold(token)
        candidates = self._fuzzy_index.get(token_fold[:1])
        if not candidates:
            return token
        best: str | None = None
        for word, word_fold in candidates:
            if _sounds_like(token, token_fold, word, word_fold):
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
        """Human-readable inner provider name for log lines.

        Delegates when the inner object carries a label of its own. Since the
        runtime fallback chain started wrapping the real provider, the class
        name here resolved to ``FallbackSTT`` and the transcription log stopped
        naming which provider actually ran — the one fact you need when a
        provider is the thing failing, which is exactly when someone reads it.
        """
        inner = getattr(self._inner, "provider_label", "") or getattr(
            self._inner, "name", ""
        )
        # ``name`` before the class name: every provider carries the id the
        # rest of the app knows it by ("groq-api"), and a log line naming
        # ``GroqWhisperAPI`` answers a question nobody asked. The class name
        # stays as the last resort for an object that has neither.
        return str(inner) if inner else type(self._inner).__name__

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __repr__(self) -> str:  # pragma: no cover — logging nicety
        return f"DictionaryCorrectingSTT({self.provider_label})"

    def _apply(self, transcript: Any) -> Any:
        try:
            text = getattr(transcript, "text", None)
            if not text:
                return transcript
            corrector = get_corrector(self._store)
            # A provider may also carry the pre-cleanup string on ``raw_text``,
            # and the dictation lane transcribes from THAT. The user's spelling
            # corrections are not part of the cleanup they opted out of — they
            # are words a person registered by name — so they have to reach both
            # fields or dictation silently stops honouring the dictionary.
            raw = getattr(transcript, "raw_text", "") or ""
            # A finished transcript that carries a recited bias prompt loses
            # it BEFORE correction. Partials are still growing — an
            # enumeration cut off mid-list is not evidence of anything.
            # ``raw_text`` is judged on its own, never on what ``text`` lost:
            # the provider's cleanup folds a repeated phrase on ``text`` into
            # one ("Agentic IDE" ×5 → "Agentic IDE"), which passes for a
            # sentence, while ``raw_text`` — the field dictation transcribes
            # from — still holds every copy. Stripping raw only after text had
            # lost something is how five "Agentic IDE" reached a document
            # the day after the tail guard shipped.
            if not getattr(transcript, "is_partial", False):
                text = self._strip_echo(corrector, text, "text")
                raw = self._strip_echo(corrector, raw, "raw_text") if raw else raw
            corrected = corrector.correct(text)
            corrected_raw = corrector.correct(raw) if raw else raw
            original_text = getattr(transcript, "text", "")
            original_raw = getattr(transcript, "raw_text", "") or ""
            if corrected == original_text and corrected_raw == original_raw:
                return transcript
            log.debug("STT dictionary corrected: %r -> %r", original_text, corrected)
            updates: dict[str, Any] = {"text": corrected}
            # Only when the provider actually has the field: passing it to a
            # provider that does not would raise instead of correcting.
            if original_raw:
                updates["raw_text"] = corrected_raw
            if dataclasses.is_dataclass(transcript):
                return dataclasses.replace(transcript, **updates)
            for name, value in updates.items():  # duck-typed fakes in tests
                setattr(transcript, name, value)
            return transcript
        except Exception as exc:  # noqa: BLE001 — corrections must never break STT
            log.warning("STT dictionary correction failed (%s); using raw text.", exc)
            return transcript

    @staticmethod
    def _strip_echo(corrector: TranscriptCorrector, value: str, field: str) -> str:
        """``value`` without a recited bias prompt, logged when one was there."""
        kept = corrector.strip_prompt_echo(value)
        if kept != value:
            log.info(
                "STT dictionary dropped a recited bias prompt from %s: %r -> %r",
                field,
                value[-120:],
                kept[-120:],
            )
        return kept

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
