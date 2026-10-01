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
  :mod:`jarvis.brain.provider_test`), the time it happened, the model when the
  caller knew it, and whether it came from normal use or an explicit test;
* for a credential failure, a short one-way fingerprint of the credential it
  was judged against, so a key replaced in another process (the terminal setup
  wizard, an external keyring edit) voids the stale verdict.

What is never kept: a provider's error body, a key, a prompt, a transcript
(AP-34). A failure is classified the moment it is reported and only the
classification survives.

Rules the readers rely on:

* A failure of normal use counts only when it is recognisably the provider's
  answer — an HTTP status in the message, a transport failure, or a provider
  SDK error type. Marker words alone ("credit", "network") also appear in tool
  and app errors, and a false ``no_credits`` would never expire.
* An unrecognised failure (``error``) from normal use is not recorded; the
  explicit Test button's verdict is recorded whatever it says.
* ``bad_key`` / ``no_credits`` are failures of the CREDENTIAL, which every
  modality of the same provider id shares (see :meth:`ProviderHealthLedger.effective`).
* Transient outcomes (``rate_limited``, ``unreachable``) age out after
  :data:`TRANSIENT_TTL_S`.
* Any credential write or delete in this process forgets what the providers
  reading that slot did (wired by the web layer through
  ``jarvis.core.config.add_secret_change_listener``); changing a model forgets
  the model-dependent outcomes of that modality.

Every status change is announced to registered listeners (the web server turns
it into a ``ProviderHealthChanged`` bus event, which reaches every open window
over the existing WebSocket). The record lives in memory and in
``DATA_DIR/state/provider_health.json`` so the dots survive a restart. The
process-wide instance reads and writes that file on a background thread and
writes only when an outcome CHANGES (or an unchanged one is older than
:data:`_REFRESH_WRITE_S` on disk), so the voice path pays a dict update and
nothing else (AP-9). Nothing initialises at import time (AP-26).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
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

# ── What makes a failure the provider's own answer ───────────────────────────
_HTTP_STATUS_RE = re.compile(r"\b[45]\d\d\b")
#: Name fragments of the exception types provider SDKs and HTTP/WebSocket
#: transports raise (OpenAI-compatible, Anthropic, Google GenAI, httpx,
#: websockets). Matched by name so no SDK is imported here (AP-28).
_PROVIDER_ERROR_TYPE_TOKENS: tuple[str, ...] = (
    "APIStatus",
    "APIConnection",
    "APITimeout",
    "Authentication",
    "PermissionDenied",
    "RateLimit",
    "InternalServer",
    "ServiceUnavailable",
    "ClientError",
    "ServerError",
    "ResourceExhausted",
    "HTTPStatus",
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "RemoteProtocol",
    "ConnectionClosed",
    "InvalidStatus",
)


def _norm_provider(provider: str | None, modality: str) -> str:
    key = (provider or "").strip().casefold()
    return _ALIASES.get((modality, key), key)


def credential_fingerprint(credential: str | None) -> str:
    """A short one-way fingerprint of a credential ("" when there is none).

    Domain-separated and truncated: enough to notice that the key changed,
    useless for recovering it. Never the credential itself.
    """
    if not credential:
        return ""
    digest = hashlib.sha256(b"jarvis-provider-health\x00" + credential.encode("utf-8"))
    return digest.hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class Outcome:
    """One remembered outcome. Never carries an error body or a secret."""

    provider: str
    modality: str
    status: str
    at: float
    source: str = SOURCE_USE
    model: str = ""
    #: :func:`credential_fingerprint` of the key a credential failure was
    #: judged against; stamped on first read, "" until then.
    credential: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "modality": self.modality,
            "status": self.status,
            "at": self.at,
            "source": self.source,
            "model": self.model,
            "credential": self.credential,
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
        except (TypeError, ValueError):  # a malformed entry is dropped, never fatal
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
            credential=str(raw.get("credential") or ""),
        )


def failure_status(error: BaseException | str | None, *, strict: bool = False) -> str:
    """Classify a failure into the provider-test vocabulary.

    The one classifier the Test button uses, so a real call and a test can
    never disagree about what "401" or "insufficient_quota" means.

    ``strict`` (normal use) returns ``error`` — "not evidence" — unless the
    failure is recognisably the provider's own answer: an HTTP status in the
    message, a timeout/connection failure, or a provider SDK/transport error
    type. Marker words alone also occur in tool and app errors.
    """
    if error is None:
        return _pt.ERROR
    if isinstance(error, BaseException):
        text = f"{type(error).__name__}: {error}"
    else:
        text = str(error)
    status = _pt.classify_provider_error(text)
    if not strict or _HTTP_STATUS_RE.search(text):
        return status
    if isinstance(error, BaseException):
        if isinstance(error, TimeoutError | ConnectionError):
            return _pt.UNREACHABLE
        names = [cls.__name__ for cls in type(error).__mro__]
        if any(token in name for name in names for token in _PROVIDER_ERROR_TYPE_TOKENS):
            return status
    return _pt.ERROR


ChangeListener = Callable[[str, str, str], None]


class ProviderHealthLedger:
    """Thread-safe record of the last real outcome per provider and modality.

    ``background_io=False`` (tests, tools) reads the file on first access and
    writes synchronously. ``background_io=True`` (the process-wide instance)
    never touches the disk on the caller's thread: :meth:`start_loading` reads
    it on a worker thread and merges it in (a newer in-memory outcome wins),
    and writes go to a single background writer in order.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        clock: Callable[[], float] = time.time,
        background_io: bool = False,
    ) -> None:
        self._path = path
        self._clock = clock
        self._background = background_io and path is not None
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._entries: dict[tuple[str, str], Outcome] = {}
        self._written_at: dict[tuple[str, str], float] = {}
        self._loaded = path is None
        self._load_started = False
        self._load_done = threading.Event()
        if self._loaded:
            self._load_done.set()
        self._version = 0
        self._write_seq = 0
        self._written_seq = 0
        self._executor: ThreadPoolExecutor | None = None
        self._listeners: list[ChangeListener] = []

    # ── listeners ────────────────────────────────────────────────────────
    def add_listener(self, listener: ChangeListener) -> None:
        """``listener(provider, modality, status)`` after every status change.

        All three are "" for a bulk change. Called outside the ledger lock, on
        whichever thread recorded the outcome; it must be cheap and must not
        raise (a failing one is logged and skipped).
        """
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def _notify(self, provider: str, modality: str, status: str) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(provider, modality, status)
            except Exception:  # noqa: BLE001 — a follower must never cost the call
                log.warning("Provider health: change listener failed", exc_info=True)

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

    def has_credential_failure(self, provider: str) -> bool:
        """Whether any modality of ``provider`` holds a key-level failure."""
        with self._lock:
            self._ensure_loaded_locked()
            return any(
                outcome.status in CREDENTIAL_STATUSES
                for (p, m), outcome in self._entries.items()
                if p == _norm_provider(provider, m)
            )

    def effective(
        self,
        provider: str,
        modality: str,
        *,
        now: float | None = None,
        credential: str | None = None,
    ) -> Outcome | None:
        """The outcome that should colour ``provider``'s ``modality`` right now.

        * a transient failure older than :data:`TRANSIENT_TTL_S` is ignored;
        * the key is shared by every modality of the same provider id, so a
          credential failure recorded on ANOTHER modality wins when it is newer
          than this modality's own outcome — and this modality's own credential
          failure is void once a sibling answered after it;
        * ``credential`` (the current key's :func:`credential_fingerprint`,
          when the caller read it) stamps a credential failure that has none
          yet and voids one judged against a different key;
        * otherwise this modality's own outcome, or ``None`` (never used yet).
        """
        current = self._clock() if now is None else now
        key_provider = _norm_provider(provider, modality)
        voided = False
        stamped = False
        with self._lock:
            self._ensure_loaded_locked()
            if credential:
                for key, outcome in list(self._entries.items()):
                    if key[0] != key_provider or outcome.status not in CREDENTIAL_STATUSES:
                        continue
                    if not outcome.credential:
                        self._entries[key] = replace(outcome, credential=credential)
                        stamped = True
                    elif outcome.credential != credential:
                        self._entries.pop(key, None)
                        self._written_at.pop(key, None)
                        voided = True
                if voided:
                    self._version += 1
            own = self._entries.get((key_provider, modality))
            siblings = [
                outcome
                for (p, m), outcome in self._entries.items()
                if p == key_provider and m != modality
            ]
        if voided or stamped:
            self._request_write()
        if voided:
            self._notify(key_provider, "", "")
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
            if (
                previous is not None
                and previous.status == status
                and previous.credential
                and status in CREDENTIAL_STATUSES
            ):
                # The same verdict on the same (already fingerprinted) key.
                outcome = replace(outcome, credential=previous.credential)
            self._entries[key] = outcome
            changed = previous is None or previous.status != status
            if changed:
                self._version += 1
            due = changed or now - self._written_at.get(key, 0.0) >= _REFRESH_WRITE_S
            if due:
                self._written_at[key] = now
        if changed:
            log.info(
                "Provider health: %s/%s %s -> %s (%s)",
                key_provider,
                modality,
                previous.status if previous is not None else "none",
                status,
                source,
            )
        if due:
            self._request_write()
        if changed:
            self._notify(key_provider, modality, status)
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
        status = failure_status(error, strict=source == SOURCE_USE)
        return self.record(provider, modality, status, source=source, model=model)

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
        if doomed:
            self._request_write()
            self._notify("", "", "")
        return len(doomed)

    # ── disk ─────────────────────────────────────────────────────────────
    def start_loading(self) -> None:
        """Read the file on a worker thread (background mode; idempotent)."""
        if not self._background:
            return
        with self._lock:
            if self._loaded or self._load_started:
                return
            self._load_started = True
        threading.Thread(
            target=self._load_in_background, name="provider-health-load", daemon=True
        ).start()

    def flush(self, timeout: float = 30.0) -> None:
        """Wait until the background loader and every queued write are done."""
        self._load_done.wait(timeout)
        executor = self._executor
        if executor is not None:
            executor.submit(lambda: None).result(timeout)

    def _load_in_background(self) -> None:
        added = False
        pending_write = False
        try:
            outcomes = self._read_file()
            with self._lock:
                added = self._merge_locked(outcomes)
                self._loaded = True
                pending_write = self._write_seq > self._written_seq
        finally:
            self._load_done.set()
        if added:
            self._notify("", "", "")
        if added and pending_write:
            # A change recorded while loading was written without the file's
            # history only if its writer gave up waiting; write the merge.
            self._request_write()

    def _ensure_loaded_locked(self) -> None:
        if self._loaded or self._background:
            # Background mode never reads on the caller's thread; the loader
            # merges the file in when it is done.
            return
        self._loaded = True
        self._merge_locked(self._read_file())
        self._load_done.set()

    def _merge_locked(self, outcomes: list[Outcome]) -> bool:
        added = False
        for outcome in outcomes:
            key = (outcome.provider, outcome.modality)
            if key in self._entries:
                continue  # a newer in-memory outcome wins over the file
            self._entries[key] = outcome
            self._written_at[key] = outcome.at
            added = True
        if added:
            self._version += 1
        return added

    def _read_file(self) -> list[Outcome]:
        path = self._path
        if path is None:
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:  # no record yet: a fresh install starts empty
            return []
        except (OSError, ValueError):
            # A damaged record only costs the history; the next real call
            # writes a fresh one.
            log.info("Provider health: %s unreadable, starting empty", path, exc_info=True)
            return []
        items = raw.get("outcomes") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            return []
        return [outcome for item in items if (outcome := Outcome.from_json(item)) is not None]

    def _request_write(self) -> None:
        if self._path is None:
            return
        with self._lock:
            self._write_seq += 1
        if not self._background:
            self._write_latest()
            return
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="provider-health-write"
                )
            executor = self._executor
        executor.submit(self._write_latest)

    def _write_latest(self) -> None:
        """Write the CURRENT state, and never an older one over a newer one."""
        if self._background and not self._load_done.wait(30.0):
            # Writing before the file was merged in would drop its history;
            # the next change writes once the loader has finished.
            log.info("Provider health: file not loaded yet, write deferred")
            return
        with self._write_lock:
            with self._lock:
                seq = self._write_seq
                payload = {
                    "version": _FILE_VERSION,
                    "outcomes": [outcome.to_json() for outcome in self._entries.values()],
                }
            if seq <= self._written_seq:
                return
            self._write_file(payload)
            self._written_seq = seq

    def _write_file(self, payload: dict[str, Any]) -> None:
        path = self._path
        if path is None:
            return
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

    @staticmethod
    def _expired(outcome: Outcome, now: float) -> bool:
        return outcome.status in TRANSIENT_STATUSES and now - outcome.at > TRANSIENT_TTL_S


# ── process-wide instance ────────────────────────────────────────────────────
_ledger: ProviderHealthLedger | None = None
_ledger_guard = threading.Lock()
_global_listeners: list[ChangeListener] = []


def ledger_path() -> Path:
    """``DATA_DIR/state/provider_health.json`` — resolved at call time."""
    from jarvis.core import config as cfg_mod  # lazy: tests redirect DATA_DIR

    return Path(cfg_mod.DATA_DIR) / "state" / LEDGER_FILE_NAME


def get_ledger() -> ProviderHealthLedger:
    """The process-wide ledger, created on first use (never at import)."""
    global _ledger
    with _ledger_guard:
        created = _ledger is None
        if _ledger is None:
            _ledger = ProviderHealthLedger(ledger_path(), background_io=True)
            for listener in _global_listeners:
                _ledger.add_listener(listener)
        ledger = _ledger
    if created:
        ledger.start_loading()
    return ledger


def set_ledger(ledger: ProviderHealthLedger | None) -> None:
    """Swap the process-wide ledger (tests; ``None`` rebuilds on next use)."""
    global _ledger
    with _ledger_guard:
        _ledger = ledger
        if ledger is not None:
            for listener in _global_listeners:
                ledger.add_listener(listener)


def add_change_listener(listener: ChangeListener) -> None:
    """Register ``listener`` on the process-wide ledger, now and after a swap."""
    with _ledger_guard:
        if listener not in _global_listeners:
            _global_listeners.append(listener)
        ledger = _ledger
    if ledger is not None:
        ledger.add_listener(listener)


def publish_changes_to(bus: Any, loop: asyncio.AbstractEventLoop) -> ChangeListener:
    """Announce every status change as a ``ProviderHealthChanged`` bus event.

    The bus forwards every event to every open window over the ONE existing
    WebSocket, so the dots re-read without a poll and without a new socket
    (AP-33). Thread-safe: an outcome recorded on a worker thread is handed to
    ``loop``. Returns the registered listener.
    """
    from jarvis.core.events import ProviderHealthChanged

    pending: set[asyncio.Future[Any]] = set()

    def _publish(event: Any) -> None:
        task = asyncio.ensure_future(bus.publish(event))
        pending.add(task)
        task.add_done_callback(pending.discard)

    def _listener(provider: str, modality: str, status: str) -> None:
        event = ProviderHealthChanged(
            source_layer="brain.provider_health",
            provider=provider,
            modality=modality,
            status=status,
        )
        try:
            loop.call_soon_threadsafe(_publish, event)
        except RuntimeError:
            # The loop is closed (shutdown): nobody is left to tell.
            log.debug("Provider health: event loop closed, change not announced")

    add_change_listener(_listener)
    return _listener


def _safely(action: Callable[[], Any], what: str) -> Any:
    """Run a ledger action for a caller on a live call path.

    A health record must never cost the call it describes, so a failure here
    is logged and swallowed on purpose.
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


def effective_outcome(
    provider: str | None, modality: str, *, credential: str | None = None
) -> Outcome | None:
    """See :meth:`ProviderHealthLedger.effective`; ``None`` on any failure."""
    if not provider:
        return None
    return _safely(
        lambda: get_ledger().effective(provider, modality, credential=credential),
        "effective_outcome",
    )


def has_credential_failure(provider: str | None) -> bool:
    if not provider:
        return False
    return bool(_safely(lambda: get_ledger().has_credential_failure(provider), "lookup"))


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
    "add_change_listener",
    "credential_fingerprint",
    "effective_outcome",
    "failure_status",
    "forget_providers",
    "get_ledger",
    "has_credential_failure",
    "ledger_path",
    "ledger_version",
    "modality_for_tier",
    "publish_changes_to",
    "record_failure",
    "record_status",
    "record_success",
    "set_ledger",
]
