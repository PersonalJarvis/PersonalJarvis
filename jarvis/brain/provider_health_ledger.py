"""Passive provider health — the last REAL outcome of every provider.

Until 2026-09-30 the status dots on the API-Keys tabs, the sidebar, the dock
and the chat composer's provider picker were fed by live probes: a real "hi"
completion for the brain and the tool model, a real TTS synthesis, half a
second of audio to a cloud recognizer, a real realtime session start. They ran
on app start, on every window reload and after every key or provider event, so
an app nobody was talking to still spent money on the user's keys (live log:
one TTS synthesis plus one billed voice-session start per reload, about 17 a
day, and a paid completion every time the chat opened).

The dots now read THIS record instead. Every real call the app makes during
normal use reports its outcome here — the brain turn, the tool-model step, the
spoken sentence, the transcribed utterance, the realtime handshake — and the
explicit, user-clicked "Test" button (plus the one check right after the user
saves a key) reports its verdict too. Nothing in this module calls a provider.

What is kept, per ``(provider id, modality)``:

* the provider-test status of the outcome (``ok``, ``bad_key``, ``no_credits``,
  ``rate_limited``, ``model_unavailable``, ``unreachable`` — the vocabulary of
  :mod:`jarvis.brain.provider_test`, so the rollup maps it exactly as it mapped a
  probe), the time it happened, the model when the caller knew it, and whether
  it came from normal use or an explicit test.

What is never kept: a provider's error body, a key, a prompt, a transcript
(AP-34). A failure is classified the moment it is reported and only the
classification survives.

Rules the readers rely on:

* An unrecognised failure (``error``) from normal use is NOT evidence about the
  provider — it may be a tool, a cancelled turn or our own bug — so it is not
  recorded. The explicit Test button's verdict is recorded whatever it says.
* ``bad_key`` / ``no_credits`` are failures of the CREDENTIAL, which every
  modality of the same provider id shares; a newer one on a sibling modality
  wins over an older own outcome (see :meth:`ProviderHealthLedger.effective`).
* Transient outcomes (``rate_limited``, ``unreachable``) age out after
  :data:`TRANSIENT_TTL_S`, so a blip never paints a dot red for the rest of the
  day while nobody uses that provider.
* Saving a new key forgets what the old key did (:func:`forget_providers`), and
  changing a model forgets the model-dependent outcomes of that modality.

The record lives in memory and in ``DATA_DIR/state/provider_health.json`` so the
dots survive a restart. The file is written only when an outcome CHANGES (or an
unchanged one is older than :data:`_REFRESH_WRITE_S` on disk), never per call,
so the hot voice path pays a dict update and nothing else (AP-9). Nothing
initialises at import time (AP-26): the file is read on first use.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.brain import provider_test as _pt

log = logging.getLogger(__name__)

LEDGER_FILE_NAME = "provider_health.json"
_FILE_VERSION = 1

# ── Modalities ───────────────────────────────────────────────────────────────
MODALITY_BRAIN = "brain"
MODALITY_TOOL = "tool"
MODALITY_TTS = "tts"
MODALITY_STT = "stt"
MODALITY_REALTIME = "realtime"
MODALITIES: tuple[str, ...] = (
    MODALITY_BRAIN,
    MODALITY_TOOL,
    MODALITY_TTS,
    MODALITY_STT,
    MODALITY_REALTIME,
)

# ── Sources ──────────────────────────────────────────────────────────────────
SOURCE_USE = "use"  # a call the app made during normal use
SOURCE_TEST = "test"  # the user clicked Test / saved a key / picked a model
SOURCES: tuple[str, ...] = (SOURCE_USE, SOURCE_TEST)

# ── Which outcomes are evidence ──────────────────────────────────────────────
#: Outcomes a normal call may record. ``error`` is excluded (not evidence about
#: the provider) and so is ``not_configured`` (the credential-presence check
#: already answers that, and a missing key is not a provider state).
USE_STATUSES: frozenset[str] = frozenset(
    {
        _pt.OK,
        _pt.BAD_KEY,
        _pt.NO_CREDITS,
        _pt.RATE_LIMITED,
        _pt.MODEL_UNAVAILABLE,
        _pt.UNREACHABLE,
    }
)
#: An explicit test is a deliberate, fresh verdict: its ``error`` counts too.
TEST_STATUSES: frozenset[str] = USE_STATUSES | {_pt.ERROR}
#: Failures of the credential itself — shared by every modality of a provider.
CREDENTIAL_STATUSES: frozenset[str] = frozenset({_pt.BAD_KEY, _pt.NO_CREDITS})
#: Failures that clear on their own; they expire instead of lingering.
TRANSIENT_STATUSES: frozenset[str] = frozenset({_pt.RATE_LIMITED, _pt.UNREACHABLE})
#: How long a transient failure keeps its dot.
TRANSIENT_TTL_S = 30 * 60.0
#: An unchanged outcome is re-written to disk at most this often, so its age
#: survives a restart roughly right without a write per call.
_REFRESH_WRITE_S = 15 * 60.0

# Plugin names that differ from the provider-card id they belong to. The
# OpenRouter speech plugin calls itself ``openrouter`` — the same id as the
# OpenRouter BRAIN card — while its card is ``openrouter-tts``.
_ALIASES: dict[tuple[str, str], str] = {
    (MODALITY_TTS, "openrouter"): "openrouter-tts",
    (MODALITY_TTS, "openrouter_tts"): "openrouter-tts",
    (MODALITY_TTS, "open-router-tts"): "openrouter-tts",
}


def _norm_provider(provider: str | None, modality: str) -> str:
    key = (provider or "").strip().casefold()
    return _ALIASES.get((modality, key), key)


@dataclass(frozen=True, slots=True)
class Outcome:
    """One remembered outcome. Never carries an error body or a secret."""

    provider: str
    modality: str
    status: str
    at: float
    source: str = SOURCE_USE
    model: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "modality": self.modality,
            "status": self.status,
            "at": self.at,
            "source": self.source,
            "model": self.model,
        }

    @classmethod
    def from_json(cls, raw: Any) -> Outcome | None:
        """Parse one persisted entry; anything malformed is dropped."""
        if not isinstance(raw, dict):
            return None
        provider = str(raw.get("provider") or "").strip()
        modality = str(raw.get("modality") or "")
        status = str(raw.get("status") or "")
        source = str(raw.get("source") or SOURCE_USE)
        try:
            at = float(raw.get("at"))
        except (TypeError, ValueError):
            return None
        if not provider or modality not in MODALITIES or source not in SOURCES:
            return None
        allowed = TEST_STATUSES if source == SOURCE_TEST else USE_STATUSES
        if status not in allowed:
            return None
        return cls(
            provider=provider,
            modality=modality,
            status=status,
            at=at,
            source=source,
            model=str(raw.get("model") or ""),
        )


def failure_status(error: BaseException | str | None) -> str:
    """Classify a failure into the provider-test vocabulary.

    The one classifier the Test button uses, so a real call and a test can
    never disagree about what "401" or "insufficient_quota" means.
    """
    if error is None:
        return _pt.ERROR
    if isinstance(error, BaseException):
        text = f"{type(error).__name__}: {error}"
    else:
        text = str(error)
    return _pt.classify_provider_error(text)


class ProviderHealthLedger:
    """Thread-safe record of the last real outcome per provider and modality."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._entries: dict[tuple[str, str], Outcome] = {}
        self._written_at: dict[tuple[str, str], float] = {}
        self._loaded = path is None
        self._version = 0

    # ── reading ──────────────────────────────────────────────────────────
    @property
    def version(self) -> int:
        """Bumps whenever a remembered status appears, changes or is forgotten."""
        with self._lock:
            self._ensure_loaded_locked()
            return self._version

    def get(self, provider: str, modality: str) -> Outcome | None:
        with self._lock:
            self._ensure_loaded_locked()
            return self._entries.get((_norm_provider(provider, modality), modality))

    def effective(
        self,
        provider: str,
        modality: str,
        *,
        now: float | None = None,
    ) -> Outcome | None:
        """The outcome that should colour ``provider``'s ``modality`` right now.

        * a transient failure older than :data:`TRANSIENT_TTL_S` is ignored;
        * the key is shared by every modality of the same provider id, so a
          credential failure recorded on ANOTHER modality wins when it is newer
          than this modality's own outcome — and this modality's own credential
          failure is void once a sibling answered after it;
        * otherwise this modality's own outcome, or ``None`` (never used yet).
        """
        current = self._clock() if now is None else now
        key_provider = _norm_provider(provider, modality)
        with self._lock:
            self._ensure_loaded_locked()
            own = self._entries.get((key_provider, modality))
            siblings = [
                outcome
                for (p, m), outcome in self._entries.items()
                if p == key_provider and m != modality
            ]
        if own is not None and self._expired(own, current):
            own = None
        newest_credential: Outcome | None = None
        newest_ok: Outcome | None = None
        for sibling in siblings:
            if sibling.status in CREDENTIAL_STATUSES:
                if newest_credential is None or sibling.at > newest_credential.at:
                    newest_credential = sibling
            elif sibling.status == _pt.OK:
                if newest_ok is None or sibling.at > newest_ok.at:
                    newest_ok = sibling
        if newest_credential is not None and (own is None or newest_credential.at > own.at):
            if newest_ok is None or newest_credential.at > newest_ok.at:
                return newest_credential
        if (
            own is not None
            and own.status in CREDENTIAL_STATUSES
            and newest_ok is not None
            and newest_ok.at > own.at
        ):
            return None
        return own

    def snapshot(self) -> list[Outcome]:
        with self._lock:
            self._ensure_loaded_locked()
            return list(self._entries.values())

    # ── writing ──────────────────────────────────────────────────────────
    def record(
        self,
        provider: str,
        modality: str,
        status: str,
        *,
        source: str = SOURCE_USE,
        model: str | None = None,
    ) -> bool:
        """Remember one outcome. Returns whether it was recorded.

        Never raises: a health record must not cost the call it describes.
        """
        if modality not in MODALITIES or source not in SOURCES:
            return False
        allowed = TEST_STATUSES if source == SOURCE_TEST else USE_STATUSES
        if status not in allowed:
            return False
        key_provider = _norm_provider(provider, modality)
        if not key_provider:
            return False
        now = self._clock()
        outcome = Outcome(
            provider=key_provider,
            modality=modality,
            status=status,
            at=now,
            source=source,
            model=(model or "").strip(),
        )
        key = (key_provider, modality)
        with self._lock:
            self._ensure_loaded_locked()
            previous = self._entries.get(key)
            self._entries[key] = outcome
            changed = previous is None or previous.status != status
            if changed:
                self._version += 1
            due = changed or now - self._written_at.get(key, 0.0) >= _REFRESH_WRITE_S
            if due:
                self._written_at[key] = now
            payload = self._payload_locked() if due else None
        if changed:
            log.info(
                "Provider health: %s/%s %s -> %s (%s)",
                key_provider,
                modality,
                previous.status if previous is not None else "none",
                status,
                source,
            )
        if payload is not None:
            self._persist(payload)
        return True

    def record_failure(
        self,
        provider: str,
        modality: str,
        error: BaseException | str | None,
        *,
        source: str = SOURCE_USE,
        model: str | None = None,
    ) -> bool:
        """Classify ``error`` and remember it when it is evidence (see module doc)."""
        return self.record(
            provider, modality, failure_status(error), source=source, model=model
        )

    def forget(
        self,
        providers: Iterable[str],
        *,
        modality: str | None = None,
        keep_credential_failures: bool = False,
    ) -> int:
        """Drop outcomes of ``providers`` (optionally one ``modality``).

        ``keep_credential_failures`` keeps ``bad_key`` / ``no_credits``: a model
        change says nothing about whether the key works.
        """
        wanted: set[str] = set()
        for provider in providers:
            for m in MODALITIES:
                normalized = _norm_provider(provider, m)
                if normalized:
                    wanted.add(normalized)
        if not wanted:
            return 0
        with self._lock:
            self._ensure_loaded_locked()
            doomed = [
                key
                for key, outcome in self._entries.items()
                if key[0] in wanted
                and (modality is None or key[1] == modality)
                and not (keep_credential_failures and outcome.status in CREDENTIAL_STATUSES)
            ]
            for key in doomed:
                self._entries.pop(key, None)
                self._written_at.pop(key, None)
            if doomed:
                self._version += 1
            payload = self._payload_locked() if doomed else None
        if payload is not None:
            self._persist(payload)
        return len(doomed)

    # ── internals ────────────────────────────────────────────────────────
    @staticmethod
    def _expired(outcome: Outcome, now: float) -> bool:
        return outcome.status in TRANSIENT_STATUSES and now - outcome.at > TRANSIENT_TTL_S

    def _payload_locked(self) -> dict[str, Any]:
        return {
            "version": _FILE_VERSION,
            "outcomes": [outcome.to_json() for outcome in self._entries.values()],
        }

    def _ensure_loaded_locked(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        path = self._path
        if path is None:
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            # A damaged record only costs the history; the next real call
            # writes a fresh one.
            log.info("Provider health: %s unreadable, starting empty", path, exc_info=True)
            return
        items = raw.get("outcomes") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            return
        for item in items:
            outcome = Outcome.from_json(item)
            if outcome is None:
                continue
            key = (outcome.provider, outcome.modality)
            self._entries[key] = outcome
            self._written_at[key] = outcome.at
        if self._entries:
            self._version += 1

    def _persist(self, payload: dict[str, Any]) -> None:
        path = self._path
        if path is None:
            return
        with self._write_lock:
            temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                os.replace(temporary, path)
            except OSError:
                log.warning("Provider health: %s not written", path, exc_info=True)
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    log.debug("Provider health: temporary file cleanup failed", exc_info=True)


# ── process-wide instance ────────────────────────────────────────────────────
_ledger: ProviderHealthLedger | None = None
_ledger_guard = threading.Lock()


def ledger_path() -> Path:
    """``DATA_DIR/state/provider_health.json`` — resolved at call time."""
    from jarvis.core import config as cfg_mod  # lazy: tests redirect DATA_DIR

    return Path(cfg_mod.DATA_DIR) / "state" / LEDGER_FILE_NAME


def get_ledger() -> ProviderHealthLedger:
    """The process-wide ledger, created on first use (never at import)."""
    global _ledger
    with _ledger_guard:
        if _ledger is None:
            _ledger = ProviderHealthLedger(ledger_path())
        return _ledger


def set_ledger(ledger: ProviderHealthLedger | None) -> None:
    """Swap the process-wide ledger (tests; ``None`` rebuilds on next use)."""
    global _ledger
    with _ledger_guard:
        _ledger = ledger


def _safely(action: Callable[[], Any], what: str) -> Any:
    """Run a ledger action for a caller on a live call path.

    A health record must never cost the call it describes, so a failure here
    is logged (once per occurrence, at warning) and swallowed on purpose.
    """
    try:
        return action()
    except Exception:  # noqa: BLE001 — see docstring: the call path must not pay
        log.warning("Provider health: %s failed", what, exc_info=True)
        return None


def record_success(
    provider: str | None,
    modality: str,
    *,
    source: str = SOURCE_USE,
    model: str | None = None,
) -> None:
    """A real call to ``provider`` answered."""
    if not provider:
        return
    _safely(
        lambda: get_ledger().record(provider, modality, _pt.OK, source=source, model=model),
        "record_success",
    )


def record_failure(
    provider: str | None,
    modality: str,
    error: BaseException | str | None,
    *,
    source: str = SOURCE_USE,
    model: str | None = None,
) -> None:
    """A real call to ``provider`` failed with ``error`` (classified, never stored)."""
    if not provider:
        return
    _safely(
        lambda: get_ledger().record_failure(
            provider, modality, error, source=source, model=model
        ),
        "record_failure",
    )


def record_status(
    provider: str | None,
    modality: str,
    status: str,
    *,
    source: str = SOURCE_USE,
    model: str | None = None,
) -> None:
    """Remember an already-classified outcome (a Test button verdict)."""
    if not provider:
        return
    _safely(
        lambda: get_ledger().record(provider, modality, status, source=source, model=model),
        "record_status",
    )


def forget_providers(
    providers: Iterable[str],
    *,
    modality: str | None = None,
    keep_credential_failures: bool = False,
) -> None:
    """Forget what ``providers`` did — a new key or model starts clean."""
    names = [p for p in providers if p]
    if not names:
        return
    _safely(
        lambda: get_ledger().forget(
            names, modality=modality, keep_credential_failures=keep_credential_failures
        ),
        "forget_providers",
    )


def effective_outcome(provider: str | None, modality: str) -> Outcome | None:
    """See :meth:`ProviderHealthLedger.effective`; ``None`` on any failure."""
    if not provider:
        return None
    return _safely(lambda: get_ledger().effective(provider, modality), "effective_outcome")


def ledger_version() -> int:
    """Current ledger version for cache keys; ``0`` if the ledger is unreadable."""
    version = _safely(lambda: get_ledger().version, "ledger_version")
    return int(version or 0)


def modality_for_tier(tier: str | None) -> str | None:
    """The modality a provider-card tier's calls are recorded under.

    ``None`` for a tier this ledger does not track (the dictation wording pass
    is judged by credential presence alone).
    """
    return {
        "brain": MODALITY_BRAIN,
        "tts": MODALITY_TTS,
        "stt": MODALITY_STT,
        "realtime": MODALITY_REALTIME,
    }.get((tier or "").strip().lower())


__all__ = [
    "CREDENTIAL_STATUSES",
    "LEDGER_FILE_NAME",
    "MODALITIES",
    "MODALITY_BRAIN",
    "MODALITY_REALTIME",
    "MODALITY_STT",
    "MODALITY_TOOL",
    "MODALITY_TTS",
    "Outcome",
    "ProviderHealthLedger",
    "SOURCE_TEST",
    "SOURCE_USE",
    "TEST_STATUSES",
    "TRANSIENT_STATUSES",
    "TRANSIENT_TTL_S",
    "USE_STATUSES",
    "effective_outcome",
    "failure_status",
    "forget_providers",
    "get_ledger",
    "ledger_path",
    "ledger_version",
    "modality_for_tier",
    "record_failure",
    "record_status",
    "record_success",
    "set_ledger",
]
