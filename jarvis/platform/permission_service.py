"""Just-in-time macOS permissions: ask when a feature first needs the access.

This is the layer between the features (voice, dictation, computer use, screen
capture, global shortcuts, audio ducking) and ``SystemPermissionPort``, the OS
adapter. A feature calls :meth:`PermissionService.ensure` at the moment a user
gesture needs a permission. The service asks macOS through the port, never
refuses an action because OUR OWN preflight said "not granted" before the OS was
asked, and never lets an action proceed on anything but a live grant
(``EnsureResult.granted`` is true only for GRANTED and NOT_REQUIRED). A denial
degrades that one feature honestly; nothing is asked at launch and nothing nags.

THREADING RULE (read before calling)

* The tap callback, any event-loop callback and any Tk callback call
  :meth:`check` only. It is lock-free, silent, never prompts and never publishes.
* ``ensure(wait_s > 0)`` blocks the calling thread: call it from a WORKER thread
  only. A loop caller uses :meth:`ensure_async`, which makes ONE ``to_thread``
  call for the native request and then ``await asyncio.sleep`` polls, so a wait
  never pins an executor worker; or it passes ``wait_s=0`` (the request is made,
  the answer arrives later through :class:`PermissionResolved`). Even
  ``ensure(wait_s=0)`` from a loop thread stays cheap: it never runs the
  window-title oracle there and never waits for an Automation probe.
* Nothing here raises for a native failure: every one becomes UNAVAILABLE with a
  fixed-template sentence and a debug log. The one exception is a programming
  error, an unknown permission id (``ValueError``).
* The service lock guards ONLY dictionary mutation. A native call, a poll, a
  publish, a listener call and a log line never run under it.
* Automation is the one permission whose state read can HANG (Apple forums
  thread 666528: ``AEDeterminePermissionToAutomateTarget`` for a running player
  without a window) and whose request blocks for as long as a dialog is open. Both
  therefore run on daemon threads: every Automation read goes through
  :class:`_AutomationGuard` (a hard timeout, one call in flight per player, a
  quarantine after a timeout), and the Automation request runs on its own daemon
  thread, so ``ensure`` answers PENDING at once and the answer arrives through
  :class:`PermissionResolved`. On an event-loop thread an Automation read never
  waits at all.

EPISODES

An episode is one coalesced "feature X is waiting on permissions Y". A feature
has at most one open record per permission regardless of ``wait_s``: a call that
overlaps an open episode of the same feature joins it (so granting one member of a
coalesced pair never opens a second record), and a retry loop that re-enters inside
an episode gets PENDING with ``asked=False`` and never a second native request.
At most ONE native request is made per permission per episode, and a per-process
cooldown (Accessibility and Input Monitoring: once per 10 minutes, Screen
Recording: once per process, the OS dialog class: while a dialog may still be
open) covers the retries across episodes. An explicit click (``force_ask``) skips
that cooldown and the per-episode rule for the PROMPT-ONCE permissions, never a
denial and never a DIALOG-class dialog that may still be open. There is NO
persisted "asked" memory: it would resurrect the dead end after ``tccutil reset``, a
re-sign or another bundle id (BUG-083). An episode ends when every permission it
needs is granted (``PermissionResolved(granted=True)``), or when nobody touched it
for ten minutes (``PermissionResolved(granted=False)``: it is closed unresolved, the
permission was still not granted at the last read).

The EPISODE WATCHER (an asyncio task when :meth:`attach_bus` gave the service a
loop, else one daemon thread) polls the open permissions every two seconds,
publishes the edges, calls the listeners and stops when nothing is open. Listeners
(:meth:`add_listener`) are zero-argument callables run when ONE permission turns
granted inside an open episode. With a loop attached they run ON that loop's
thread (so a consumer can set an ``asyncio.Event``); without one they run on the
thread that observed the edge. They must be cheap and must not raise.

Boot: construction does no I/O, no pyobjc/framework is imported here and the
process singleton is lazy (AP-26). Off macOS ``ensure`` returns NOT_REQUIRED
before the port is asked for anything. Every macOS behaviour that Apple does not
document stays labelled unverified where it is relied on.
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final
from uuid import UUID, uuid4

from jarvis.core.events import (
    PERMISSION_FEATURES,
    PERMISSION_NEEDED_ORIGINS,
    PermissionNeeded,
    PermissionResolved,
)
from jarvis.platform import permissions as _permissions_module
from jarvis.platform.permissions import (
    APP_NAME,
    AUTOMATION_TARGETS,
    PANE_FAMILY,
    REQUEST_CLASS,
    SETTINGS_PATH_TEXT,
    PermissionId,
    PermissionState,
    RequestClass,
)

log = logging.getLogger(__name__)

_SOURCE_LAYER: Final = "platform.permission_service"

# check() cache. A grant is cached briefly so a hot path (a capture loop, the
# watchdog tick) does not pay a native read per call; a negative is cached for a
# quarter of a second only, so a grant the user just gave is seen almost at once.
_GRANTED_TTL_S: Final = 1.0
_NEGATIVE_TTL_S: Final = 0.25

# How often a waiting ensure() re-reads the state (the contract says 250 ms).
_POLL_S: Final = 0.25
# How often the episode watcher re-reads the open permissions.
_WATCH_INTERVAL_S: Final = 2.0
# An episode nobody touched for this long is closed without a grant.
_EPISODE_TTL_S: Final = 600.0
# A PROMPT-ONCE dialog only offers "Open System Settings" (community-observed,
# UNVERIFIED): once this long has passed since the request (or the app was
# refocused) and the switch is still off, the user is "blocked" on Settings and
# the app may show its own card. Both the 15 s and the 130 s below are our own
# figures, not Apple's.
_BLOCKED_AFTER_S: Final = 15.0
# A DIALOG-class dialog (microphone, automation) that has been open this long
# without an answer stops being "macOS is asking" and turns "blocked", so a
# dialog the user ignored never leaves the feature silently dead.
_DIALOG_CEILING_S: Final = 130.0

# The Screen Recording window-title oracle enumerates the on-screen windows. It
# runs on a gesture entry (an interactive ensure) and, for an episode a gesture
# opened, at most this often (or at once after an app refocus). Whether the
# enumeration itself can trigger the macOS 15 "bypass the system picker" alert is
# UNVERIFIED, hence the low cadence; the shallow preflight is read every pass.
_ORACLE_EVERY_S: Final = 10.0

# Automation reads: a hard timeout for one Apple Event probe, and how long a
# target that did not answer is left alone afterwards. A watcher pass or a poll
# waits only this long for a probe (a hung one must not stall the other
# permissions), picking the answer up on the next pass.
_AUTOMATION_READ_TIMEOUT_S: Final = 5.0
_AUTOMATION_QUARANTINE_S: Final = 600.0
_AUTOMATION_REFRESH_WAIT_S: Final = 0.25

# After a native request FAILED (the call raised or the symbol was missing) a retry
# loop must not hammer it: the next automatic attempt waits this long. An explicit
# click is never held back, and a failure is not "asked once per process" any more.
_FAILED_RETRY_S: Final = 30.0

# Per-process cooldown between two native requests for one permission. The OS
# dialog class keeps a request "in flight" for as long as its dialog may be open
# (the Automation runner is killed after 120 s); Accessibility's prompt call can
# re-show while untrusted, so we rate-limit it ourselves; Screen Recording shows
# its request at most once per process (community-observed, UNVERIFIED: Apple
# documents only the declaration of ``CGRequestScreenCaptureAccess``).
_REASK_AFTER_S: Final[dict[PermissionId, float]] = {
    PermissionId.MICROPHONE: 120.0,
    PermissionId.AUTOMATION: 120.0,
    PermissionId.SCREEN_RECORDING: math.inf,
    PermissionId.ACCESSIBILITY: 600.0,
    PermissionId.INPUT_MONITORING: 600.0,
}

# The permissions whose grant may only work after a restart, so a real failed
# attempt can produce the ``restart_hint`` reason (see ``report_failed_use``).
# Automation is deliberately NOT here: Apple Events are checked per send, so its
# failed-use report (-1743 after a granted read) is ``needs_settings``, a different
# reason with a different way out (``report_use_ok``).
_RESTART_HINT_FAMILIES: Final = frozenset(
    {PermissionId.SCREEN_RECORDING, PermissionId.INPUT_MONITORING}
)

_READY_STATES: Final = frozenset({PermissionState.GRANTED, PermissionState.NOT_REQUIRED})
_UNDECIDED_STATES: Final = frozenset({PermissionState.NOT_DETERMINED, PermissionState.NOT_GRANTED})
_DECIDED_STATES: Final = frozenset(
    {PermissionState.GRANTED, PermissionState.DENIED, PermissionState.RESTRICTED}
)
_ASKABLE_CLASSES: Final = frozenset({RequestClass.DIALOG, RequestClass.PROMPT_ONCE})

# The reason shown for a coalesced episode when its permissions disagree: the
# first one in this order wins (the most final, least actionable-by-us first).
_REASON_PRIORITY: Final = (
    "restricted",
    "unavailable",
    "denied",
    "needs_settings",
    "restart_hint",
    "not_determined",
)

_LABELS: Final[dict[PermissionId, str]] = {
    PermissionId.MICROPHONE: "Microphone",
    PermissionId.SCREEN_RECORDING: "Screen Recording",
    PermissionId.ACCESSIBILITY: "Accessibility",
    PermissionId.INPUT_MONITORING: "Input Monitoring",
    PermissionId.EVENT_POSTING: "Accessibility",
    PermissionId.AUTOMATION: "Automation",
    PermissionId.CREDENTIAL_STORE: "Keychain",
}


# ----------------------------------------------------------------------
# Public result types
# ----------------------------------------------------------------------


class PermissionOutcome(StrEnum):
    """What an ``ensure`` call can tell its caller.

    ``GRANTED`` / ``NOT_REQUIRED``
        The caller may proceed. Nothing else ever lets it proceed.
    ``PENDING``
        Not decided yet and nothing blocks it for good: macOS may be showing its
        dialog right now, or the feature has not been allowed to ask yet.
    ``NEEDS_SETTINGS``
        The user has to flip a switch in System Settings (or confirm the grantee
        when Jarvis runs outside an installed app).
    ``DENIED``
        The user answered no. macOS will not ask again by itself.
    ``UNAVAILABLE``
        It cannot be asked for here (restricted by policy, no GUI session, a
        missing framework or usage string, a failed native call).
    """

    GRANTED = "granted"
    PENDING = "pending"
    DENIED = "denied"
    NEEDS_SETTINGS = "needs_settings"
    UNAVAILABLE = "unavailable"
    NOT_REQUIRED = "not_required"


_PROCEED_OUTCOMES: Final = frozenset({PermissionOutcome.GRANTED, PermissionOutcome.NOT_REQUIRED})


@dataclass(frozen=True, slots=True)
class EnsureResult:
    """The answer of one ``ensure`` call for one permission.

    ``agent_detail`` is for an LLM tool error: prohibitive, starts with
    ``[permission_needed:<permission>] `` and tells the model not to answer a
    system dialog itself and not to retry (P9). ``user_detail`` is for people and
    the UI: a full English sentence from fixed templates only, never exception
    text, a path or a window title. Both are empty when the call may proceed.

    ``reason``, ``can_prompt`` and ``can_open_settings`` mirror what the
    ``PermissionNeeded`` event says, for a caller (a route answer, an inline
    note) that has no bus. ``reason`` is empty when the call may proceed.
    """

    permission: PermissionId
    outcome: PermissionOutcome
    state: PermissionState
    asked: bool
    outside_installed_app: bool
    agent_detail: str
    user_detail: str
    reason: str = ""
    can_prompt: bool = False
    can_open_settings: bool = False
    target: str = ""

    @property
    def granted(self) -> bool:
        """``True`` only for GRANTED and NOT_REQUIRED: the one way to proceed."""
        return self.outcome in _PROCEED_OUTCOMES


@dataclass(frozen=True, slots=True)
class Episode:
    """A read-only copy of one open episode (the server-side truth a UI hydrates from)."""

    permissions: tuple[str, ...]
    feature: str
    reason: str
    phase: str
    origin: str
    target: str
    can_prompt: bool
    can_open_settings: bool
    outside_app: bool
    detail: str
    trace_id: str
    opened_at_ns: int

    def as_dict(self) -> dict[str, Any]:
        """A JSON-friendly copy (the ``needed`` list of the status snapshot)."""
        return {
            "permissions": list(self.permissions),
            "feature": self.feature,
            "reason": self.reason,
            "phase": self.phase,
            "origin": self.origin,
            "target": self.target,
            "can_prompt": self.can_prompt,
            "can_open_settings": self.can_open_settings,
            "outside_app": self.outside_app,
            "detail": self.detail,
            "trace_id": self.trace_id,
            "opened_at_ns": self.opened_at_ns,
        }


@dataclass(frozen=True, slots=True)
class AppInfo:
    """Who this process is to macOS, as the status snapshot reports it.

    ``stable`` says "this is the installed app bundle under an accepted bundle id":
    it decides who may RESET a permission, never who may act (design P6), and
    ``outside_installed_app`` is its inverse on macOS. ``headless`` means no
    desktop session was found. Off macOS there is no app bundle: the bundle fields
    are empty, ``stable`` and ``outside_installed_app`` are ``False``.
    """

    platform: str
    app_name: str
    bundle_id: str | None
    bundle_path: str | None
    launched_as_bundle: bool
    stable: bool
    outside_installed_app: bool
    headless: bool


# ----------------------------------------------------------------------
# Fixed-template sentences
# ----------------------------------------------------------------------


def _player_name(bundle_id: str) -> str | None:
    for name, known in AUTOMATION_TARGETS:
        if known == bundle_id:
            return name
    return None


def _subject(family: PermissionId, target: str) -> str:
    """``"Microphone access"``; ``"Automation access for Music"`` for a player."""
    label = _LABELS[family]
    if family is PermissionId.AUTOMATION:
        name = _player_name(target)
        if name is not None:
            return f"{label} access for {name}"
    return f"{label} access"


def user_detail_for(
    family: PermissionId,
    reason: str,
    *,
    target: str = "",
    asking: bool = False,
    outside_app: bool = False,
    launched_as_bundle: bool = False,
    refused_use: bool = False,
) -> str:
    """The full English sentence about the situation: built from fixed templates only.

    Never carries exception text, a filesystem path or a window title. The Settings
    path it names comes from the fixed table in the port. ``asking`` means macOS
    is showing (or may be showing) its own dialog right now. Agents read these
    sentences too (``jarvis permissions status``), so each one states a fact about
    the user's situation and none is an imperative aimed at the reader (P9).
    ``launched_as_bundle`` tells a real ``.app`` run from the wrong place (a mounted
    disk image: the grantee is Personal Jarvis itself) from a terminal or IDE run.
    ``refused_use`` (Automation, ``needs_settings`` only) says the access READS as
    allowed but a real Apple Event to the named player was refused anyway, so the
    sentence does not claim the switch is off.
    """
    family = PANE_FAMILY[family]
    subject = _subject(family, target)
    pane = SETTINGS_PATH_TEXT.get(family)
    where = f" in {pane}" if pane else ""
    if outside_app:
        if launched_as_bundle:
            return (
                "Personal Jarvis is running from outside its installed location, so "
                f"macOS may record {subject} for this copy only. The user has to confirm "
                "that to continue, or allow it in System Settings."
            )
        return (
            "Personal Jarvis is not running as an installed app, so "
            f"{subject} would be granted to the app that started it. The user has to "
            "confirm that to continue, or allow it in System Settings."
        )
    if reason == "restricted":
        return (
            f"{subject} is restricted on this Mac by a profile or a parental control, "
            "so Personal Jarvis cannot use it. This cannot be changed from the app."
        )
    if reason == "unavailable":
        return f"Personal Jarvis cannot ask for {subject} in this session."
    if reason == "denied":
        return f"{subject} is turned off for Personal Jarvis. The user has to turn it on{where}."
    if reason == "restart_hint":
        return f"{subject} may only take effect after Personal Jarvis is quit and reopened."
    if reason == "needs_settings":
        player = _player_name(target) if family is PermissionId.AUTOMATION else None
        if refused_use and player is not None:
            return (
                f"macOS refused an Apple event that Personal Jarvis sent to {player}, "
                f"although {subject} reads as allowed. The user has to check that "
                f"Personal Jarvis is switched on{where}."
            )
        if asking:
            return (
                f"macOS may be showing a dialog about {subject}. The user has to turn "
                f"Personal Jarvis on{where}, then return to the app."
            )
        return f"{subject} is off for Personal Jarvis. The user has to turn it on{where}."
    # not_determined
    if asking:
        return f"Waiting for the user to answer the macOS dialog about {subject}."
    return f"Personal Jarvis needs {subject} for this feature and has not asked for it yet."


def agent_detail_for(
    family: PermissionId,
    reason: str,
    *,
    target: str = "",
    asking: bool = False,
) -> str:
    """The prohibitive sentence for an LLM tool error (P9).

    It starts with ``[permission_needed:<permission>] `` so a consumer can map the
    prefix to a terminal "blocked on permission" state, and it forbids the three
    things a model might try: answering the system dialog itself, retrying, and
    looking for a workaround.
    """
    family = PANE_FAMILY[family]
    subject = _subject(family, target)
    if reason == "restricted":
        situation = f"{subject} is restricted by a device policy and cannot be allowed here."
    elif reason == "unavailable":
        situation = f"{subject} cannot be requested in this session."
    elif reason == "denied":
        situation = f"{subject} is turned off and the user has to turn it on in System Settings."
    elif reason == "restart_hint":
        situation = (
            f"{subject} is allowed but does not work in this running app yet; "
            "the user may have to quit and reopen Personal Jarvis."
        )
    elif asking:
        situation = f"macOS is asking the user about {subject} right now."
    else:
        situation = f"{subject} has not been allowed yet and the user has to allow it."
    return (
        f"[permission_needed:{family.value}] This action cannot run: {situation} "
        "You must not try to answer, click or dismiss any macOS system dialog yourself, "
        "you must not retry this action, and you must not look for a workaround. "
        "Tell the user that this feature needs the permission and stop."
    )


# ----------------------------------------------------------------------
# Internal records
# ----------------------------------------------------------------------

_SlotKey = tuple[PermissionId, str]
_EpisodeKey = tuple[str, tuple[tuple[str, str], ...]]


@dataclass(frozen=True, slots=True)
class _View:
    """How one permission looks right now, in the vocabulary of events and results."""

    outcome: PermissionOutcome
    reason: str
    phase: str
    can_prompt: bool
    can_open_settings: bool
    outside: bool
    # Only meaningful with ``outside``: the process IS a real ``.app``, run from the
    # wrong place, so the grantee is Personal Jarvis itself and not a terminal.
    outside_bundle: bool = False

    @property
    def asking(self) -> bool:
        return self.phase == "os_dialog"


@dataclass(slots=True)
class _Slot:
    """One permission inside an episode. Plain attribute writes; see the module docstring."""

    family: PermissionId
    target: str
    probe: PermissionId
    state: PermissionState = PermissionState.NOT_DETERMINED
    # When THIS episode made the native request (monotonic), else None.
    asked_at: float | None = None
    # True: a native request was withheld because Jarvis is not the installed app.
    outside: bool = False
    # True: the permission cannot be asked for here (no usage string, a failed
    # native call, an invalid target).
    unavailable: bool = False
    # True: the app was refocused after a PROMPT-ONCE request, so the user is
    # known to have left the dialog.
    promoted: bool = False
    notified: bool = False
    # With ``outside``: the process is a real bundle run from the wrong place.
    outside_bundle: bool = False
    # True while the Automation request runs on its own daemon thread (the dialog
    # may be open); ``ask_done`` is set when that thread has finished.
    asking: bool = False
    ask_done: threading.Event | None = None
    # True: a real attempt to USE the permission failed although the state reads
    # granted (or the user came back from Settings and it still fails), so the
    # honest advice is "quit and reopen" (:meth:`PermissionService.report_failed_use`).
    restart_hint: bool = False
    # True (Automation only): a real Apple Event to this player was refused with
    # -1743 although the probe read GRANTED (:meth:`PermissionService.report_failed_use`).
    # The probe is exactly what lied, so a granted read never clears it: only
    # :meth:`PermissionService.report_use_ok` (a later send landed), ``note_reset``
    # or the episode's ten minute TTL does.
    refused_use: bool = False
    # When the Screen Recording window-title oracle last ran for this slot.
    deep_at: float | None = None

    @property
    def key(self) -> _SlotKey:
        return (self.family, self.target)

    @property
    def failed_use(self) -> bool:
        """A real use failed although the state may read granted: the slot stays open."""
        return self.restart_hint or self.refused_use

    def refused_while(self, state: PermissionState) -> bool:
        """Whether the "refused although granted" view applies: flagged AND the probe reads granted.

        A flagged slot whose state turned denied, restricted or undecided is described
        by that state instead (the more specific and more actionable reason).
        """
        return self.refused_use and state in _READY_STATES


@dataclass(slots=True)
class _Episode:
    """An open episode. Fields change only through the service."""

    key: _EpisodeKey
    feature: str
    slots: dict[_SlotKey, _Slot]
    origin: str
    trace_id: UUID
    opened_ns: int
    touched: float
    reason: str = ""
    phase: str = ""
    can_prompt: bool = False
    can_open_settings: bool = False
    outside_app: bool = False
    detail: str = ""
    published: set[tuple[Any, ...]] = field(default_factory=set)
    closed: bool = False
    # Callers of ``_start`` that have not finished their asks yet. While it is
    # positive the episode is invisible (no event, not in ``outstanding``): its
    # slots are not claimed yet, so a classify now would say "blocked, can_prompt"
    # while the OS dialog is still being requested.
    starting: int = 0


@dataclass(slots=True)
class _Item:
    """One requested permission inside one ``ensure`` call."""

    requested: PermissionId
    family: PermissionId
    target: str
    state: PermissionState = PermissionState.NOT_DETERMINED
    asked: bool = False
    bad_target: bool = False
    slot: _Slot | None = None

    @property
    def slot_key(self) -> _SlotKey:
        return (self.family, self.target)


@dataclass(slots=True)
class _Plan:
    items: list[_Item]
    feature: str
    interactive: bool
    episode: _Episode | None = None
    # An explicit user click: skips the per-process cooldown of PROMPT-ONCE asks.
    force_ask: bool = False
    # Whether this call may run the window-title oracle (a gesture entry that is
    # not on an event-loop thread).
    deep: bool = False


@dataclass(frozen=True, slots=True)
class _GuardRead:
    """What one guarded Automation read produced.

    ``kind`` is ``"value"`` (``state`` is the port's answer), ``"pending"`` (the
    probe is still running; ``state`` is the last answer, or ``None``),
    ``"quarantined"`` (a probe hung and the target is left alone) or ``"failed"``.
    """

    kind: str
    state: PermissionState | None = None


@dataclass(slots=True)
class _GuardCall:
    done: threading.Event
    # Real time (``time.monotonic``) after which the call counts as hung.
    deadline: float


class _AutomationGuard:
    """Runs every Automation state read on a daemon thread, with a hard timeout.

    ``AEDeterminePermissionToAutomateTarget`` can hang for a running player that
    has no window (Apple forums thread 666528, a DTS engineer called it a bug), and
    a native call cannot be cancelled. So a read never runs on the caller's thread:
    at most ONE call is in flight per target (a second reader joins it), a reader
    waits at most ``wait_s`` (0 on an event loop), and a call that outlives
    ``timeout_s`` quarantines its target for ``quarantine_s``: the target reads
    "unknown" at once and no further thread is started for it, so a hung probe
    never piles up threads and never stalls the other permissions. The hung thread
    is simply abandoned; its late answer is discarded.
    """

    def __init__(
        self, *, timeout_s: float, quarantine_s: float, clock: Callable[[], float]
    ) -> None:
        self._timeout_s = timeout_s
        self._quarantine_s = quarantine_s
        self._clock = clock
        self._lock = threading.Lock()
        self._inflight: dict[str, _GuardCall] = {}
        self._quarantine: dict[str, float] = {}
        self._last: dict[str, PermissionState] = {}
        self._failed: set[str] = set()

    @property
    def timeout_s(self) -> float:
        return self._timeout_s

    def read(
        self, target: str, probe: Callable[[], PermissionState], *, wait_s: float
    ) -> _GuardRead:
        now = self._clock()
        with self._lock:
            until = self._quarantine.get(target)
            if until is not None:
                if now < until:
                    return _GuardRead("quarantined")
                del self._quarantine[target]
            call = self._inflight.get(target)
            if call is not None and call.done.is_set():
                del self._inflight[target]
                call = None
            if call is not None and time.monotonic() >= call.deadline:
                # The previous probe is still hung: do not start a second thread.
                self._quarantine_locked(target, now)
                return _GuardRead("quarantined")
            if call is None:
                call = _GuardCall(
                    done=threading.Event(), deadline=time.monotonic() + self._timeout_s
                )
                self._inflight[target] = call
                self._failed.discard(target)
                threading.Thread(
                    target=self._run,
                    args=(target, probe, call),
                    name="permission-automation-probe",
                    daemon=True,
                ).start()
        if wait_s > 0:
            call.done.wait(min(wait_s, max(0.0, call.deadline - time.monotonic())))
        with self._lock:
            if call.done.is_set():
                if self._inflight.get(target) is call:
                    del self._inflight[target]
                if target in self._failed:
                    return _GuardRead("failed")
                return _GuardRead("value", self._last.get(target))
            if time.monotonic() >= call.deadline:
                self._quarantine_locked(target, self._clock())
                return _GuardRead("quarantined")
            return _GuardRead("pending", self._last.get(target))

    def _quarantine_locked(self, target: str, now: float) -> None:
        self._quarantine[target] = now + self._quarantine_s
        self._last.pop(target, None)
        log.debug("The Automation probe for %s hung; it is left alone for a while.", target)

    def _run(self, target: str, probe: Callable[[], PermissionState], call: _GuardCall) -> None:
        try:
            state = probe()
        except Exception:  # noqa: BLE001 - the thread must never die loudly; the reader reports it
            log.debug("The Automation probe for %s failed.", target, exc_info=True)
            with self._lock:
                self._failed.add(target)
        else:
            with self._lock:
                if target not in self._quarantine:
                    self._last[target] = state
        finally:
            call.done.set()

    def forget(self) -> None:
        """Drop every record (tests and process teardown)."""
        with self._lock:
            self._inflight.clear()
            self._quarantine.clear()
            self._last.clear()
            self._failed.clear()


# ----------------------------------------------------------------------
# The service
# ----------------------------------------------------------------------


class PermissionService:
    """Ask macOS for a permission when a feature needs it. See the module docstring."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        poll_s: float = _POLL_S,
        watch_interval_s: float = _WATCH_INTERVAL_S,
        episode_ttl_s: float = _EPISODE_TTL_S,
        granted_ttl_s: float = _GRANTED_TTL_S,
        negative_ttl_s: float = _NEGATIVE_TTL_S,
        automation_timeout_s: float = _AUTOMATION_READ_TIMEOUT_S,
        automation_quarantine_s: float = _AUTOMATION_QUARANTINE_S,
        automation_refresh_wait_s: float = _AUTOMATION_REFRESH_WAIT_S,
        oracle_every_s: float = _ORACLE_EVERY_S,
        ask_runner: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        # Seams, so a test drives time without sleeping.
        self._clock = clock
        self._sleep = sleep
        self._poll_s = poll_s
        self._watch_interval_s = watch_interval_s
        self._episode_ttl_s = episode_ttl_s
        self._granted_ttl_s = granted_ttl_s
        self._negative_ttl_s = negative_ttl_s
        self._automation_refresh_wait_s = automation_refresh_wait_s
        self._oracle_every_s = oracle_every_s
        # Starts the Automation request off the calling thread (a daemon thread by
        # default); a test injects a synchronous runner to keep the answer inline.
        self._ask_runner = ask_runner if ask_runner is not None else _start_daemon_thread
        self._automation = _AutomationGuard(
            timeout_s=automation_timeout_s, quarantine_s=automation_quarantine_s, clock=clock
        )

        self._lock = threading.Lock()
        # check() cache: immutable (state, expires_at) tuples, assigned whole, so
        # a read needs no lock. ``_cache_gen`` counts invalidations: a read that
        # started before one never stores its (possibly stale) answer.
        self._cache: dict[tuple[PermissionId, str], tuple[PermissionState, float, bool]] = {}
        self._cache_gen = 0
        # True once the window-title oracle (or the preflight) proved Screen
        # Recording granted in this process. The per-process-frozen preflight can
        # only go stale NEGATIVE (BUG-161), so a proven grant must not flap back to
        # "not granted" when the 1 s cache entry expires; only a capture error
        # (:meth:`invalidate`), a reset or a deep read that finds no grant drops it.
        self._sr_proven = False
        # Ports whose ``state()`` takes no ``deep``/``target`` (a pre-JIT stub).
        self._episodes: dict[_EpisodeKey, _Episode] = {}
        # Per-process "last native request" stamps (the cooldown); never persisted.
        self._last_native: dict[_SlotKey, float] = {}
        # When a native request last FAILED, per slot (see ``_FAILED_RETRY_S``).
        self._failed_native: dict[_SlotKey, float] = {}
        self._listeners: dict[PermissionId, tuple[Callable[[], None], ...]] = {}
        # (bus, loop) as ONE tuple so a reader never sees half of an attach.
        self._sink: tuple[Any, asyncio.AbstractEventLoop] | None = None

        self._watch_running = False
        self._watch_kind = ""
        self._watch_gen = 0
        self._watch_thread: threading.Thread | None = None
        self._watch_task: asyncio.Task[None] | None = None
        self._watch_loop: asyncio.AbstractEventLoop | None = None
        self._watch_stop = threading.Event()

    # ------------------------------------------------------------ wiring

    def attach_bus(self, bus: Any | None, loop: asyncio.AbstractEventLoop | None) -> None:
        """Give the service the event bus and the loop that owns it.

        Publishing goes through ``asyncio.run_coroutine_threadsafe`` and never
        waits for the result. Without a bus the service still keeps its episode
        registry and writes a debug line, and never raises. ``attach_bus(None,
        None)`` detaches.
        """
        sink = (bus, loop) if bus is not None and loop is not None else None
        with self._lock:
            self._sink = sink

    def _port(self) -> Any:
        # Resolved per call and never cached, so a test stub applies at once.
        return _permissions_module.get_system_permission_port()

    def _call_state(
        self, port: Any, perm: PermissionId, target: str, deep: bool
    ) -> PermissionState:
        """One ``port.state`` call, in the vocabulary the port understands."""
        return PermissionState(port.state(perm, target=target or None, deep=deep))

    @staticmethod
    def _is_darwin(port: Any) -> bool:
        platform = getattr(port, "platform", None)
        if platform is None:
            from jarvis.platform import detect_platform

            platform = detect_platform()
        return platform == "darwin"

    # ------------------------------------------------------------- check

    def check(
        self, permission: PermissionId | str, *, target: str | None = None
    ) -> PermissionState:
        """The live state of one permission. Silent, lock-free, never prompts.

        Safe from the event loop, a Tk callback and an event-tap callback. A
        GRANTED answer is cached for about one second (bypassed on purpose by
        :meth:`invalidate` after a capture error), a negative for about 250 ms.
        Off macOS it answers NOT_REQUIRED without asking the port anything.

        Screen Recording reads the preflight only, but an oracle-proven grant (an
        ``ensure`` or the watcher saw it) is remembered, so it never flaps back to
        "not granted" while the frozen preflight still says so. Automation needs a
        ``target`` (without one it answers UNAVAILABLE: the aggregate read would
        probe every player); its read runs on a guarded daemon thread, answers
        UNAVAILABLE for a player whose probe hung, and on an event-loop thread never
        waits (the last answer, else NOT_DETERMINED while the probe still runs).
        """
        perm = PermissionId(permission)
        try:
            port = self._port()
            if not self._is_darwin(port):
                return PermissionState.NOT_REQUIRED
            return self._read_state(port, perm, target or "")
        except Exception:  # noqa: BLE001 - check() is called from hot paths and never raises
            log.debug("Reading the %s permission failed.", perm.value, exc_info=True)
            return PermissionState.UNAVAILABLE

    def invalidate(self, permission: PermissionId | str | None = None) -> None:
        """Forget the cached answer(s) so the next read goes to the OS.

        Call it after a capture error that suggests the grant changed (a revoked
        Screen Recording grant must not stay "granted" for another second). It also
        drops the remembered Screen Recording proof, so the next deep read decides
        again. A read already in flight when this runs never stores its answer.
        """
        self._cache_gen += 1
        if permission is None:
            self._cache = {}
            self._sr_proven = False
            return
        perm = PermissionId(permission)
        if PANE_FAMILY[perm] is PermissionId.SCREEN_RECORDING:
            self._sr_proven = False
        # dict.copy() is one C-level call, so it cannot see a concurrent insert
        # half-way (iterating list(items()) of a dict another thread mutates can
        # raise "dictionary changed size during iteration").
        snapshot = self._cache.copy()
        self._cache = {key: value for key, value in snapshot.items() if key[0] is not perm}

    def check_deep(
        self, permission: PermissionId | str, *, target: str | None = None
    ) -> PermissionState:
        """:meth:`check`, but fresh and with the Screen Recording window-title oracle.

        The oracle sees a grant the per-process-frozen preflight still denies
        (BUG-161) at the price of enumerating the on-screen windows, so this is for
        a gesture route or a worker thread, never the event loop, a Tk callback or
        an event tap. Never prompts, never raises.
        """
        perm = PermissionId(permission)
        try:
            port = self._port()
            if not self._is_darwin(port):
                return PermissionState.NOT_REQUIRED
            return self._read_state(port, perm, target or "", fresh=True, deep=True)
        except Exception:  # noqa: BLE001 - a deep read is advisory and never raises
            log.debug("Reading the %s permission (deep) failed.", perm.value, exc_info=True)
            return PermissionState.UNAVAILABLE

    def app_info(self) -> AppInfo:
        """The identity block of the status snapshot. Never prompts, never raises.

        On macOS it asks AppKit whether a desktop session exists, one window-server
        round trip: a status route calls it, a hot path does not. A port that cannot
        describe itself (a hand-written stub) reads as "no bundle, not stable".
        """
        port = self._port()
        platform = str(getattr(port, "platform", "") or "")
        if not platform:
            from jarvis.platform import detect_platform

            platform = detect_platform()
        read_identity = getattr(port, "_app_identity", None)
        try:
            if read_identity is None:
                raise AttributeError("the port has no identity reader")
            identity, headless = read_identity()
        except Exception:  # noqa: BLE001 - the status snapshot degrades to "identity unknown"
            log.debug("Reading the app identity failed.", exc_info=True)
            return AppInfo(platform, APP_NAME, None, None, False, False, False, False)
        return AppInfo(
            platform=platform,
            app_name=str(identity.app_name),
            bundle_id=identity.bundle_id,
            bundle_path=identity.bundle_path,
            launched_as_bundle=bool(identity.launched_as_bundle),
            stable=bool(identity.stable),
            outside_installed_app=platform == "darwin" and self._outside_installed_app(port),
            headless=bool(headless),
        )

    def can_request(
        self,
        permission: PermissionId | str,
        state: PermissionState | None = None,
        *,
        target: str | None = None,
    ) -> bool:
        """Whether a gesture could make macOS show something for this permission now.

        ``False`` for a permission that is already decided (granted, denied,
        restricted, unavailable) and for one with no native request to make; a
        denial is a stable state and macOS will not ask again. It does not look at
        the installed-app verdict: outside the installed app an ask is still
        possible after an explicit confirmation (:meth:`ensure` ``allow_outside_app``),
        and it does not look at the per-process cooldown either: the "Allow" button
        is an explicit click, and an explicit click (``force_ask``) skips the
        cooldown of the PROMPT-ONCE permissions, so the button never does nothing.
        """
        perm = PermissionId(permission)
        family = PANE_FAMILY[perm]
        if REQUEST_CLASS.get(family) not in _ASKABLE_CLASSES:
            return False
        port = self._port()
        if not self._is_darwin(port):
            return False
        if state is None and family is PermissionId.AUTOMATION and not target:
            # Without a player the port would read every installed one and rewrite
            # its consent record (the aggregate read); a caller names a player or
            # hands the state it already has.
            return False
        current = state if state is not None else self.check(perm, target=target)
        if current not in _UNDECIDED_STATES:
            return False
        return self._usage_string_ok(port, family)

    def _read_state(
        self,
        port: Any,
        perm: PermissionId,
        target: str,
        *,
        fresh: bool = False,
        deep: bool = False,
        automation_wait_s: float | None = None,
    ) -> PermissionState:
        """One state read, through the cache unless ``fresh``.

        ``deep`` adds the Screen Recording window-title oracle, which sees a grant
        the per-process-frozen preflight still denies (BUG-161) but enumerates the
        on-screen windows. Only a gesture entry or the watcher (never a hot path,
        never the event loop) passes it; ``check()`` never does. An oracle-proven
        grant is remembered (``_sr_proven``) so a SHALLOW read does not undo it; a
        deep read is never answered from that memory (it decides, and a deep read
        that finds no grant drops the proof).

        Automation reads go through :class:`_AutomationGuard`; ``automation_wait_s``
        is how long this call may wait for the probe (default: the guard's hard
        timeout, and nothing at all on an event-loop thread).
        """
        key = (perm, target)
        family = PANE_FAMILY[perm]
        now = self._clock()
        if family is PermissionId.AUTOMATION and not target:
            # The aggregate read probes every running player and rewrites the
            # consent record: a caller names a player.
            return PermissionState.UNAVAILABLE
        if not fresh:
            entry = self._cache.get(key)
            # A shallow negative must not answer a deep read: the oracle may know better.
            if (
                entry is not None
                and entry[1] > now
                and (entry[2] or not deep or entry[0] in _READY_STATES)
            ):
                return entry[0]
        gen = self._cache_gen
        if family is PermissionId.AUTOMATION:
            wait = automation_wait_s
            if wait is None:
                wait = 0.0 if _on_event_loop_thread() else self._automation.timeout_s
            got = self._automation.read(
                target, lambda: self._call_state(port, perm, target, deep), wait_s=wait
            )
            if got.kind == "value" and got.state is not None:
                state = got.state
            elif got.kind == "pending":
                # Still answering: the last answer, else "unknown" (the port's own
                # reading of a player it cannot see). Not cached.
                return got.state if got.state is not None else PermissionState.NOT_DETERMINED
            else:
                return PermissionState.UNAVAILABLE
        else:
            try:
                state = self._call_state(port, perm, target, deep)
            except Exception:  # noqa: BLE001 - a native failure is "unavailable", never a crash
                log.debug("The %s state read failed.", perm.value, exc_info=True)
                state = PermissionState.UNAVAILABLE
        if family is PermissionId.SCREEN_RECORDING and gen == self._cache_gen:
            if state is PermissionState.GRANTED:
                self._sr_proven = True
            elif deep and state is not PermissionState.UNAVAILABLE:
                # A deep read is the one that decides: the oracle had its chance and
                # the preflight says no, so the remembered proof must not paper over
                # a grant that was revoked since (the Computer-Use pre-dispatch gate
                # reads deep to see exactly that). The oracle only ever proves
                # positively, so "no readable window title" also lands here: the
                # next shallow read then reports the honest negative and the next
                # deep read that finds a titled window proves the grant again.
                self._sr_proven = False
            elif self._sr_proven and state in _UNDECIDED_STATES:
                # Shallow read only: the frozen preflight can go stale negative
                # (BUG-161), a proven grant must not flap back every second.
                state = PermissionState.GRANTED
        ttl = self._granted_ttl_s if state is PermissionState.GRANTED else self._negative_ttl_s
        if gen == self._cache_gen:
            self._cache[key] = (state, now + ttl, deep)
        self._observe(perm, target, state)
        return state

    def _observe(self, perm: PermissionId, target: str, state: PermissionState) -> None:
        """A decision for an OS-dialog permission ends its "request in flight" cooldown."""
        family = PANE_FAMILY[perm]
        key = (family, target if family is PermissionId.AUTOMATION else "")
        if (
            REQUEST_CLASS.get(family) is RequestClass.DIALOG
            and state in _DECIDED_STATES
            and key in self._last_native
        ):
            # One atomic dict operation, no lock: check() must stay lock-free (design
            # 3.2 item 10), and a stamp a concurrent claim reads just before this is
            # harmless (it only delays one re-ask).
            self._last_native.pop(key, None)

    def note_reset(self, permission: PermissionId | str, *, target: str | None = None) -> None:
        """A ``tccutil reset`` happened: forget the per-process cooldown for it.

        The route that resets a permission calls this so "Ask again" really asks.
        An Automation reset without a ``target`` forgets every player (``tccutil``
        resets the whole Apple Events row of the app).
        """
        perm = PermissionId(permission)
        family = PANE_FAMILY[perm]
        every_player = family is PermissionId.AUTOMATION and not target
        key = (family, (target or "") if family is PermissionId.AUTOMATION else "")

        def matches(slot_key: _SlotKey) -> bool:
            return slot_key[0] is family if every_player else slot_key == key

        with self._lock:
            # ``_observe`` pops ``_last_native`` without this lock (check() is
            # lock-free), so iterate a C-level copy, never the live dict.
            for stamp_key in [k for k in self._last_native.copy() if matches(k)]:
                self._last_native.pop(stamp_key, None)
            for stamp_key in [k for k in self._failed_native.copy() if matches(k)]:
                self._failed_native.pop(stamp_key, None)
            # An episode that is still open remembers it already asked (one native
            # request per permission per episode); after a reset that memory
            # describes a decision macOS no longer has, so the next ensure may ask.
            for episode in self._episodes.values():
                for slot in episode.slots.values():
                    if matches(slot.key):
                        slot.asked_at = None
                        slot.restart_hint = False
                        slot.refused_use = False
                        slot.promoted = False
        self.invalidate(perm)

    # ------------------------------------------------------------ ensure

    def ensure(
        self,
        permission: PermissionId | str,
        *,
        feature: str,
        interactive: bool = True,
        wait_s: float = 0.0,
        target: str | None = None,
        trace_id: UUID | str | None = None,
        allow_outside_app: bool = False,
        force_ask: bool = False,
    ) -> EnsureResult:
        """Make sure a permission is granted, asking macOS at most once per episode.

        ``interactive=True`` (a user gesture) may make the native request;
        ``interactive=False`` (a background consumer) never does and only records a
        background-origin episode. ``wait_s`` > 0 waits for the answer in 250 ms
        polls: WORKER THREADS ONLY (see the module docstring); 0 returns PENDING at
        once. ``force_ask`` marks an explicit click on an "Allow" button: it skips
        the per-process cooldown of the PROMPT-ONCE permissions (never the
        per-episode single request, never a denial). Never raises for a native
        failure.
        """
        return self.ensure_all(
            [permission],
            feature=feature,
            interactive=interactive,
            wait_s=wait_s,
            target=target,
            trace_id=trace_id,
            allow_outside_app=allow_outside_app,
            force_ask=force_ask,
        )[0]

    def ensure_all(
        self,
        permissions: Iterable[PermissionId | str],
        *,
        feature: str,
        interactive: bool = True,
        wait_s: float = 0.0,
        target: str | None = None,
        trace_id: UUID | str | None = None,
        allow_outside_app: bool = False,
        force_ask: bool = False,
    ) -> list[EnsureResult]:
        """``ensure`` for several permissions: ONE coalesced episode and ONE event.

        Computer use needs Screen Recording and Accessibility together; the user
        sees one prompt for the pair. The results come back in request order.
        """
        wanted = [PermissionId(item) for item in permissions]
        tgt = _fixed_target(target)
        try:
            port = self._port()
            if not self._is_darwin(port):
                return [self._not_required(perm) for perm in wanted]
            plan = self._start(
                port,
                wanted,
                target or "",
                feature,
                interactive,
                trace_id,
                allow_outside_app,
                force_ask,
            )
            if wait_s > 0 and interactive:
                if _on_event_loop_thread():
                    # A synchronous wait here would freeze the loop for up to wait_s
                    # while the OS dialog is open: ensure_async is the loop's API.
                    log.debug("ensure(%s) waited on the event loop; not waiting.", feature)
                else:
                    self._wait_sync(port, plan, wait_s)
            return self._finish(plan)
        except Exception:  # noqa: BLE001 - ensure() never raises (contract)
            log.debug("ensure(%s) failed.", feature, exc_info=True)
            return [self._unavailable(perm, tgt) for perm in wanted]

    async def ensure_async(
        self,
        permission: PermissionId | str,
        *,
        feature: str,
        interactive: bool = True,
        wait_s: float = 0.0,
        target: str | None = None,
        trace_id: UUID | str | None = None,
        allow_outside_app: bool = False,
        force_ask: bool = False,
    ) -> EnsureResult:
        """``ensure`` for the event loop: native async, never pins an executor worker.

        The native request runs in ONE ``asyncio.to_thread`` call; the wait is
        ``await asyncio.sleep`` polls, each of which reads the state on the loop
        only when that read is cheap (a probe that can be slow, the window-title
        oracle and Automation, takes one short thread hop instead).
        """
        results = await self.ensure_all_async(
            [permission],
            feature=feature,
            interactive=interactive,
            wait_s=wait_s,
            target=target,
            trace_id=trace_id,
            allow_outside_app=allow_outside_app,
            force_ask=force_ask,
        )
        return results[0]

    async def ensure_all_async(
        self,
        permissions: Iterable[PermissionId | str],
        *,
        feature: str,
        interactive: bool = True,
        wait_s: float = 0.0,
        target: str | None = None,
        trace_id: UUID | str | None = None,
        allow_outside_app: bool = False,
        force_ask: bool = False,
    ) -> list[EnsureResult]:
        """The coalesced, loop-friendly form of :meth:`ensure_all`."""
        wanted = [PermissionId(item) for item in permissions]
        tgt = _fixed_target(target)
        try:
            port = self._port()
            if not self._is_darwin(port):
                return [self._not_required(perm) for perm in wanted]
            args = (
                port,
                wanted,
                target or "",
                feature,
                interactive,
                trace_id,
                allow_outside_app,
                force_ask,
            )
            if self._all_ready_cheaply(port, wanted):
                # Nothing to ask and no native call to make: no thread hop needed.
                plan = self._start(*args)
            else:
                plan = await asyncio.to_thread(self._start, *args)
            if wait_s > 0 and interactive:
                deadline = self._clock() + wait_s
                while self._waiting(plan):
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        break
                    await asyncio.sleep(min(self._poll_s, remaining))
                    if self._needs_thread_poll(plan):
                        # The window-title oracle enumerates windows and an
                        # Automation probe can be slow: one short thread hop per
                        # poll, never a worker held across the sleep.
                        await asyncio.to_thread(self._poll, port, plan)
                    else:
                        self._poll(port, plan)
            return self._finish(plan)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - ensure() never raises (contract)
            log.debug("ensure_async(%s) failed.", feature, exc_info=True)
            return [self._unavailable(perm, tgt) for perm in wanted]

    def _all_ready_cheaply(self, port: Any, wanted: Sequence[PermissionId]) -> bool:
        """Whether every permission reads granted without a possibly slow probe.

        Automation is excluded: its probe can hang for a running player, so it
        never runs on the loop.
        """
        if not wanted or any(PANE_FAMILY[perm] is PermissionId.AUTOMATION for perm in wanted):
            return False
        return all(self._read_state(port, perm, "") in _READY_STATES for perm in wanted)

    # ---- ensure: the shared core ------------------------------------------

    def _start(
        self,
        port: Any,
        wanted: Sequence[PermissionId],
        target: str,
        feature: str,
        interactive: bool,
        trace_id: UUID | str | None,
        allow_outside_app: bool,
        force_ask: bool = False,
    ) -> _Plan:
        """Read the states, open or join the episode, make the native request if allowed.

        Blocking (it may call into the OS), so the async path runs it in a thread.
        It never waits for an answer. The window-title oracle runs only for an
        interactive call that is not on an event-loop thread: a background consumer
        and a loop caller read the preflight alone.
        """
        if feature not in PERMISSION_FEATURES:
            log.warning("ensure() was called with the unknown feature %r.", feature)
            feature = ""
        now = self._clock()
        deep = interactive and not _on_event_loop_thread()
        items: list[_Item] = []
        for perm in wanted:
            family = PANE_FAMILY[perm]
            tgt = _fixed_target(target) if family is PermissionId.AUTOMATION else ""
            item = _Item(requested=perm, family=family, target=tgt)
            if family is PermissionId.AUTOMATION and not tgt:
                # Only the fixed player table may be asked about (nothing else is
                # ever interpolated into the consent script, echoed or published).
                log.debug("Automation was requested for a target that is not a scriptable player.")
                item.bad_target = True
                item.state = PermissionState.UNAVAILABLE
            else:
                item.state = self._read_state(port, perm, tgt, deep=deep)
            items.append(item)

        plan = _Plan(
            items=items, feature=feature, interactive=interactive, force_ask=force_ask, deep=deep
        )
        pending = [item for item in items if item.state not in _READY_STATES]
        for item in items:
            if item.state in _READY_STATES:
                self._note_ready(item)
        if not pending:
            return plan

        episode = self._open_episode(feature, pending, interactive, trace_id, now)
        plan.episode = episode
        try:
            for item in pending:
                item.slot = episode.slots[item.slot_key]
                item.slot.state = item.state
                item.slot.notified = False
                if deep and item.family is PermissionId.SCREEN_RECORDING:
                    item.slot.deep_at = now
                self._maybe_ask(port, plan, item, allow_outside_app, now)
        finally:
            with self._lock:
                episode.starting -= 1
        self._emit_episode(episode)
        self._ensure_watcher()
        return plan

    def _maybe_ask(
        self, port: Any, plan: _Plan, item: _Item, allow_outside_app: bool, now: float
    ) -> None:
        slot = item.slot
        if slot is None or plan.episode is None:
            return
        if item.bad_target:
            slot.unavailable = True
            return
        if item.state in (
            PermissionState.RESTRICTED,
            PermissionState.UNAVAILABLE,
            PermissionState.DENIED,
        ):
            # A decision (or an impossibility) is on file: macOS will not ask again.
            return
        if REQUEST_CLASS.get(item.family) not in _ASKABLE_CLASSES:
            return
        if not plan.interactive:
            return  # a background consumer never asks
        # Everything below is decided afresh on every gesture: an earlier
        # "unavailable" (a player that was not running) must not stick to the slot
        # for the rest of the episode. A request that FAILED stays "unavailable"
        # for a short while, so a retry loop does not hammer a broken native call.
        slot.unavailable = not plan.force_ask and self._failed_recently(slot.key, now)
        if not self._desktop_session_ok(port):
            # No window server (a headless backend, an ssh session, a launchd job):
            # nothing could be shown, and a native request there is a silently dead
            # feature (P8). The same case is where a bundle-less process fools the
            # usage-string guard below, so it is refused first.
            slot.unavailable = True
            return
        outside = self._outside_installed_app(port) and not allow_outside_app
        slot.outside = outside
        slot.outside_bundle = outside and self._launched_as_bundle(port)
        if outside:
            return
        if not self._usage_string_ok(port, item.family):
            slot.unavailable = True
            return
        if not self._claim(plan.episode, slot, now, force=plan.force_ask):
            return
        # The state read above can be stale by now (another thread's request may
        # have been answered in between): never ask about a decision that exists.
        deep = self._deep_for(plan, slot, now)
        current = self._read_state(port, item.requested, item.target, fresh=True, deep=deep)
        if current not in _UNDECIDED_STATES:
            item.state = current
            slot.state = current
            self._release_claim(slot)
            return
        if item.family is PermissionId.AUTOMATION:
            # The consent runner blocks for as long as the dialog is open (up to
            # 120 s): it runs on its own daemon thread and the caller hears PENDING.
            item.asked = True
            self._begin_automation_ask(port, plan.episode, slot, item)
            return
        outcome = self._request_native(port, item)
        item.asked = True
        if outcome == "unavailable":
            # Nothing is in flight: a later attempt may try again (after the retry
            # window, or at once for an explicit click).
            slot.unavailable = True
            self._release_claim(slot, failed=True)
            return
        # Right after the request: an answer that arrived at once (a dialog answered
        # in place, a switch already on) must be seen by THIS call, oracle included.
        fresh = self._read_state(port, item.requested, item.target, fresh=True, deep=plan.deep)
        item.state = fresh
        slot.state = fresh
        if plan.deep and item.family is PermissionId.SCREEN_RECORDING:
            slot.deep_at = self._clock()

    def _begin_automation_ask(self, port: Any, episode: _Episode, slot: _Slot, item: _Item) -> None:
        """Run the (blocking) Automation request off the calling thread."""
        done = threading.Event()
        slot.asking = True
        slot.ask_done = done

        def work() -> None:
            try:
                outcome = self._request_native(port, item)
                self._settle_automation_ask(port, slot, item, outcome)
            except Exception:  # noqa: BLE001 - a thread must not die loudly
                log.debug("Settling the Automation request failed.", exc_info=True)
                slot.unavailable = True
                self._release_claim(slot)
            finally:
                slot.asking = False
                done.set()
            self._emit_episode(episode)

        try:
            self._ask_runner(work)
        except Exception:  # noqa: BLE001 - e.g. the process cannot start another thread
            log.debug("The Automation request could not be started.", exc_info=True)
            slot.asking = False
            slot.unavailable = True
            done.set()
            self._release_claim(slot)

    def _settle_automation_ask(self, port: Any, slot: _Slot, item: _Item, outcome: str) -> None:
        """Read the answer after the consent runner returned and classify what it means."""
        fresh = self._read_state(
            port,
            item.requested,
            item.target,
            fresh=True,
            automation_wait_s=self._automation.timeout_s,
        )
        item.state = fresh
        slot.state = fresh
        if outcome == "unavailable":
            slot.unavailable = True
            self._release_claim(slot, failed=True)
        elif fresh in _UNDECIDED_STATES:
            if outcome == "no_dialog":
                # The runner finished without a dialog and nothing was decided: the
                # player is not running. Nothing is in flight, so a later attempt
                # (once the player runs) may ask again.
                slot.unavailable = True
                self._release_claim(slot)
            elif outcome == "timed_out":
                # Nobody answered before the runner was killed: a slow user is not a
                # missing player. The view is "not asked yet, may ask" again.
                self._release_claim(slot)

    @staticmethod
    def _outside_installed_app(port: Any) -> bool:
        return bool(getattr(port, "outside_installed_app", False))

    @staticmethod
    def _launched_as_bundle(port: Any) -> bool:
        return bool(getattr(port, "launched_as_bundle", False))

    @staticmethod
    def _desktop_session_ok(port: Any) -> bool:
        """Whether a window server answers (a stub port without the probe is assumed to)."""
        check = getattr(port, "has_desktop_session", None)
        if check is None:
            return True
        try:
            return bool(check())
        except Exception:  # noqa: BLE001 - an unreadable session fails closed, like the port
            log.debug("Reading the desktop session failed.", exc_info=True)
            return False

    @staticmethod
    def _usage_string_ok(port: Any, family: PermissionId) -> bool:
        check = getattr(port, "usage_string_present", None)
        if check is None:
            return True
        try:
            return bool(check(family))
        except Exception:  # noqa: BLE001 - an unreadable bundle must not look present
            log.debug("The usage string check for %s failed.", family.value, exc_info=True)
            return False

    @staticmethod
    def _request_native(port: Any, item: _Item) -> str:
        """The one native call. Never raises, never holds a lock."""
        try:
            if item.family is PermissionId.AUTOMATION:
                outcome = port.request_native(item.family, target=item.target)
            else:
                outcome = port.request_native(item.family)
        except Exception:  # noqa: BLE001 - the native request boundary never raises
            log.debug("The native %s request failed.", item.family.value, exc_info=True)
            return "unavailable"
        return str(outcome)

    def _claim(self, episode: _Episode, slot: _Slot, now: float, *, force: bool = False) -> bool:
        """Atomically decide that THIS caller makes the native request.

        At most one per permission per episode, and at most one per cooldown
        window per process. ``force`` (an explicit click on "Allow") skips both for
        a PROMPT-ONCE permission, nothing else: a person re-clicking is not a retry
        loop, and the button would otherwise do nothing for the ten minutes an
        episode lives (the route rate limits the clicks). A DIALOG-class dialog may
        still be open, so it is never asked twice. The decision and the stamp are one
        critical section, so many threads racing here produce one request; the
        request itself runs after the lock is released.
        """
        window = _REASK_AFTER_S.get(slot.family, math.inf)
        skip_cooldown = force and REQUEST_CLASS.get(slot.family) is RequestClass.PROMPT_ONCE
        with self._lock:
            if slot.asked_at is not None and not skip_cooldown:
                return False
            if not force and self._failed_recently(slot.key, now):
                return False
            last = self._last_native.get(slot.key)
            if not skip_cooldown and last is not None and now - last < window:
                return False
            self._last_native[slot.key] = now
            slot.asked_at = now
            return True

    def _release_claim(self, slot: _Slot, *, failed: bool = False) -> None:
        with self._lock:
            slot.asked_at = None
            self._last_native.pop(slot.key, None)
            if failed:
                self._failed_native[slot.key] = self._clock()

    def _failed_recently(self, key: _SlotKey, now: float) -> bool:
        failed_at = self._failed_native.get(key)
        return failed_at is not None and now - failed_at < _FAILED_RETRY_S

    # ---- waiting ----------------------------------------------------------

    def _waiting(self, plan: _Plan) -> bool:
        """Whether some asked permission can still change (a dialog may be open)."""
        for item in plan.items:
            slot = item.slot
            if slot is None or item.state not in _UNDECIDED_STATES:
                continue
            if slot.unavailable or slot.outside or item.bad_target:
                continue
            if REQUEST_CLASS.get(item.family) not in _ASKABLE_CLASSES:
                continue
            if slot.asking or slot.asked_at is not None or slot.key in self._last_native:
                return True
        return False

    @staticmethod
    def _needs_thread_poll(plan: _Plan) -> bool:
        """Whether a poll may be slow: the oracle enumerates windows, Automation can hang."""
        return any(
            item.state not in _READY_STATES
            and (
                item.family is PermissionId.AUTOMATION
                or (item.family is PermissionId.SCREEN_RECORDING and plan.deep)
            )
            for item in plan.items
        )

    @staticmethod
    def _pending_ask(plan: _Plan) -> threading.Event | None:
        """The "finished" event of an Automation request still running for this plan."""
        for item in plan.items:
            slot = item.slot
            if slot is not None and slot.asking and slot.ask_done is not None:
                return slot.ask_done
        return None

    def _oracle_due(self, slot: _Slot, now: float) -> bool:
        return slot.deep_at is None or now - slot.deep_at >= self._oracle_every_s

    def _deep_for(self, plan: _Plan, slot: _Slot, now: float) -> bool:
        """Whether this read may run the window-title oracle: a gesture entry, rate limited.

        ``_start`` stamps the slot when it ran the oracle, so the claim re-read and the
        polls of the same ``ensure`` call stay shallow (the one read right after a
        request is the exception: it must see an answer that arrived at once).
        """
        return (
            plan.deep
            and slot.family is PermissionId.SCREEN_RECORDING
            and self._oracle_due(slot, now)
        )

    def _poll(self, port: Any, plan: _Plan) -> None:
        now = self._clock()
        for item in plan.items:
            slot = item.slot
            if slot is None or item.state in _READY_STATES:
                continue
            deep = self._deep_for(plan, slot, now)
            state = self._read_state(
                port,
                item.requested,
                item.target,
                fresh=True,
                deep=deep,
                automation_wait_s=self._automation_refresh_wait_s,
            )
            item.state = state
            slot.state = state
            if deep:
                slot.deep_at = now

    def _wait_sync(self, port: Any, plan: _Plan, wait_s: float) -> None:
        deadline = self._clock() + wait_s
        # A second, real-time bound: while a request thread runs we wait on ITS
        # event in real seconds, which an injected test clock does not advance.
        real_deadline = time.monotonic() + wait_s
        while self._waiting(plan):
            remaining = min(deadline - self._clock(), real_deadline - time.monotonic())
            if remaining <= 0:
                break
            step = min(self._poll_s, remaining)
            ask = self._pending_ask(plan)
            if ask is not None:
                ask.wait(step)  # wakes the moment the request thread has finished
            else:
                self._sleep(step)
            self._poll(port, plan)

    # ---- results ----------------------------------------------------------

    def _finish(self, plan: _Plan) -> list[EnsureResult]:
        now = self._clock()
        results: list[EnsureResult] = []
        for item in plan.items:
            slot = item.slot
            if slot is None:
                results.append(self._result(item, self._ready_view(item.state)))
                continue
            # The slot is the shared truth (the watcher and the request thread write
            # it too); the item may be one poll behind.
            item.state = slot.state
            if item.state in _READY_STATES:
                # A grant is a grant (P2): a restart hint on the slot never turns a
                # live GRANTED into a refusal, it only keeps the card honest.
                results.append(self._result(item, self._ready_view(item.state)))
            else:
                results.append(self._result(item, self._classify(slot, item.state, now)))
        if plan.episode is not None:
            self._emit_episode(plan.episode)
        return results

    @staticmethod
    def _ready_view(state: PermissionState) -> _View:
        outcome = (
            PermissionOutcome.NOT_REQUIRED
            if state is PermissionState.NOT_REQUIRED
            else PermissionOutcome.GRANTED
        )
        return _View(outcome, "", "", False, False, False)

    def _result(self, item: _Item, view: _View) -> EnsureResult:
        if view.outcome in _PROCEED_OUTCOMES:
            return EnsureResult(
                permission=item.requested,
                outcome=view.outcome,
                state=item.state,
                asked=item.asked,
                outside_installed_app=False,
                agent_detail="",
                user_detail="",
                target=item.target,
            )
        return EnsureResult(
            permission=item.requested,
            outcome=view.outcome,
            state=item.state,
            asked=item.asked,
            outside_installed_app=view.outside,
            agent_detail=agent_detail_for(
                item.family, view.reason, target=item.target, asking=view.asking
            ),
            user_detail=user_detail_for(
                item.family,
                view.reason,
                target=item.target,
                asking=view.asking,
                outside_app=view.outside,
                launched_as_bundle=view.outside_bundle,
                refused_use=item.slot is not None and item.slot.refused_while(item.state),
            ),
            reason=view.reason,
            can_prompt=view.can_prompt,
            can_open_settings=view.can_open_settings,
            target=item.target,
        )

    @staticmethod
    def _not_required(perm: PermissionId) -> EnsureResult:
        return EnsureResult(
            permission=perm,
            outcome=PermissionOutcome.NOT_REQUIRED,
            state=PermissionState.NOT_REQUIRED,
            asked=False,
            outside_installed_app=False,
            agent_detail="",
            user_detail="",
        )

    @staticmethod
    def _unavailable(perm: PermissionId, target: str) -> EnsureResult:
        family = PANE_FAMILY[perm]
        tgt = _fixed_target(target) if family is PermissionId.AUTOMATION else ""
        return EnsureResult(
            permission=perm,
            outcome=PermissionOutcome.UNAVAILABLE,
            state=PermissionState.UNAVAILABLE,
            asked=False,
            outside_installed_app=False,
            agent_detail=agent_detail_for(family, "unavailable", target=tgt),
            user_detail=user_detail_for(family, "unavailable", target=tgt),
            reason="unavailable",
            target=tgt,
        )

    # ---- classification ---------------------------------------------------

    def _classify(self, slot: _Slot, state: PermissionState, now: float) -> _View:
        """Turn (state, what we did) into an outcome, a reason and a phase.

        The phase is ``os_dialog`` while macOS is (or may be) asking and ``blocked``
        once the user has to act. A PROMPT-ONCE dialog only points at Settings, so
        it turns ``blocked`` after :data:`_BLOCKED_AFTER_S` seconds or an app
        refocus, and a DIALOG-class dialog after :data:`_DIALOG_CEILING_S` without an
        answer (UNVERIFIED how long a real dialog stays up; both figures are ours).
        """
        family = slot.family
        cls = REQUEST_CLASS.get(family, RequestClass.NONE)
        has_pane = family in SETTINGS_PATH_TEXT
        if slot.restart_hint:
            # A real attempt failed although the state may read granted: the one
            # honest advice left is "quit and reopen" (never an automatic restart).
            return _View(
                PermissionOutcome.NEEDS_SETTINGS, "restart_hint", "blocked", False, has_pane, False
            )
        if slot.refused_while(state):
            # A real Apple Event was refused (-1743) although the probe reads granted:
            # the probe is what lied, so the honest advice is the Automation pane. It
            # is never asked for again from here (``can_prompt`` is False) and never a
            # restart hint: Apple Events are checked per send.
            return _View(
                PermissionOutcome.NEEDS_SETTINGS,
                "needs_settings",
                "blocked",
                False,
                has_pane,
                False,
            )
        if state in _READY_STATES:
            return self._ready_view(state)
        if state is PermissionState.RESTRICTED:
            return _View(
                PermissionOutcome.UNAVAILABLE, "restricted", "blocked", False, False, False
            )
        if state is PermissionState.UNAVAILABLE or slot.unavailable:
            return _View(
                PermissionOutcome.UNAVAILABLE, "unavailable", "blocked", False, False, False
            )
        if state is PermissionState.DENIED or cls not in _ASKABLE_CLASSES:
            return _View(PermissionOutcome.DENIED, "denied", "blocked", False, has_pane, False)
        if slot.outside:
            return _View(
                PermissionOutcome.NEEDS_SETTINGS,
                "needs_settings",
                "blocked",
                True,
                has_pane,
                True,
                slot.outside_bundle,
            )
        stamp = slot.asked_at if slot.asked_at is not None else self._last_native.get(slot.key)
        if stamp is None:
            # Never asked in this process: waiting for a gesture.
            return _View(
                PermissionOutcome.PENDING, "not_determined", "blocked", True, has_pane, False
            )
        if cls is RequestClass.DIALOG:
            if slot.asking or now - stamp < _DIALOG_CEILING_S:
                return _View(
                    PermissionOutcome.PENDING, "not_determined", "os_dialog", False, has_pane, False
                )
            return _View(
                PermissionOutcome.NEEDS_SETTINGS,
                "needs_settings",
                "blocked",
                False,
                has_pane,
                False,
            )
        if slot.promoted or now - stamp >= _BLOCKED_AFTER_S:
            return _View(
                PermissionOutcome.NEEDS_SETTINGS,
                "needs_settings",
                "blocked",
                False,
                has_pane,
                False,
            )
        return _View(
            PermissionOutcome.PENDING, "needs_settings", "os_dialog", False, has_pane, False
        )

    # ----------------------------------------------------------- episodes

    def _open_episode(
        self,
        feature: str,
        pending: Sequence[_Item],
        interactive: bool,
        trace_id: UUID | str | None,
        now: float,
    ) -> _Episode:
        """Get or create the one record for this feature and these permissions.

        An open episode of the same feature that shares a permission with the
        pending set is JOINED (and gains the slots it lacks), so granting one member
        of a coalesced pair never opens a second record for the same permission.
        The caller gets it with ``starting`` raised and must lower it again.
        """
        slot_keys = sorted({item.slot_key for item in pending}, key=lambda k: (k[0].value, k[1]))
        key: _EpisodeKey = (feature, tuple((fam.value, tgt) for fam, tgt in slot_keys))
        origin = "user" if interactive else "background"
        trace = _coerce_trace(trace_id)
        with self._lock:
            episode = self._episodes.get(key)
            if episode is None:
                wanted_keys = set(slot_keys)
                episode = next(
                    (
                        candidate
                        for candidate in self._episodes.values()
                        if candidate.feature == feature
                        and not candidate.closed
                        and wanted_keys & set(candidate.slots)
                    ),
                    None,
                )
            if episode is None:
                episode = _Episode(
                    key=key,
                    feature=feature,
                    slots={},
                    origin=origin,
                    trace_id=trace,
                    opened_ns=time.time_ns(),
                    touched=now,
                )
                self._episodes[key] = episode
            else:
                episode.touched = now
                if interactive:
                    # A gesture upgrades a background episode: the card may open now.
                    episode.origin = "user"
                if trace_id is not None:
                    episode.trace_id = trace
            for item in pending:
                slot_key = item.slot_key
                existing = episode.slots.get(slot_key)
                probe = (
                    PermissionId.EVENT_POSTING
                    if item.requested is PermissionId.EVENT_POSTING
                    else item.family
                )
                if existing is None:
                    episode.slots[slot_key] = _Slot(
                        family=item.family, target=item.target, probe=probe, state=item.state
                    )
                elif probe is PermissionId.EVENT_POSTING:
                    existing.probe = probe
            episode.starting += 1
        return episode

    def _note_ready(self, item: _Item) -> None:
        """A permission was seen granted: advance every open episode that waits on it."""
        with self._lock:
            waiting = [ep for ep in self._episodes.values() if item.slot_key in ep.slots]
        for episode in waiting:
            slot = episode.slots.get(item.slot_key)
            if slot is not None:
                slot.state = item.state
            self._emit_episode(episode)

    def _emit_episode(self, episode: _Episode, fired: set[PermissionId] | None = None) -> None:
        """Bring subscribers up to date with an episode: grants, closing, one event.

        Idempotent: the dedup key is (permissions, feature, reason, phase, origin),
        so a repeated silent call changes nothing. An episode whose caller has not
        finished its asks yet (``starting``) is left alone: nothing is claimed
        there, so a classify would announce "blocked, can prompt" while the OS
        dialog is still being requested. ``fired`` collects the families whose
        listeners already ran in this pass, so two episodes for one permission
        call a re-arm once.
        """
        if episode.closed or episode.starting > 0:
            return
        now = self._clock()
        views: list[tuple[_Slot, _View]] = []
        newly_granted: list[_Slot] = []
        for slot in tuple(episode.slots.values()):
            if slot.state in _READY_STATES and not slot.failed_use:
                if not slot.notified:
                    newly_granted.append(slot)
                continue
            views.append((slot, self._classify(slot, slot.state, now)))
        if newly_granted:
            with self._lock:
                fresh = [slot for slot in newly_granted if not slot.notified]
                for slot in fresh:
                    slot.notified = True
            self._call_listeners([slot.family for slot in fresh], fired)
        if not views:
            self._close(episode, granted=True, fired=fired)
            return

        reason, phase, view = self._aggregate(views)
        slot_primary = next(slot for slot, candidate in views if candidate is view)
        permissions = tuple(slot.family.value for slot, _ in views)
        detail = user_detail_for(
            slot_primary.family,
            reason,
            target=slot_primary.target,
            asking=phase == "os_dialog",
            outside_app=view.outside,
            launched_as_bundle=view.outside_bundle,
            refused_use=slot_primary.refused_while(slot_primary.state),
        )
        can_prompt = any(candidate.can_prompt for _, candidate in views)
        can_open = any(candidate.can_open_settings for _, candidate in views)
        outside = any(candidate.outside for _, candidate in views)
        target = next((slot.target for slot, _ in views if slot.target), "")
        with self._lock:
            if episode.closed:
                return
            episode.reason = reason
            episode.phase = phase
            episode.can_prompt = can_prompt
            episode.can_open_settings = can_open
            episode.outside_app = outside
            episode.detail = detail
            dedup = (permissions, episode.feature, reason, phase, episode.origin)
            if dedup in episode.published:
                return
            episode.published.add(dedup)
            origin = episode.origin
            trace = episode.trace_id
        log.debug(
            "Permission needed: %s for %s (%s, %s, %s).",
            ",".join(permissions),
            episode.feature,
            reason,
            phase,
            origin,
        )
        self._publish(
            PermissionNeeded(
                trace_id=trace,
                source_layer=_SOURCE_LAYER,
                permissions=permissions,
                feature=episode.feature,
                reason=reason,
                phase=phase,
                origin=origin,
                target=target,
                can_prompt=can_prompt,
                can_open_settings=can_open,
                outside_app=outside,
                detail=detail,
            )
        )

    @staticmethod
    def _aggregate(views: Sequence[tuple[_Slot, _View]]) -> tuple[str, str, _View]:
        ranked = sorted(
            (candidate for _, candidate in views),
            key=lambda v: _REASON_PRIORITY.index(v.reason),
        )
        primary = ranked[0]
        phase = (
            "os_dialog"
            if all(candidate.phase == "os_dialog" for _, candidate in views)
            else "blocked"
        )
        return primary.reason, phase, primary

    def _close(
        self, episode: _Episode, *, granted: bool, fired: set[PermissionId] | None = None
    ) -> None:
        """End an episode once: remove it, tell the bus, call the listeners."""
        with self._lock:
            if episode.closed:
                return
            episode.closed = True
            if self._episodes.get(episode.key) is episode:
                del self._episodes[episode.key]
            unnotified = [
                slot
                for slot in episode.slots.values()
                if slot.state in _READY_STATES and not slot.notified
            ]
            for slot in unnotified:
                slot.notified = True
            permissions = tuple(slot.family.value for slot in episode.slots.values())
            trace = episode.trace_id
        if unnotified:
            self._call_listeners([slot.family for slot in unnotified], fired)
        log.debug("Permission episode for %s ended (granted=%s).", episode.feature, granted)
        self._publish(
            PermissionResolved(
                trace_id=trace,
                source_layer=_SOURCE_LAYER,
                permissions=permissions,
                feature=episode.feature,
                granted=granted,
            )
        )

    def outstanding(self) -> list[Episode]:
        """The open episodes, oldest first: what a UI hydrates its cards from.

        An episode still being started (its asks are not finished) is not listed
        yet, and ``permissions`` names only what is still missing (the same set the
        ``PermissionNeeded`` event carries).
        """
        with self._lock:
            open_episodes = sorted(
                (ep for ep in self._episodes.values() if ep.starting <= 0 and ep.reason),
                key=lambda ep: ep.opened_ns,
            )
            return [
                Episode(
                    permissions=tuple(
                        slot.family.value
                        for slot in ep.slots.values()
                        if slot.state not in _READY_STATES or slot.failed_use
                    ),
                    feature=ep.feature,
                    reason=ep.reason,
                    phase=ep.phase,
                    origin=ep.origin,
                    target=next((slot.target for slot in ep.slots.values() if slot.target), ""),
                    can_prompt=ep.can_prompt,
                    can_open_settings=ep.can_open_settings,
                    outside_app=ep.outside_app,
                    detail=ep.detail,
                    trace_id=str(ep.trace_id),
                    opened_at_ns=ep.opened_ns,
                )
                for ep in open_episodes
            ]

    def note_app_activated(self) -> None:
        """The app was refocused: a PROMPT-ONCE dialog the user left is now "blocked".

        The window-focus handler calls it; a request that only offered "Open
        System Settings" has then done all it can, and the app may show its card.
        The refocus also makes the next watcher pass run the Screen Recording
        oracle at once (the user probably just flipped the switch).
        """
        with self._lock:
            episodes = list(self._episodes.values())
        for episode in episodes:
            changed = False
            for slot in tuple(episode.slots.values()):
                if slot.family is PermissionId.SCREEN_RECORDING:
                    slot.deep_at = None
                if (
                    slot.asked_at is not None
                    and not slot.promoted
                    and REQUEST_CLASS.get(slot.family) is RequestClass.PROMPT_ONCE
                ):
                    slot.promoted = True
                    changed = True
            if changed:
                self._emit_episode(episode)

    def refresh_episodes(self) -> None:
        """Re-read every open permission and publish the edges.

        What the watcher runs every two seconds; a status route calls it too, so
        an edge is emitted whichever path sees it first. Blocking (it reads the
        OS): not for the event loop.

        Every slot is read independently: an Automation probe that hangs costs a
        pass at most :data:`_AUTOMATION_REFRESH_WAIT_S` (and nothing once its
        player is quarantined), and the episodes without Automation are read and
        published FIRST, so a stuck player never delays a microphone grant. The
        Screen Recording oracle runs only for an episode a gesture opened, and at
        most every ``oracle_every_s`` seconds.
        """
        with self._lock:
            episodes = list(self._episodes.values())
        if not episodes:
            return
        port = self._port()
        if not self._is_darwin(port):
            # Nothing can be open off macOS; drop a record a test planted.
            for episode in episodes:
                self._close(episode, granted=True)
            return
        episodes.sort(
            key=lambda ep: any(slot.family is PermissionId.AUTOMATION for slot in ep.slots.values())
        )
        fired: set[PermissionId] = set()
        for episode in episodes:
            if episode.closed:
                continue
            now = self._clock()
            for slot in tuple(episode.slots.values()):
                self._refresh_slot(port, episode, slot, now)
            if any(
                slot.state not in _READY_STATES or slot.failed_use
                for slot in episode.slots.values()
            ) and (now - episode.touched > self._episode_ttl_s):
                self._close(episode, granted=False, fired=fired)
                continue
            self._emit_episode(episode, fired)

    def _refresh_slot(self, port: Any, episode: _Episode, slot: _Slot, now: float) -> None:
        """Re-read one slot; a failure of this read never costs the other slots theirs."""
        deep = (
            slot.family is PermissionId.SCREEN_RECORDING
            and episode.origin == "user"
            and self._oracle_due(slot, now)
        )
        try:
            slot.state = self._read_state(
                port,
                slot.probe,
                slot.target,
                deep=deep,
                automation_wait_s=self._automation_refresh_wait_s,
            )
        except Exception:  # noqa: BLE001 - one slot's failed read must not starve the others
            log.debug("Re-reading the %s permission failed.", slot.family.value, exc_info=True)
            return
        if deep:
            slot.deep_at = now

    # ------------------------------------------------------------ watcher

    def _ensure_watcher(self) -> None:
        """Start the watcher unless one is alive. Never blocks, never raises."""
        loop: asyncio.AbstractEventLoop | None = None
        with self._lock:
            if self._watcher_alive():
                return
            self._watch_gen += 1
            gen = self._watch_gen
            self._watch_running = True
            sink = self._sink
            loop = sink[1] if sink is not None else None
            if loop is not None and not loop.is_closed():
                self._watch_kind = "task"
                self._watch_loop = loop
                self._watch_task = None
            else:
                self._watch_kind = "thread"
                self._watch_loop = None
                self._watch_stop = threading.Event()
                thread = threading.Thread(
                    target=self._watch_thread_main,
                    args=(gen, self._watch_stop),
                    name="permission-episode-watcher",
                    daemon=True,
                )
                self._watch_thread = thread
                thread.start()
                return
        try:
            loop.call_soon_threadsafe(self._spawn_watch_task, gen)
        except RuntimeError:
            # The loop closed between the check and the call: fall back to a thread.
            log.debug("The attached loop is closed; the episode watcher uses a thread.")
            with self._lock:
                if gen != self._watch_gen:
                    return
                self._watch_kind = "thread"
                self._watch_loop = None
                self._watch_stop = threading.Event()
                thread = threading.Thread(
                    target=self._watch_thread_main,
                    args=(gen, self._watch_stop),
                    name="permission-episode-watcher",
                    daemon=True,
                )
                self._watch_thread = thread
                thread.start()

    def _watcher_alive(self) -> bool:
        """Whether a watcher is running (called with the lock held; reads only)."""
        if not self._watch_running:
            return False
        if self._watch_kind == "thread":
            thread = self._watch_thread
            return thread is not None and thread.is_alive()
        loop = self._watch_loop
        if loop is None or loop.is_closed():
            return False
        task = self._watch_task
        return task is None or not task.done()

    def _spawn_watch_task(self, gen: int) -> None:
        """Runs on the loop thread: create the watcher task."""
        with self._lock:
            if gen != self._watch_gen:
                return
        self._watch_task = asyncio.get_running_loop().create_task(self._watch_main(gen))

    async def _watch_main(self, gen: int) -> None:
        try:
            while True:
                await asyncio.sleep(self._watch_interval_s)
                # The tick reads the OS: off the loop, one short thread hop per tick.
                if not await asyncio.to_thread(self._watch_tick, gen):
                    return
        finally:
            with self._lock:
                if gen == self._watch_gen:
                    self._watch_running = False

    def _watch_thread_main(self, gen: int, stop: threading.Event) -> None:
        while not stop.wait(self._watch_interval_s):
            if not self._watch_tick(gen):
                return
        with self._lock:
            if gen == self._watch_gen:
                self._watch_running = False

    def _watch_tick(self, gen: int) -> bool:
        """One watcher pass. ``False`` means "stop": nothing is open, or superseded."""
        try:
            self.refresh_episodes()
        except Exception:  # noqa: BLE001 - the watcher must outlive one bad pass
            log.debug("The permission episode watcher pass failed.", exc_info=True)
        with self._lock:
            if gen != self._watch_gen:
                return False
            if not self._episodes:
                self._watch_running = False
                return False
        return True

    def _shutdown(self) -> None:
        """Stop the watcher and drop every record (tests and process teardown)."""
        with self._lock:
            self._watch_gen += 1
            self._watch_running = False
            stop = self._watch_stop
            task = self._watch_task
            loop = self._watch_loop
            self._watch_task = None
            self._watch_thread = None
            self._watch_loop = None
            self._episodes = {}
            self._last_native = {}
            self._failed_native = {}
            self._listeners = {}
            self._sink = None
        stop.set()
        self._cache = {}
        self._sr_proven = False
        self._automation.forget()
        if task is not None and loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                log.debug("The loop closed before the watcher task could be cancelled.")

    # ----------------------------------------------------------- listeners

    def add_listener(
        self, permission: PermissionId | str, callback: Callable[[], None]
    ) -> Callable[[], None]:
        """Call ``callback()`` when this permission turns granted inside an open episode.

        For consumers that are not on the bus: a hotkey backend that re-arms, a
        wake loop that parks until the microphone is allowed. EVENT_POSTING is
        folded into Accessibility. With a loop attached the callback runs on that
        loop's thread; without one, on the observing thread. It must be cheap and
        must not raise (a raise is logged and does not stop the other listeners).
        Returns an idempotent unsubscribe function.
        """
        family = PANE_FAMILY[PermissionId(permission)]
        with self._lock:
            self._listeners[family] = (*self._listeners.get(family, ()), callback)

        def unsubscribe() -> None:
            with self._lock:
                current = self._listeners.get(family, ())
                self._listeners[family] = tuple(cb for cb in current if cb is not callback)

        return unsubscribe

    def _call_listeners(
        self, families: Sequence[PermissionId], fired: set[PermissionId] | None = None
    ) -> None:
        """Run the listeners of these families; ``fired`` skips ones already run in this pass."""
        callbacks: list[Callable[[], None]] = []
        for family in families:
            if fired is not None:
                if family in fired:
                    continue
                fired.add(family)
            callbacks.extend(self._listeners.get(family, ()))
        if not callbacks:
            return
        sink = self._sink
        if sink is not None and not sink[1].is_closed():
            try:
                sink[1].call_soon_threadsafe(_run_callbacks, tuple(callbacks))
                return
            except RuntimeError:
                log.debug("The attached loop is closed; calling the listeners directly.")
        _run_callbacks(tuple(callbacks))

    # ------------------------------------------------------------- publish

    def _publish(self, event: PermissionNeeded | PermissionResolved) -> None:
        """Hand an event to the bus from any thread; never waits, never raises."""
        sink = self._sink
        if sink is None:
            log.debug(
                "No bus attached; %s kept in the episode registry only.", type(event).__name__
            )
            return
        bus, loop = sink
        if loop.is_closed():
            log.debug("The attached loop is closed; %s dropped.", type(event).__name__)
            return
        try:
            coro = bus.publish(event)
        except Exception:  # noqa: BLE001 - a broken bus must not break a permission check
            log.debug("The bus refused %s.", type(event).__name__, exc_info=True)
            return
        try:
            future = asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:
            coro.close()
            log.debug("The attached loop is closed; %s dropped.", type(event).__name__)
            return
        future.add_done_callback(_log_publish_failure)

    # ------------------------------------------------------------ settings

    def open_settings(self, permission: PermissionId | str) -> bool:
        """Open the System Settings pane of a permission. Never raises.

        Light: it goes through the port's ``open_pane`` (no snapshot, so no
        Automation probe and no oracle read) and needs a desktop session, not the
        installed-app identity (opening a pane is not a prompt, design P6). A
        user who goes to Settings also keeps their open episodes alive: the ten
        minute clock starts again.
        """
        perm = PermissionId(permission)
        family = PANE_FAMILY[perm]
        try:
            port = self._port()
            if not self._is_darwin(port):
                return False
            opened = bool(port.open_pane(family))
        except Exception:  # noqa: BLE001 - opening a pane is best-effort
            log.debug("Opening the %s pane failed.", perm.value, exc_info=True)
            return False
        if opened:
            now = self._clock()
            with self._lock:
                for episode in self._episodes.values():
                    if any(slot.family is family for slot in episode.slots.values()):
                        episode.touched = now
        return opened

    # ------------------------------------------------------- failed attempts

    def report_failed_use(
        self,
        permission: PermissionId | str,
        *,
        feature: str,
        target: str | None = None,
        trace_id: UUID | str | None = None,
        reason: str | None = None,
        origin: str | None = None,
    ) -> EnsureResult:
        """A consumer reports a REAL failed attempt to use a permission it was granted.

        Two families have a producer here, each with ONE reason it can carry
        (``reason=None`` means that default; any other value is a state that does not
        fit and falls back to the plain path below):

        * Screen Recording and Input Monitoring, ``restart_hint``: the preflight is
          frozen per process and a tap may see no events (community-observed,
          UNVERIFIED), so when a real capture or tap failed while the live state
          reads granted, or the user came back from Settings and the preflight is
          still negative, the honest advice is "quit and reopen", never an automatic
          restart. Call it from the capture or tap site after a real failure, from a
          user-started feature only; the episode origin defaults to ``user``.
        * Automation, ``needs_settings``: an Apple Event to ONE player (``target``, a
          bundle id from ``AUTOMATION_TARGETS``; anything else is refused with
          UNAVAILABLE, opens nothing and is never echoed) was refused with ``-1743``
          although the probe read GRANTED. The probe is exactly what lied (the grant
          may belong to another app: the attribution of a child ``osascript`` is
          UNVERIFIED), so this opens ONE episode that a granted read does not close,
          and that only :meth:`report_use_ok`, :meth:`note_reset` or the episode's
          ten minute TTL ends. It is NOT a restart hint (Apple Events are checked per
          send), it never asks (no native request, ``can_prompt`` False) and the
          origin defaults to ``background``: the report comes from a voice session
          start, so only the inline status and the Privacy row show it, never the
          floating card. A live state that no longer reads granted is reported
          through the plain path (its real reason).

        ``origin`` (``user`` or ``background``) overrides the family default; a value
        outside the event vocabulary is ignored. Anything else (another permission,
        a state that does not fit) falls back to a plain non-interactive
        :meth:`ensure`. The answer is the view of that episode: neither reason is ever
        a grant, and a following ``ensure`` still answers GRANTED while the state
        reads granted. Never raises.
        """
        perm = PermissionId(permission)
        family = PANE_FAMILY[perm]
        tgt = _fixed_target(target) if family is PermissionId.AUTOMATION else ""
        try:
            port = self._port()
            if not self._is_darwin(port):
                return self._not_required(perm)
            if family is PermissionId.AUTOMATION:
                return self._report_refused_automation(
                    port, perm, feature, tgt, trace_id, reason, origin
                )
            if family not in _RESTART_HINT_FAMILIES or reason not in (None, "restart_hint"):
                return self.ensure(perm, feature=feature, interactive=False, target=tgt or None)
            if feature not in PERMISSION_FEATURES:
                log.warning("report_failed_use() was called with the unknown feature %r.", feature)
                feature = ""
            now = self._clock()
            state = self._read_state(port, perm, tgt, fresh=True, deep=not _on_event_loop_thread())
            promoted = self._promoted(feature, family)
            if state not in _READY_STATES and not promoted:
                return self.ensure(perm, feature=feature, interactive=False, trace_id=trace_id)
            item = _Item(requested=perm, family=family, target=tgt, state=state)
            episode = self._open_episode(
                feature, [item], _failed_use_origin(origin, "user") == "user", trace_id, now
            )
            try:
                slot = episode.slots[item.slot_key]
                slot.state = state
                slot.restart_hint = True
                item.slot = slot
            finally:
                with self._lock:
                    episode.starting -= 1
            self._emit_episode(episode)
            self._ensure_watcher()
            return self._result(item, self._classify(slot, state, now))
        except Exception:  # noqa: BLE001 - reporting a failure never raises
            log.debug("report_failed_use(%s) failed.", feature, exc_info=True)
            return self._unavailable(perm, tgt)

    def _report_refused_automation(
        self,
        port: Any,
        perm: PermissionId,
        feature: str,
        target: str,
        trace_id: UUID | str | None,
        reason: str | None,
        origin: str | None,
    ) -> EnsureResult:
        """The Automation branch of :meth:`report_failed_use`: a send was refused (-1743).

        Reads the live state once. Not granted any more: the plain non-interactive
        :meth:`ensure` describes it (its real reason, still never a request). Granted:
        marks the player's slot ``refused_use`` in the (feature, player) episode and
        publishes ONE ``needs_settings`` / ``blocked`` event (a repeat of the same
        report is deduplicated by ``_emit_episode``, so a refusal on every session
        start stays one event). The caller holds no service lock, and the one read is
        the guarded, hard-timeout Automation read, never a native request.
        """
        if not target:
            # Only the fixed player table is ever acted on, echoed or published.
            log.debug("A failed Automation use was reported for a target that is not a player.")
            return self._unavailable(perm, "")
        if reason not in (None, "needs_settings"):
            log.debug("A failed Automation use was reported with a reason it cannot carry.")
            return self.ensure(
                perm, feature=feature, interactive=False, target=target, trace_id=trace_id
            )
        if feature not in PERMISSION_FEATURES:
            log.warning("report_failed_use() was called with the unknown feature %r.", feature)
            feature = ""
        now = self._clock()
        state = self._read_state(port, perm, target, fresh=True)
        if state not in _READY_STATES:
            return self.ensure(
                perm, feature=feature, interactive=False, target=target, trace_id=trace_id
            )
        item = _Item(requested=perm, family=PermissionId.AUTOMATION, target=target, state=state)
        # An episode this player's ask left behind and that is already answered closes
        # first, so the background report below never inherits its user origin (and
        # with it the floating card).
        self._note_ready(item)
        user = _failed_use_origin(origin, "background") == "user"
        episode = self._open_episode(feature, [item], user, trace_id, now)
        try:
            slot = episode.slots[item.slot_key]
            slot.state = state
            slot.refused_use = True
            item.slot = slot
        finally:
            with self._lock:
                episode.starting -= 1
        self._emit_episode(episode)
        self._ensure_watcher()
        return self._result(item, self._classify(slot, state, now))

    def report_use_ok(
        self,
        permission: PermissionId | str,
        *,
        feature: str,
        target: str | None = None,
    ) -> bool:
        """A consumer reports that a REAL use of a permission worked: the mark is stale.

        The counterpart of :meth:`report_failed_use` for Automation: a send to the
        player landed (the volume command ran), so the ``needs_settings`` episode
        that an earlier ``-1743`` opened is over. It clears the ``refused_use`` mark
        of that (feature, player) slot and lets the episode close like any other
        (``PermissionResolved(granted=True)``). A landed send is stronger evidence than
        the probe, so the slot reads granted from here on.

        This is the ONLY thing that ends such an episode early: a probe that reads
        granted (the watcher, a status read, ``ensure``) never does, because that
        probe is what lied. ``note_reset`` and the ten minute TTL end it too.

        Returns ``True`` when a mark was cleared. Another permission, an unknown
        player or a feature with nothing to clear returns ``False`` (the restart hints
        of Screen Recording and Input Monitoring end through ``note_reset``). Reads no
        state, touches no port, makes no request, never raises (an unknown permission
        id is the one ``ValueError``, like everywhere else).
        """
        perm = PermissionId(permission)
        family = PANE_FAMILY[perm]
        tgt = _fixed_target(target) if family is PermissionId.AUTOMATION else ""
        if family is not PermissionId.AUTOMATION or not tgt:
            return False
        if feature not in PERMISSION_FEATURES:
            feature = ""
        try:
            cleared: list[_Episode] = []
            with self._lock:
                for episode in self._episodes.values():
                    slot = episode.slots.get((family, tgt))
                    if episode.closed or episode.feature != feature or slot is None:
                        continue
                    if slot.refused_use:
                        slot.refused_use = False
                        slot.state = PermissionState.GRANTED
                        cleared.append(episode)
            for episode in cleared:
                self._emit_episode(episode)
            return bool(cleared)
        except Exception:  # noqa: BLE001 - tidying a status line never fails a real send
            log.debug("report_use_ok(%s) failed.", feature, exc_info=True)
            return False

    def _promoted(self, feature: str, family: PermissionId) -> bool:
        """Whether the user is known to have left to Settings and come back for this."""
        with self._lock:
            return any(
                slot.promoted
                for episode in self._episodes.values()
                if episode.feature == feature
                for slot in episode.slots.values()
                if slot.family is family
            )


# ----------------------------------------------------------------------
# Module helpers and the process singleton
# ----------------------------------------------------------------------


def _fixed_target(target: str | None) -> str:
    """The Automation target if it is one of the fixed players, else ``""``.

    Nothing a caller invents (an app name, a path, another bundle id) is ever echoed
    into a result, an episode or an event.
    """
    return target if target and _player_name(target) is not None else ""


def _failed_use_origin(origin: str | None, default: str) -> str:
    """The episode origin of a failed-use report: the caller's, if it is a known one."""
    if origin is None:
        return default
    if origin in PERMISSION_NEEDED_ORIGINS:
        return origin
    log.debug("report_failed_use() was called with an origin outside the event vocabulary.")
    return default


def _on_event_loop_thread() -> bool:
    """Whether the calling thread is running an asyncio loop (it must never block)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # Not an error: "no running loop in this thread" is exactly the answer.
        return False
    return True


def _start_daemon_thread(work: Callable[[], None]) -> None:
    """The default ``ask_runner``: run ``work`` on its own daemon thread."""
    threading.Thread(target=work, name="permission-automation-ask", daemon=True).start()


def _coerce_trace(trace_id: UUID | str | None) -> UUID:
    if isinstance(trace_id, UUID):
        return trace_id
    if isinstance(trace_id, str):
        try:
            return UUID(trace_id)
        except ValueError:
            log.debug("Ignoring a trace id that is not a UUID.")
    return uuid4()


def _run_callbacks(callbacks: Sequence[Callable[[], None]]) -> None:
    """Call every listener; one that raises does not stop the others."""
    for callback in callbacks:
        try:
            callback()
        except Exception:  # noqa: BLE001 - a listener must never break the watcher or the loop
            log.warning("A permission listener raised.", exc_info=True)


def _log_publish_failure(future: Any) -> None:
    if future.cancelled():
        return
    error = future.exception()
    if error is not None:
        log.debug("Publishing a permission event failed: %r", error)


_SERVICE: PermissionService | None = None
_SERVICE_LOCK = threading.Lock()


def get_permission_service() -> PermissionService:
    """The lazy process-wide service. Constructing it does no I/O."""
    global _SERVICE
    service = _SERVICE
    if service is None:
        with _SERVICE_LOCK:
            if _SERVICE is None:
                _SERVICE = PermissionService()
            service = _SERVICE
    return service


def _reset_for_tests() -> None:
    """Drop the singleton and stop its watcher (the autouse test fixture calls this)."""
    global _SERVICE
    with _SERVICE_LOCK:
        service, _SERVICE = _SERVICE, None
    if service is not None:
        service._shutdown()


def attach_bus(bus: Any | None, loop: asyncio.AbstractEventLoop | None) -> None:
    """:meth:`PermissionService.attach_bus` on the process singleton."""
    get_permission_service().attach_bus(bus, loop)


def check(permission: PermissionId | str, *, target: str | None = None) -> PermissionState:
    """:meth:`PermissionService.check` on the process singleton."""
    return get_permission_service().check(permission, target=target)


def ensure(permission: PermissionId | str, **kwargs: Any) -> EnsureResult:
    """:meth:`PermissionService.ensure` on the process singleton."""
    return get_permission_service().ensure(permission, **kwargs)


async def ensure_async(permission: PermissionId | str, **kwargs: Any) -> EnsureResult:
    """:meth:`PermissionService.ensure_async` on the process singleton."""
    return await get_permission_service().ensure_async(permission, **kwargs)


def ensure_all(permissions: Iterable[PermissionId | str], **kwargs: Any) -> list[EnsureResult]:
    """:meth:`PermissionService.ensure_all` on the process singleton."""
    return get_permission_service().ensure_all(permissions, **kwargs)


def outstanding() -> list[Episode]:
    """:meth:`PermissionService.outstanding` on the process singleton."""
    return get_permission_service().outstanding()


def add_listener(
    permission: PermissionId | str, callback: Callable[[], None]
) -> Callable[[], None]:
    """:meth:`PermissionService.add_listener` on the process singleton."""
    return get_permission_service().add_listener(permission, callback)


def open_settings(permission: PermissionId | str) -> bool:
    """:meth:`PermissionService.open_settings` on the process singleton."""
    return get_permission_service().open_settings(permission)


__all__ = [
    "AppInfo",
    "Episode",
    "EnsureResult",
    "PermissionOutcome",
    "PermissionService",
    "add_listener",
    "agent_detail_for",
    "attach_bus",
    "check",
    "ensure",
    "ensure_all",
    "ensure_async",
    "get_permission_service",
    "open_settings",
    "outstanding",
    "user_detail_for",
]
