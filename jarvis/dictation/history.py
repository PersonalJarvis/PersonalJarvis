"""Local record of what was dictated — raw text, cleaned text, and outcome.

Two reasons this exists, and one reason it is careful:

* **Auditability.** The filler cleanup is deterministic, but "deterministic"
  only helps if you can see what it did. Storing raw *and* cleaned side by side
  is what makes a wrong rule findable instead of merely suspected.
* **Recovery.** When insertion degrades to "it is on your clipboard" and the
  user copies something else before pasting, the transcript is otherwise gone.

The care: dictated text is among the most sensitive data this application ever
holds — it is, by definition, whatever the user is writing. So the store is
local-only (a JSON sidecar under ``user_data_dir()/data/``, never synced,
never sent anywhere), capped in size, aged out on a user-set retention, and
purgeable with one call. It can be switched off entirely
(``[dictation].history_enabled = false``), in which case nothing is written.

Storage pattern mirrors ``jarvis.speech.stt_dictionary.DictionaryStore``:
atomic tempfile + ``os.replace`` so a crash mid-write never leaves a torn file.

Two companion sidecars sit next to this one, both derived from ITS path so a
history pointed somewhere else takes them along:

* ``dictation_stats.json`` (:mod:`jarvis.dictation.stats`) — per-day counts and
  durations, no text, never pruned. It exists because totals derived from a
  30-day rolling window would silently stop growing.
* ``dictation_audio/`` (:mod:`jarvis.dictation.audio`) — WAV files kept ONLY for
  dictations that produced nothing usable, and only when the user allows it.
  It is what makes Restore more than a button that shakes its head.

Deleting the history deletes all three. Anything less would be a quiet lie
about what the application still knows.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field, replace
from dataclasses import fields as fields_of
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jarvis.dictation._locks import store_lock

if TYPE_CHECKING:  # import-time cost stays zero on the dictation hot path
    from jarvis.dictation.stats import DictationStats

log = logging.getLogger(__name__)

#: Hard ceiling regardless of configuration — an abuse guard, not a product limit.
MAX_ENTRIES_CEILING = 5_000
#: Longest single transcript kept. Longer ones are stored truncated with a marker.
MAX_TEXT_LEN = 20_000


@dataclass(frozen=True, slots=True)
class DictationEntry:
    """One completed dictation."""

    id: str
    created_at: str
    #: The transcript exactly as the STT returned it.
    raw_text: str
    #: What was actually inserted (equals ``raw_text`` when no cleanup applied).
    text: str
    #: The language of the dictation as ONE code, or "" when none was
    #: established. Normalised at the store boundary by
    #: :func:`_normalize_language` — never stored the way a provider happened to
    #: spell it, because four spellings of two languages is what the live
    #: history actually grew (AP-4).
    language: str = ""
    #: Seconds of audio.
    duration_s: float = 0.0
    #: One of ``jarvis.dictation.outcomes.DICTATION_OUTCOMES``.
    outcome: str = ""
    #: How it got there, e.g. ``clipboard+ctrl_v``.
    method: str = ""
    #: Words the cleanup removed (0 when it did not run or was refused).
    removed_words: int = 0
    #: Why a cleanup did not apply — ``""`` when it did.
    cleanup_reason: str = ""
    #: Words in the inserted text. Stored rather than recomputed so the
    #: statistics sidecar and the UI can never disagree about one entry.
    word_count: int = 0
    #: The user hid this entry. Separate from ``outcome`` on purpose: an entry
    #: can be both ``inserted`` and discarded, and folding the two into one
    #: string would make that state unrepresentable (AD-6).
    discarded: bool = False
    #: Local path of the kept audio sidecar, or ``None``. NEVER serialised to
    #: an API response — the wire shape exposes ``audio_available`` instead,
    #: because a filesystem path in a JSON body is an information leak that
    #: buys the client nothing.
    audio_path: str | None = None
    #: Why transcription failed, when it did. ``None`` on every other path.
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_storage_dict(self) -> dict[str, Any]:
        """The full on-disk shape, including ``audio_path``. Sidecar only."""
        return asdict(self)

    def to_dict(self) -> dict[str, Any]:
        """The API shape: no ``audio_path``, plus a resolved availability flag.

        The SAFE shape is the default one on purpose. A serialiser that leaks
        by default and has to be opted out of is a leak waiting for the one
        call site that forgets — so the on-disk shape is the one that needs an
        explicit name (:meth:`to_storage_dict`), not the other way round.

        ``audio_available`` is a live filesystem check rather than a stored
        boolean, because the retention prune deletes sidecars behind the
        history's back — a cached flag would offer the user a Restore button
        for audio that is no longer there.
        """
        from jarvis.dictation.audio import audio_exists

        public_metadata = {
            key: self.metadata[key]
            for key in (
                "polish_status",
                "polish_provider",
                "polish_latency_ms",
                "stt_providers",
                "stt_models",
                "detected_languages",
                "stt_latency_ms",
                "stt_calls",
                "stt_errors",
                "stt_audit",
                "audio_sample_rate_hz",
                "audio_rms",
                "audio_clipping_ratio",
                "audio_dropouts",
                "audio_dropout_ms",
            )
            if key in self.metadata
        }
        return {
            "id": self.id,
            "created_at": self.created_at,
            "raw_text": self.raw_text,
            "text": self.text,
            "language": self.language,
            "duration_s": self.duration_s,
            "outcome": self.outcome,
            "method": self.method,
            "removed_words": self.removed_words,
            "cleanup_reason": self.cleanup_reason,
            "word_count": self.word_count,
            "discarded": self.discarded,
            "audio_available": audio_exists(self.audio_path),
            "error": self.error,
            **public_metadata,
        }


def default_history_path() -> Path:
    """``<user data>/data/dictation_history.json``."""
    from jarvis.core.paths import user_data_dir

    return Path(user_data_dir()) / "data" / "dictation_history.json"


class DictationHistory:
    """Append-only-ish store of recent dictations. Never raises to the caller.

    Every public method is wrapped: a broken or unreadable history file must
    never cost the user their dictation. Failures are logged and degrade to
    "no history", which is a cosmetic loss.
    """

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        stats_path: Path | str | None = None,
    ) -> None:
        self._path = Path(path) if path is not None else default_history_path()
        # The statistics sidecar lives NEXT TO the history file rather than at
        # a globally resolved location, so pointing the history at a temporary
        # directory (a test, a second profile) moves both together instead of
        # leaking counters into the real user data directory.
        self._stats_path = (
            Path(stats_path)
            if stats_path is not None
            else self._path.parent / "dictation_stats.json"
        )
        # Keyed by the PATH, not owned by this object: every call site builds a
        # fresh DictationHistory per operation (the REST routes, the speech
        # pipeline), so a per-instance lock would serialise nothing and two
        # overlapping read-modify-write cycles would end with the later
        # os.replace quietly erasing the earlier one. See jarvis.dictation._locks.
        self._lock = store_lock(self._path)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def stats_path(self) -> Path:
        return self._stats_path

    @property
    def audio_dir(self) -> Path:
        """Where this history's audio sidecars live — beside the JSON file.

        Derived rather than resolved globally for the same reason as
        ``stats_path``: a history pointed at a temporary directory must not
        be able to purge the real user's audio.
        """
        return self._path.parent / "dictation_audio"

    def stats(self) -> DictationStats:
        """The lifetime-counter sidecar bound to this history."""
        from jarvis.dictation.stats import DictationStats

        return DictationStats(self._stats_path)

    # -- reading ---------------------------------------------------------

    def list_all(self, *, include_discarded: bool = True) -> list[DictationEntry]:
        """Newest first. An unreadable file reads as an empty history."""
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError as exc:
            log.warning("dictation history unreadable: %s", exc)
            return []
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError) as exc:
            log.warning("dictation history is corrupt, ignoring it: %s", exc)
            return []
        entries: list[DictationEntry] = []
        for item in payload.get("entries", []) or []:
            if not isinstance(item, dict):
                continue
            try:
                entries.append(
                    DictationEntry(
                        id=str(item.get("id") or uuid.uuid4().hex),
                        created_at=str(item.get("created_at") or ""),
                        raw_text=str(item.get("raw_text") or ""),
                        text=str(item.get("text") or ""),
                        # Normalised on the way OUT as well as on the way in:
                        # every row already on disk was written before this
                        # store had a boundary, so the four live spellings are
                        # collapsed here and the next _write persists the
                        # collapsed value (F10).
                        language=_normalize_language(item.get("language")),
                        duration_s=float(item.get("duration_s") or 0.0),
                        outcome=str(item.get("outcome") or ""),
                        method=str(item.get("method") or ""),
                        removed_words=int(item.get("removed_words") or 0),
                        cleanup_reason=str(item.get("cleanup_reason") or ""),
                        # Fields added after the first release. The reader has
                        # always built field-by-field with .get(), so a file
                        # written by an older install reads as these defaults
                        # instead of failing — verified, not assumed
                        # (test_history_written_before_the_new_fields_reads_as_defaults).
                        # word_count is the exception: its default is a
                        # measurable value, so a legacy row is repaired rather
                        # than left at a zero that would then be written back
                        # forever (F9, see _healed_word_count).
                        word_count=_healed_word_count(item),
                        discarded=bool(item.get("discarded") or False),
                        audio_path=(str(item["audio_path"]) if item.get("audio_path") else None),
                        error=(str(item["error"]) if item.get("error") else None),
                        metadata=dict(item.get("metadata") or {}),
                    )
                )
            except (TypeError, ValueError):
                continue  # one bad row never invalidates the rest
        if not include_discarded:
            entries = [e for e in entries if not e.discarded]
        return entries

    def get(self, entry_id: str) -> DictationEntry | None:
        """One entry by id, or ``None``."""
        for entry in self.list_all():
            if entry.id == entry_id:
                return entry
        return None

    # -- writing ---------------------------------------------------------

    def add(
        self,
        *,
        raw_text: str,
        text: str,
        language: str = "",
        duration_s: float = 0.0,
        outcome: str = "",
        method: str = "",
        removed_words: int = 0,
        cleanup_reason: str = "",
        word_count: int | None = None,
        error: str | None = None,
        max_entries: int = 200,
        retention_days: int = 30,
        **metadata: Any,
    ) -> DictationEntry | None:
        """Record one dictation and prune. ``None`` when nothing was stored.

        A dictation with no text at all is still recorded when its outcome says
        the user lost something (``failed`` / ``cancelled`` / ``empty``) — that
        row is the only place a later Restore can start from. Without it, the
        worst failure this feature has is also its most invisible one.

        Any surplus keyword lands in :attr:`DictationEntry.metadata` instead of
        raising ``TypeError``. That is not laxity, it is the fix for a real
        defect: the polish pass passed ``polish_status`` / ``polish_provider`` /
        ``polish_latency_ms`` here, the strict signature rejected them, and the
        caller's ``except TypeError`` fell back to writing the row WITHOUT them.
        The result was a generative pass that could rewrite a user's words while
        leaving no record anywhere of whether it had run, which provider
        answered, or which guard fired — the one question a bug report about
        wrong words has to be able to answer. :meth:`DictationEntry.to_dict`
        exposes only its explicit safe-key allowlist; arbitrary metadata stays
        in the local sidecar and can never drift into an API response.
        """
        from jarvis.dictation.outcomes import is_recoverable

        if not (raw_text or text) and not is_recoverable(outcome):
            return None
        from jarvis.dictation.cleanup import count_words

        entry = DictationEntry(
            id=uuid.uuid4().hex,
            created_at=datetime.now(UTC).isoformat(),
            raw_text=_clip(raw_text),
            text=_clip(text),
            language=_normalize_language(language),
            duration_s=max(0.0, float(duration_s or 0.0)),
            outcome=str(outcome or ""),
            method=str(method or ""),
            removed_words=max(0, int(removed_words or 0)),
            cleanup_reason=str(cleanup_reason or ""),
            word_count=(
                max(0, int(word_count))
                if word_count is not None
                else count_words(text or raw_text)
            ),
            error=(str(error) if error else None),
            metadata={k: v for k, v in metadata.items() if v not in ("", None)},
        )
        try:
            with self._lock:
                # Counted BEFORE the prune, so a dictation that immediately
                # ages out of the rolling window still shows up in the lifetime
                # totals. That ordering is the whole point of a second sidecar.
                self._record_stats(entry)
                entries = [entry, *self.list_all()]
                entries = _prune(
                    entries,
                    max_entries=max_entries,
                    retention_days=retention_days,
                )
                self._write(entries)
        except Exception:  # noqa: BLE001 — history is never worth a failed dictation
            log.warning("could not record the dictation history entry", exc_info=True)
            return None
        return entry

    def update(self, entry_id: str, **fields: Any) -> DictationEntry | None:
        """Replace named fields on one entry. ``None`` when the id is unknown.

        Used by the audio hand-off (``audio_path`` is only known after the
        entry exists, because the file name is built from its id) and by a
        Restore that re-transcribed. Unknown field names are ignored rather
        than raised, so an older caller can never break a write.
        """
        allowed = {f.name for f in fields_of(DictationEntry)} - {"id", "created_at"}
        changes = {k: v for k, v in fields.items() if k in allowed}
        if not changes:
            return None
        if "raw_text" in changes:
            changes["raw_text"] = _clip(str(changes["raw_text"] or ""))
        if "text" in changes:
            changes["text"] = _clip(str(changes["text"] or ""))
        if "language" in changes:
            # The Restore route re-transcribes and writes the language its
            # provider reported, so this is the fourth writer into the same
            # field. Normalising HERE rather than there is what makes the
            # boundary a boundary: a caller cannot re-introduce a fifth
            # spelling by forgetting to collapse its own value first.
            changes["language"] = _normalize_language(changes["language"])
        if "audio_path" in changes and changes["audio_path"] is not None:
            changes["audio_path"] = str(changes["audio_path"])
        try:
            with self._lock:
                entries = self.list_all()
                updated: DictationEntry | None = None
                out: list[DictationEntry] = []
                for entry in entries:
                    if entry.id == entry_id and updated is None:
                        updated = replace(entry, **changes)
                        out.append(updated)
                    else:
                        out.append(entry)
                if updated is None:
                    return None
                self._write(out)
                return updated
        except Exception:  # noqa: BLE001
            log.warning("could not update a dictation history entry", exc_info=True)
            return None

    def set_discarded(self, entry_id: str, value: bool = True) -> DictationEntry | None:
        """Hide or un-hide one entry. The soft counterpart to :meth:`delete`.

        Soft on purpose: the trash icon in the UI calls this, so a mis-click
        stays recoverable. ``DELETE /history/{id}`` keeps hard-delete semantics
        for anyone scripting the CLI (AD-8).
        """
        return self.update(entry_id, discarded=bool(value))

    def delete(self, entry_id: str) -> bool:
        """Hard-delete one entry AND its audio sidecar. Irreversible."""
        try:
            with self._lock:
                entries = self.list_all()
                kept = [e for e in entries if e.id != entry_id]
                if len(kept) == len(entries):
                    return False
                doomed = [e for e in entries if e.id == entry_id]
                self._write(kept)
        except Exception:  # noqa: BLE001
            log.warning("could not delete a dictation history entry", exc_info=True)
            return False
        # Outside the lock: the sidecar is a separate file and a slow unlink
        # must not hold up another dictation being recorded.
        for entry in doomed:
            if entry.audio_path:
                from jarvis.dictation.audio import delete_dictation_audio

                delete_dictation_audio(entry.audio_path)
        return True

    def clear(self) -> bool:
        """Purge everything. The user-facing "delete my dictation history".

        Deliberately total: the entries, the kept audio and the lifetime
        counters all go. Leaving the streak standing after someone asked for
        their dictation history to be deleted would be a quiet lie about what
        the app still knows.
        """
        try:
            with self._lock:
                self._write([])
        except Exception:  # noqa: BLE001
            log.warning("could not clear the dictation history", exc_info=True)
            return False
        try:
            from jarvis.dictation.audio import purge_dictation_audio

            purge_dictation_audio(directory=self.audio_dir)
            self.stats().reset()
        except Exception:  # noqa: BLE001
            log.warning("could not purge the dictation sidecars", exc_info=True)
        return True

    def _record_stats(self, entry: DictationEntry) -> None:
        """Feed one entry to the lifetime counters. Never raises."""
        try:
            self.stats().record(
                created_at=entry.created_at,
                word_count=entry.word_count,
                duration_s=entry.duration_s,
            )
        except Exception:  # noqa: BLE001 — a counter never costs a dictation
            log.debug("dictation statistics write failed", exc_info=True)

    def _write(self, entries: list[DictationEntry]) -> None:
        payload = {"version": 1, "entries": [e.to_storage_dict() for e in entries]}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic tempfile + os.replace: a crash mid-write never leaves a torn
        # sidecar (same discipline as the config writer, AP-7).
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=".dictation_history_", suffix=".tmp"
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


def _clip(text: str) -> str:
    value = str(text or "")
    if len(value) <= MAX_TEXT_LEN:
        return value
    return value[:MAX_TEXT_LEN] + " […truncated]"


def _healed_word_count(item: dict[str, Any]) -> int:
    """The stored word count, recomputed when the row never carried one.

    ``word_count`` was added after the first release, so every row written
    before it defaults to zero — and a zero is indistinguishable from a
    measured "this dictation had no words". That matters because the lifetime
    counters skip anything at or below zero
    (:meth:`jarvis.dictation.stats.DictationStats.record`), so a history full of
    obvious text reported a fraction of its own words: 26 counted for 41 rows on
    the maintainer's machine. Worse, the zero was durable — every write rewrites
    the whole file from what was read, so a default that is never repaired on
    read is a default that gets persisted forever.

    So a missing or zero count on a row that HAS text is treated as "never
    measured" rather than "measured as none", and recomputed with the same
    :func:`jarvis.dictation.cleanup.count_words` the writer would have used. The
    next :meth:`DictationHistory._write` persists it, so the repair happens once
    per row rather than on every read.

    A row with no text at all keeps its zero: a failed or empty dictation really
    did produce no words, and inventing one would break the counters in the
    other direction. A stored count that is not a number is treated the same way
    as a missing one — a broken field is worth healing, never worth dropping the
    whole row over.
    """
    try:
        stored = int(item.get("word_count") or 0)
    except (TypeError, ValueError):
        stored = 0
    if stored > 0:
        return stored
    text = str(item.get("text") or "") or str(item.get("raw_text") or "")
    if not text.strip():
        return 0
    from jarvis.dictation.cleanup import count_words

    return count_words(text)


#: Tags that are an answer of "we could not tell", not a language. ``auto`` is
#: the REQUEST ("detect it") echoed back by a provider that had no opinion;
#: ``unknown`` / ``und`` are what a recogniser says when detection failed. All
#: three store as "" so "not detected" stays distinguishable from a real result.
_NON_LANGUAGE_TAGS: frozenset[str] = frozenset({"auto", "unknown", "und"})


def _normalize_language(tag: object) -> str:
    """Collapse a provider's language tag to ONE spelling per row (AP-4).

    The live history grew four spellings for two languages — ``"English"`` 27
    rows, ``"German"`` 9, ``"de"`` 3, ``"en"`` 1, ``""`` 1 — because every
    writer stored whatever its provider happened to say: a Whisper cloud
    endpoint returns the English NAME, local faster-whisper returns an ISO code,
    a BCP-47 pin returns ``"de-DE"``. Any consumer doing
    ``{"de": ...}.get(entry.language)`` then misses on three rows out of four,
    which is exactly the BUG-008 / AP-4 shape. The collapse therefore happens
    HERE, at the store boundary, on the way in AND on the way out — not in each
    of the four writers, where the fifth one to be added would forget.

    Resolution, in order:

    * an empty tag, or one of :data:`_NON_LANGUAGE_TAGS`, stores as ``""``;
    * anything :func:`jarvis.core.turn_language.normalize_language_tag` resolves
      stores as that code — ``de`` / ``en`` / ``es``;
    * **anything else keeps what the recogniser said**, lower-cased and reduced
      to its primary subtag (``"ja-JP"`` -> ``"ja"``). Never coerced, never
      dropped.

    That last rule is the load-bearing one. The canonical resolver knows only
    the three product locales and answers ``"unknown"`` for the other ~96
    languages dictation accepts (:data:`jarvis.core.config.RECOGNITION_LANGUAGES`),
    so folding an unresolved tag into the default locale would relabel a
    Japanese dictation as English — a worse lie than the drift this function
    exists to remove. Storing ``""`` is the other bad answer: it erases the only
    record of what was actually heard and makes a detected language look like a
    failed detection. Keeping the tag costs nothing and stays honest.

    The consequence is a bounded, deliberate residue: one code per row is EXACT
    for de/en/es and best-effort canonicalisation elsewhere, so ``"Japanese"``
    and ``"ja"`` can still coexist as two rows. Closing that needs a language
    NAME -> code table covering all of ``RECOGNITION_LANGUAGES``, and it belongs
    next to the canonical resolver in :mod:`jarvis.core.turn_language` — a
    second private copy here would be the drift it is meant to prevent.
    """
    value = str(tag or "").strip().lower().replace("_", "-")
    if not value or value in _NON_LANGUAGE_TAGS:
        return ""
    from jarvis.core.turn_language import normalize_language_tag

    code = normalize_language_tag(value)
    if code != "unknown":
        return code
    return value.split("-", 1)[0]


def _holds_pending_recovery(entry: DictationEntry) -> bool:
    """``True`` when dropping this entry would strand a Restore.

    An entry the user can still recover from — one that ended badly or was
    discarded, and whose audio is still on disk — is the ONLY thing that makes
    the Restore button do anything. Dropping the row while leaving the audio
    file behind is the worst of both worlds: the user sees the entry vanish,
    the disk keeps the recording, and nobody can reach either again. The
    history row IS the only handle on that file, so it survives BOTH caps —
    the count cap and the retention window.

    Surviving the retention window is not an exception to the retention
    promise, it is how the promise is kept: the row stops being exempt the
    moment the audio ages out on its own schedule
    (``[dictation].audio_retention_days``, enforced by
    :func:`jarvis.dictation.audio.prune_audio`), and the next prune drops it.
    The two retention keys are independently settable, so the alternative —
    trusting the audio window to be the shorter one — leaves an orphaned WAV
    behind on every configuration where it is not.
    """
    from jarvis.dictation.outcomes import is_recoverable

    if not entry.audio_path:
        return False
    if not (entry.discarded or is_recoverable(entry.outcome)):
        return False
    from jarvis.dictation.audio import audio_exists

    return audio_exists(entry.audio_path)


def _is_expired(entry: DictationEntry, cutoff: datetime) -> bool:
    """``True`` when this entry is older than the retention cutoff.

    An unparseable timestamp reads as "not expired": a row whose age cannot be
    established is kept rather than silently discarded.
    """
    try:
        created = datetime.fromisoformat(entry.created_at)
    except (TypeError, ValueError):
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return created < cutoff


def _prune(
    entries: list[DictationEntry],
    *,
    max_entries: int,
    retention_days: int,
) -> list[DictationEntry]:
    """Drop entries past the count cap or the retention window.

    ``retention_days = 0`` means "keep until the count cap"; an unparseable
    timestamp is kept rather than silently discarded. ``max_entries = 0`` is an
    explicit "keep nothing" and overrides every exemption.

    Both caps are checked in one pass, because the pending-recovery exemption
    costs a filesystem probe per entry and applies to both of them.
    """
    cap = max(0, min(int(max_entries or 0), MAX_ENTRIES_CEILING))
    if not cap:
        return []
    cutoff: datetime | None = None
    if retention_days and retention_days > 0:
        cutoff = datetime.now(UTC) - timedelta(days=int(retention_days))

    result: list[DictationEntry] = []
    budget = cap
    for entry in entries:  # newest first — the survivors of the cap are recent
        if _holds_pending_recovery(entry):
            # Exempt from both caps, and it does not spend the count budget.
            result.append(entry)
            continue
        if cutoff is not None and _is_expired(entry, cutoff):
            continue
        if budget > 0:
            result.append(entry)
            budget -= 1
    # The exemption is bounded too: the audio retention caps how many sidecars
    # can exist, and the absolute ceiling backstops a pathological file.
    return result[:MAX_ENTRIES_CEILING]


__all__ = [
    "MAX_ENTRIES_CEILING",
    "MAX_TEXT_LEN",
    "DictationEntry",
    "DictationHistory",
    "default_history_path",
]
