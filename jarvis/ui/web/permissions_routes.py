"""Local-user API for macOS privacy permissions: a passive snapshot and the ask flows.

Nothing here is a wall. ``GET`` routes only READ: they never prompt and never
block (the Automation probe and the episode refresh run on a bounded daemon
thread). Asking is a user gesture: ``POST /{id}/request`` hands the permission to
the just-in-time service (``jarvis.platform.permission_service``), which asks macOS
at most once per episode, and answers at once with the outcome. A decision made in
the OS dialog arrives later, through ``PermissionResolved`` on the bus.

Snapshot v2 (``GET /status``)::

    {platform, supported, headless, app_identity{...}, outside_installed_app,
     permissions: [{id, label, status, used_for, can_request, can_open_settings,
                    can_reset, restart_hint, detail, settings_path}],
     needed: [open episodes]}

The Automation row is computed only while ``[ducking].enabled`` is on or the caller
passes ``?include=automation``: reading it asks a running player, which a user who
never switched the feature on should never meet. Screen Recording is the SHALLOW
preflight here (no window enumeration); that preflight is frozen per process and can
only go stale NEGATIVE (BUG-161), so a grant given in System Settings shows up at
once only while an episode watches it. Off macOS every row is ``not_required`` and
``needed`` is empty. ``event_posting`` is an alias of ``accessibility`` and has no
row of its own.

The key sets of the TypedDicts below are the Python side of an AP-4 parity: a test
reads them back against the real answer and against the TypeScript twin.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Final, TypedDict, cast

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, field_validator

from jarvis.core.events import PERMISSION_FEATURES
from jarvis.platform import permissions as _permissions_module
from jarvis.platform.permission_service import (
    AppInfo,
    EnsureResult,
    PermissionOutcome,
    PermissionService,
    agent_detail_for,
    get_permission_service,
    user_detail_for,
)
from jarvis.platform.permissions import (
    AUTOMATION_TARGETS,
    PANE_FAMILY,
    PermissionId,
    PermissionState,
    settings_path_text,
)

from .control_auth import require_control_key_or_session

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/permissions",
    tags=["permissions"],
    dependencies=[Depends(require_control_key_or_session)],
)


# ----------------------------------------------------------------------
# Wire shapes (the Python side of the AP-4 parity)
# ----------------------------------------------------------------------


class AppIdentityBlock(TypedDict):
    app_name: str
    bundle_id: str | None
    bundle_path: str | None
    launched_as_bundle: bool
    stable: bool


class PermissionRow(TypedDict):
    id: str
    label: str
    status: str
    used_for: list[str]
    can_request: bool
    can_open_settings: bool
    can_reset: bool
    restart_hint: bool
    detail: str
    settings_path: str | None


class NeededEpisode(TypedDict):
    """One open episode: the keys of ``permission_service.Episode.as_dict``."""

    permissions: list[str]
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


class PermissionsSnapshot(TypedDict):
    platform: str
    supported: bool
    headless: bool
    app_identity: AppIdentityBlock
    outside_installed_app: bool
    permissions: list[PermissionRow]
    needed: list[NeededEpisode]


class EnsurePayload(TypedDict):
    """The answer of ``POST /{id}/request``: an ``EnsureResult`` as JSON."""

    permission: str
    outcome: str
    granted: bool
    state: str
    asked: bool
    outside_installed_app: bool
    reason: str
    can_prompt: bool
    can_open_settings: bool
    target: str
    user_detail: str
    agent_detail: str


class OperationPayload(TypedDict):
    """The answer of ``/open-settings`` and ``/reset`` (and any dry run)."""

    ok: bool
    permission_id: str
    action: str
    performed: bool
    dry_run: bool
    message: str
    permission: PermissionRow | None


class RateLimitedPayload(TypedDict):
    """The stable body of a 429."""

    error: str
    scope: str
    action: str
    permission_id: str
    retry_after_s: int


# ----------------------------------------------------------------------
# Row metadata
# ----------------------------------------------------------------------

# The rows a person sees, in order. EVENT_POSTING is an alias of Accessibility for
# asking and has no row; the Keychain row stays (it names the storage kind and
# offers "Try again").
_ROW_ORDER: Final[tuple[PermissionId, ...]] = (
    PermissionId.MICROPHONE,
    PermissionId.SCREEN_RECORDING,
    PermissionId.ACCESSIBILITY,
    PermissionId.INPUT_MONITORING,
    PermissionId.AUTOMATION,
    PermissionId.CREDENTIAL_STORE,
)

# English fallback labels; the UI renders its own i18n per row id.
_ROW_LABELS: Final[dict[PermissionId, str]] = {
    PermissionId.MICROPHONE: "Microphone",
    PermissionId.SCREEN_RECORDING: "Screen Recording",
    PermissionId.ACCESSIBILITY: "Accessibility",
    PermissionId.INPUT_MONITORING: "Input Monitoring",
    PermissionId.AUTOMATION: "Automation (Music & Spotify)",
    PermissionId.CREDENTIAL_STORE: "Keychain (API keys)",
}

# Which features use a permission, in the vocabulary of ``PERMISSION_FEATURES``. The
# FIRST one is the feature a request without a ``feature`` is attributed to. The
# Keychain has no feature in that vocabulary: the UI names the row by its id.
_USED_FOR: Final[dict[PermissionId, tuple[str, ...]]] = {
    PermissionId.MICROPHONE: ("voice", "dictation", "wake_word", "browser_voice"),
    PermissionId.SCREEN_RECORDING: ("computer_use", "screen_context", "appshot"),
    PermissionId.ACCESSIBILITY: ("computer_use", "window_control", "dictation_insert"),
    PermissionId.INPUT_MONITORING: ("global_shortcuts",),
    PermissionId.AUTOMATION: ("audio_ducking",),
    PermissionId.CREDENTIAL_STORE: (),
}

_READY: Final = frozenset({PermissionState.GRANTED, PermissionState.NOT_REQUIRED})
# Nothing is decided yet: macOS can still be asked.
_UNDECIDED: Final = frozenset({PermissionState.NOT_DETERMINED, PermissionState.NOT_GRANTED})
# The states "Ask again" (a tccutil reset) can do something about. A restricted or
# unavailable permission is not stranded, it is out of reach; a granted one must
# never be reset.
_RESETTABLE: Final = frozenset(
    {PermissionState.NOT_DETERMINED, PermissionState.NOT_GRANTED, PermissionState.DENIED}
)
_REASON_OF_STATE: Final[dict[PermissionState, str]] = {
    PermissionState.NOT_DETERMINED: "not_determined",
    PermissionState.NOT_GRANTED: "needs_settings",
    PermissionState.DENIED: "denied",
    PermissionState.RESTRICTED: "restricted",
    PermissionState.UNAVAILABLE: "unavailable",
}
_AUTOMATION_PLAYERS: Final = tuple(bundle_id for _name, bundle_id in AUTOMATION_TARGETS)

_NOT_REQUIRED_DETAIL: Final = "This operating system does not require a macOS privacy permission."
_AUTOMATION_NOTE: Final = "Checked only while Music or Spotify is running."
_AUTOMATION_NO_ANSWER: Final = "The Automation check did not answer in time. " + _AUTOMATION_NOTE
_AUTOMATION_NO_PLAYER: Final = "Music and Spotify are not installed, so there is nothing to allow."
_KEYCHAIN_DECLINED: Final = (
    "Keychain access was declined, so API keys are kept in a local file for now. "
    "Allow access to store them encrypted in the macOS Keychain."
)

# How long a GET waits for the Automation probe / the episode refresh, and a POST
# /request for a blocking Automation ask, before it answers without them. The work
# keeps running on its daemon thread; its late answer is discarded.
_AUTOMATION_PROBE_TIMEOUT_S: Final = 2.5
_REFRESH_TIMEOUT_S: Final = 2.5
_AUTOMATION_ASK_WAIT_S: Final = 2.0

# Rate limits for the two routes that can make the OS show something: /request and
# /open-settings. A cooldown per (action, permission) plus a global cap, so a UI bug
# or another local page cannot turn them into a prompt loop.
_PERMISSION_COOLDOWN_S: Final = 5.0
_GLOBAL_MAX_CALLS: Final = 20
_GLOBAL_WINDOW_S: Final = 60.0


# ----------------------------------------------------------------------
# Rate limiting and bounded calls
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Refusal:
    scope: str
    retry_after_s: float


class _RateLimiter:
    """A cooldown per (action, permission) and a sliding-window cap over all of them."""

    def __init__(
        self,
        *,
        cooldown_s: float = _PERMISSION_COOLDOWN_S,
        global_max: int = _GLOBAL_MAX_CALLS,
        global_window_s: float = _GLOBAL_WINDOW_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cooldown_s = cooldown_s
        self._global_max = global_max
        self._global_window_s = global_window_s
        self._clock = clock
        self._lock = threading.Lock()
        self._last: dict[tuple[str, str], float] = {}
        self._recent: deque[float] = deque()

    def admit(self, action: str, permission: PermissionId) -> _Refusal | None:
        """``None`` and the call is counted, or why it is refused (nothing is counted)."""
        now = self._clock()
        key = (action, permission.value)
        with self._lock:
            while self._recent and now - self._recent[0] >= self._global_window_s:
                self._recent.popleft()
            last = self._last.get(key)
            if last is not None and now - last < self._cooldown_s:
                return _Refusal("permission", self._cooldown_s - (now - last))
            if len(self._recent) >= self._global_max:
                return _Refusal("global", self._global_window_s - (now - self._recent[0]))
            self._last[key] = now
            self._recent.append(now)
        return None


@dataclass(frozen=True, slots=True)
class _BoundedResult:
    finished: bool
    value: Any = None
    # True: a previous call of this slot is still running, so nothing was started.
    busy: bool = False
    # True: the call raised (logged at debug); there is no value.
    failed: bool = False

    @property
    def ok(self) -> bool:
        return self.finished and not self.failed


class _Bounded:
    """Runs ONE call at a time on a daemon thread and waits for it at most ``timeout_s``.

    A native Apple Events probe can hang for a running player that has no window
    (Apple forums thread 666528) and an Automation ask blocks until the dialog is
    answered. A route must answer anyway: the call keeps its thread, the route gets
    ``finished=False``, and while that thread lives no second one is started (a
    hung probe must not pile up threads). A late answer is discarded.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def run(self, call: Callable[[], Any], timeout_s: float) -> _BoundedResult:
        box: dict[str, Any] = {}

        def target() -> None:
            try:
                box["value"] = call()
            except Exception:  # noqa: BLE001 - the thread must never die loudly, the route reports it
                log.debug("The bounded %s call failed.", self._name, exc_info=True)
                box["failed"] = True

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return _BoundedResult(finished=False, busy=True)
            thread = threading.Thread(target=target, name=f"permissions-{self._name}", daemon=True)
            self._thread = thread
            thread.start()
        thread.join(timeout_s)
        if thread.is_alive():
            log.debug("The bounded %s call did not finish within %.1f s.", self._name, timeout_s)
            return _BoundedResult(finished=False)
        return _BoundedResult(finished=True, value=box.get("value"), failed=bool(box.get("failed")))


class _Runtime:
    """The mutable state of the permission routes of ONE app (rate limits, bounded calls)."""

    def __init__(self) -> None:
        self.limiter = _RateLimiter()
        self.automation_probe = _Bounded("automation-probe")
        self.automation_ask = _Bounded("automation-ask")
        self.refresh = _Bounded("episode-refresh")


_RUNTIME_LOCK = threading.Lock()


def _runtime(request: Request) -> _Runtime:
    state = request.app.state
    runtime = getattr(state, "permissions_runtime", None)
    if runtime is None:
        with _RUNTIME_LOCK:
            runtime = getattr(state, "permissions_runtime", None)
            if runtime is None:
                runtime = _Runtime()
                state.permissions_runtime = runtime
    return runtime


def _service(request: Request) -> PermissionService:
    """The permission service: ``app.state.permission_service`` when a test injected one."""
    injected = getattr(request.app.state, "permission_service", None)
    return injected if injected is not None else get_permission_service()


def _ducking_enabled(request: Request) -> bool:
    """Whether the user switched "Mute music while dictating" on (``[ducking].enabled``)."""
    state = request.app.state
    config = getattr(state, "config", None) or getattr(state, "cfg", None)
    return bool(getattr(getattr(config, "ducking", None), "enabled", False))


# ----------------------------------------------------------------------
# Reading: rows and the snapshot
# ----------------------------------------------------------------------


def _worst_automation(states: list[PermissionState]) -> PermissionState:
    """One state for every scriptable player: the strictest answer wins.

    A player that is not installed (``NOT_REQUIRED``) takes no part. A running player
    that was never asked, and a player that is not running at all, both read
    ``NOT_DETERMINED``: the port cannot tell "unknown" from "not asked" for a player
    that is closed, which is why the row says it is checked while the player runs.
    """
    relevant = [state for state in states if state is not PermissionState.NOT_REQUIRED]
    if not relevant:
        return PermissionState.NOT_REQUIRED
    if PermissionState.DENIED in relevant:
        return PermissionState.DENIED
    if any(state in _UNDECIDED for state in relevant):
        return PermissionState.NOT_DETERMINED
    for wanted in (PermissionState.RESTRICTED, PermissionState.UNAVAILABLE):
        if wanted in relevant:
            return wanted
    return PermissionState.GRANTED


def _read_automation(service: PermissionService) -> PermissionState:
    """Read-only, per player: never the aggregate read (it rewrites the consent record)."""
    return _worst_automation(
        [
            service.check(PermissionId.AUTOMATION, target=bundle_id)
            for bundle_id in _AUTOMATION_PLAYERS
        ]
    )


def _automation_state(
    service: PermissionService, runtime: _Runtime
) -> tuple[PermissionState, bool]:
    """``(state, answered)``; a probe that did not answer in time reads UNAVAILABLE."""
    result = runtime.automation_probe.run(
        lambda: _read_automation(service), _AUTOMATION_PROBE_TIMEOUT_S
    )
    if result.ok and isinstance(result.value, PermissionState):
        return result.value, True
    return PermissionState.UNAVAILABLE, False


def _row_detail(
    permission: PermissionId,
    state: PermissionState,
    *,
    darwin: bool,
    restart_hint: bool,
    answered: bool,
) -> str:
    """One English sentence for the row, from fixed templates only (never OS text)."""
    if state is PermissionState.NOT_REQUIRED:
        if darwin and permission is PermissionId.AUTOMATION:
            return _AUTOMATION_NO_PLAYER
        return _NOT_REQUIRED_DETAIL
    if state is PermissionState.GRANTED:
        return ""
    if restart_hint:
        return user_detail_for(permission, "restart_hint")
    if permission is PermissionId.AUTOMATION and not answered:
        return _AUTOMATION_NO_ANSWER
    if permission is PermissionId.CREDENTIAL_STORE and state is PermissionState.NOT_GRANTED:
        return _KEYCHAIN_DECLINED
    sentence = user_detail_for(permission, _REASON_OF_STATE.get(state, "unavailable"))
    if permission is PermissionId.AUTOMATION:
        return f"{sentence} {_AUTOMATION_NOTE}"
    return sentence


def _build_row(
    service: PermissionService,
    runtime: _Runtime,
    info: AppInfo,
    permission: PermissionId,
    restart_hint_for: frozenset[str],
) -> PermissionRow:
    answered = True
    if permission is PermissionId.AUTOMATION:
        state, answered = _automation_state(service, runtime)
    else:
        state = service.check(permission)
    darwin = info.platform == "darwin"
    # A desktop session is needed for anything that opens a dialog or a window.
    interactive = darwin and not info.headless
    pane = settings_path_text(permission)
    if permission is PermissionId.CREDENTIAL_STORE:
        # The Keychain has no request call; "Try again" replays the failed read.
        can_request = interactive and state is PermissionState.NOT_GRANTED
    elif permission is PermissionId.AUTOMATION:
        can_request = interactive and answered and service.can_request(permission, state)
    else:
        can_request = interactive and service.can_request(permission, state)
    return {
        "id": permission.value,
        "label": _ROW_LABELS[permission],
        "status": state.value,
        "used_for": list(_USED_FOR[permission]),
        "can_request": can_request,
        # Opening a pane is not a prompt. The port still refuses it outside the
        # installed app (its own gate, retired with the port cleanup), so the row
        # does not promise what the route would answer with a 409.
        "can_open_settings": interactive and info.stable and pane is not None,
        # tccutil is scoped to this app's own bundle id, so only the installed app
        # may reset (P6); a developer run must not wipe the installed app's rows.
        "can_reset": (
            interactive
            and info.stable
            and permission is not PermissionId.CREDENTIAL_STORE
            and state in _RESETTABLE
        ),
        "restart_hint": permission.value in restart_hint_for and state not in _READY,
        "detail": _row_detail(
            permission,
            state,
            darwin=darwin,
            restart_hint=permission.value in restart_hint_for,
            answered=answered,
        ),
        # The textual path of the pane; there is no System Settings off macOS.
        "settings_path": pane if darwin else None,
    }


def _refresh_edges(service: PermissionService, runtime: _Runtime) -> None:
    """Re-read the open episodes so a resolve edge is published from this path too.

    The episode watcher does it every two seconds; a status read does it as well,
    so an edge is emitted whichever path sees it first. Only when something is open,
    and bounded: an Automation episode reads a running player.
    """
    if not service.outstanding():
        return
    result = runtime.refresh.run(service.refresh_episodes, _REFRESH_TIMEOUT_S)
    if not result.finished:
        log.debug("The episode refresh did not finish in time; the watcher keeps going.")


def _restart_hints(needed: list[NeededEpisode]) -> frozenset[str]:
    return frozenset(
        permission
        for episode in needed
        if episode["reason"] == "restart_hint"
        for permission in episode["permissions"]
    )


def _needed(service: PermissionService) -> list[NeededEpisode]:
    return [cast("NeededEpisode", episode.as_dict()) for episode in service.outstanding()]


def _build_snapshot(
    service: PermissionService, runtime: _Runtime, *, include_automation: bool
) -> PermissionsSnapshot:
    info = service.app_info()
    _refresh_edges(service, runtime)
    needed = _needed(service)
    hints = _restart_hints(needed)
    rows = [
        _build_row(service, runtime, info, permission, hints)
        for permission in _ROW_ORDER
        if include_automation or permission is not PermissionId.AUTOMATION
    ]
    return {
        "platform": info.platform,
        "supported": info.platform == "darwin",
        "headless": info.headless,
        "app_identity": {
            "app_name": info.app_name,
            "bundle_id": info.bundle_id,
            "bundle_path": info.bundle_path,
            "launched_as_bundle": info.launched_as_bundle,
            "stable": info.stable,
        },
        "outside_installed_app": info.outside_installed_app,
        "permissions": rows,
        "needed": needed,
    }


def _single_row(
    service: PermissionService, runtime: _Runtime, permission: PermissionId
) -> PermissionRow:
    """One row without the full snapshot (the card and the Privacy page poll this)."""
    family = PANE_FAMILY[permission]
    info = service.app_info()
    hints = _restart_hints(_needed(service))
    return _build_row(service, runtime, info, family, hints)


@router.get("/status", summary="Inspect macOS privacy permissions", response_model=None)
def get_permissions_status(
    request: Request,
    include: str | None = Query(
        default=None,
        description="Comma-separated extras; 'automation' adds the Automation row.",
    ),
) -> PermissionsSnapshot:
    """Return the passive permission snapshot. It never prompts and never blocks."""
    wanted = {part.strip() for part in (include or "").split(",")}
    include_automation = "automation" in wanted or _ducking_enabled(request)
    return _build_snapshot(
        _service(request), _runtime(request), include_automation=include_automation
    )


@router.get("/{permission_id}", summary="Read one privacy permission row", response_model=None)
def get_permission(permission_id: PermissionId, request: Request) -> PermissionRow:
    """One cheap row; also re-reads open episodes so a resolve edge is published here.

    ``event_posting`` is an alias of ``accessibility`` and answers with that row.
    """
    service = _service(request)
    runtime = _runtime(request)
    _refresh_edges(service, runtime)
    return _single_row(service, runtime, permission_id)


# ----------------------------------------------------------------------
# Acting: request, open settings, reset
# ----------------------------------------------------------------------


class PermissionRequestBody(BaseModel):
    """Optional body of ``POST /{id}/request``. Unknown keys are refused (a typo must
    never silently drop a consent flag)."""

    model_config = ConfigDict(extra="forbid")

    # True ONLY after the person confirmed that macOS will record the grant for the
    # app that started Jarvis (a terminal, an IDE) because Jarvis is not the
    # installed app. Never a default, never inferred.
    allow_outside_app: bool = False
    # The feature the ask is attributed to (``PERMISSION_FEATURES``); defaults to the
    # first feature that uses the permission.
    feature: str | None = None
    # Automation only: the bundle id of ONE player (``AUTOMATION_TARGETS``). Without
    # it the route picks the first player that can still be asked.
    target: str | None = None

    @field_validator("feature")
    @classmethod
    def _known_feature(cls, value: str | None) -> str | None:
        if value is not None and value not in PERMISSION_FEATURES:
            raise ValueError(f"feature must be one of: {', '.join(PERMISSION_FEATURES)}")
        return value

    @field_validator("target")
    @classmethod
    def _known_player(cls, value: str | None) -> str | None:
        if value is not None and value not in _AUTOMATION_PLAYERS:
            raise ValueError(f"target must be one of: {', '.join(_AUTOMATION_PLAYERS)}")
        return value


def _operation(
    permission_id: PermissionId,
    action: str,
    *,
    ok: bool,
    message: str,
    performed: bool = False,
    dry_run: bool = False,
    row: PermissionRow | None = None,
) -> OperationPayload:
    return {
        "ok": ok,
        "permission_id": permission_id.value,
        "action": action,
        "performed": performed,
        "dry_run": dry_run,
        "message": message,
        "permission": row,
    }


def _operation_response(payload: OperationPayload) -> Any:
    if payload["ok"]:
        return payload
    return JSONResponse(status_code=409, content=payload)


def _rate_limited(refusal: _Refusal, action: str, permission_id: PermissionId) -> JSONResponse:
    retry_after = max(1, math.ceil(refusal.retry_after_s))
    body: RateLimitedPayload = {
        "error": "rate_limited",
        "scope": refusal.scope,
        "action": action,
        "permission_id": permission_id.value,
        "retry_after_s": retry_after,
    }
    return JSONResponse(status_code=429, content=body, headers={"Retry-After": str(retry_after)})


def _ensure_payload(result: EnsureResult) -> EnsurePayload:
    return {
        "permission": result.permission.value,
        "outcome": result.outcome.value,
        "granted": result.granted,
        "state": result.state.value,
        "asked": result.asked,
        "outside_installed_app": result.outside_installed_app,
        "reason": result.reason,
        "can_prompt": result.can_prompt,
        "can_open_settings": result.can_open_settings,
        "target": result.target,
        "user_detail": result.user_detail,
        "agent_detail": result.agent_detail,
    }


def _asking_result(permission: PermissionId, target: str, *, asked: bool) -> EnsureResult:
    """PENDING, for an Automation ask whose dialog is still open when the route answers."""
    family = PANE_FAMILY[permission]
    return EnsureResult(
        permission=permission,
        outcome=PermissionOutcome.PENDING,
        state=PermissionState.NOT_DETERMINED,
        asked=asked,
        outside_installed_app=False,
        agent_detail=agent_detail_for(family, "not_determined", target=target, asking=True),
        user_detail=user_detail_for(family, "not_determined", target=target, asking=True),
        reason="not_determined",
        can_prompt=False,
        can_open_settings=settings_path_text(family) is not None,
        target=target,
    )


def _unavailable_result(permission: PermissionId, target: str) -> EnsureResult:
    family = PANE_FAMILY[permission]
    return EnsureResult(
        permission=permission,
        outcome=PermissionOutcome.UNAVAILABLE,
        state=PermissionState.UNAVAILABLE,
        asked=False,
        outside_installed_app=False,
        agent_detail=agent_detail_for(family, "unavailable", target=target),
        user_detail=user_detail_for(family, "unavailable", target=target),
        reason="unavailable",
        target=target,
    )


def _ask_automation(
    service: PermissionService,
    permission: PermissionId,
    *,
    feature: str,
    target: str | None,
    allow_outside_app: bool,
) -> EnsureResult:
    """Ask for ONE player: the named one, else the first that can still be asked.

    Blocking (the consent runner returns once the dialog is answered or killed), so
    the route runs it through :class:`_Bounded`. A player that is not running is not
    launched and not asked (the runner is guarded), which reads UNAVAILABLE here; the
    next player is tried.
    """
    if target:
        candidates = [target]
    else:
        undecided = [
            bundle_id
            for bundle_id in _AUTOMATION_PLAYERS
            if service.check(PermissionId.AUTOMATION, target=bundle_id) in _UNDECIDED
        ]
        candidates = undecided or list(_AUTOMATION_PLAYERS[:1])
    result = _unavailable_result(permission, "")
    for bundle_id in candidates:
        result = service.ensure(
            permission,
            feature=feature,
            interactive=True,
            wait_s=0.0,
            target=bundle_id,
            allow_outside_app=allow_outside_app,
        )
        # ``asked`` is true even when the guarded script found the player closed and
        # showed nothing; only the outcome says whether this player is worth stopping at.
        if result.outcome is not PermissionOutcome.UNAVAILABLE:
            break
    return result


def _keychain_result(state: PermissionState, *, asked: bool) -> EnsureResult:
    """The Keychain "Try again" answer, in the shape of every other ask."""
    permission = PermissionId.CREDENTIAL_STORE
    if state in _READY:
        outcome = (
            PermissionOutcome.NOT_REQUIRED
            if state is PermissionState.NOT_REQUIRED
            else PermissionOutcome.GRANTED
        )
        return EnsureResult(permission, outcome, state, asked, False, "", "")
    declined = state is PermissionState.NOT_GRANTED
    reason = "denied" if declined else "unavailable"
    return EnsureResult(
        permission=permission,
        outcome=PermissionOutcome.DENIED if declined else PermissionOutcome.UNAVAILABLE,
        state=state,
        asked=asked,
        outside_installed_app=False,
        agent_detail=agent_detail_for(permission, reason),
        user_detail=_KEYCHAIN_DECLINED if declined else user_detail_for(permission, reason),
        reason=reason,
        can_prompt=declined,
        can_open_settings=False,
    )


def _retry_keychain(service: PermissionService) -> EnsureResult:
    """Replay the failed Keychain read, the one supported way to make macOS ask again."""
    state = service.check(PermissionId.CREDENTIAL_STORE)
    if state is not PermissionState.NOT_GRANTED:
        return _keychain_result(state, asked=False)
    port = _permissions_module.get_system_permission_port()
    try:
        port.request_native(PermissionId.CREDENTIAL_STORE)
    except Exception:  # noqa: BLE001 - the replay is best-effort, the state read below is the truth
        log.debug("Replaying the Keychain read failed.", exc_info=True)
    service.invalidate(PermissionId.CREDENTIAL_STORE)
    return _keychain_result(service.check(PermissionId.CREDENTIAL_STORE), asked=True)


@router.post(
    "/{permission_id}/request",
    summary="Ask macOS for a permission",
    openapi_extra={"x-jarvis-dangerous": True},
    response_model=None,
)
def request_permission(
    permission_id: PermissionId,
    request: Request,
    body: Annotated[PermissionRequestBody | None, Body()] = None,
    dry_run: bool = Query(default=False),
) -> Any:
    """Ask for ONE permission from a user gesture and answer with the outcome at once.

    The just-in-time service asks macOS at most once per episode and never refuses
    an action because its own preflight said "not granted"; the answer is its
    ``EnsureResult`` (PENDING while a system dialog may be open, NEEDS_SETTINGS when
    the person has to flip a switch, DENIED once macOS will not ask again). The
    decision itself arrives later as ``PermissionResolved``. Outside the installed
    app nothing is asked unless the body says ``allow_outside_app`` after the person
    confirmed which app receives the grant. Rate limited (429).
    """
    options = body if body is not None else PermissionRequestBody()
    label = _ROW_LABELS.get(PANE_FAMILY[permission_id], permission_id.value)
    if dry_run:
        return _operation(
            permission_id,
            "request",
            ok=True,
            dry_run=True,
            message=f"Would request {label} access.",
        )
    runtime = _runtime(request)
    refusal = runtime.limiter.admit("request", PANE_FAMILY[permission_id])
    if refusal is not None:
        return _rate_limited(refusal, "request", permission_id)
    service = _service(request)
    family = PANE_FAMILY[permission_id]
    if family is PermissionId.CREDENTIAL_STORE:
        return _ensure_payload(_retry_keychain(service))
    feature = options.feature or _USED_FOR[family][0]
    if family is PermissionId.AUTOMATION:
        asked = runtime.automation_ask.run(
            lambda: _ask_automation(
                service,
                permission_id,
                feature=feature,
                target=options.target,
                allow_outside_app=options.allow_outside_app,
            ),
            _AUTOMATION_ASK_WAIT_S,
        )
        if asked.ok and isinstance(asked.value, EnsureResult):
            return _ensure_payload(asked.value)
        if asked.finished:
            # The call raised (logged); nothing is known, so nothing is promised.
            return _ensure_payload(_unavailable_result(permission_id, options.target or ""))
        # The consent dialog is still open (or the check hung): macOS owns the next
        # step, the answer arrives as PermissionResolved. Never an HTTP hold.
        return _ensure_payload(
            _asking_result(permission_id, options.target or "", asked=not asked.busy)
        )
    result = service.ensure(
        permission_id,
        feature=feature,
        interactive=True,
        wait_s=0.0,
        allow_outside_app=options.allow_outside_app,
    )
    return _ensure_payload(result)


@router.post(
    "/{permission_id}/open-settings",
    summary="Open a system permission settings pane",
    openapi_extra={"x-jarvis-dangerous": True},
    response_model=None,
)
def open_permission_settings(
    permission_id: PermissionId,
    request: Request,
    dry_run: bool = Query(default=False),
) -> Any:
    """Open the matching System Settings pane. Not a prompt; rate limited (429)."""
    family = PANE_FAMILY[permission_id]
    label = _ROW_LABELS.get(family, permission_id.value)
    if settings_path_text(family) is None:
        return _operation_response(
            _operation(
                permission_id,
                "open_settings",
                ok=False,
                message=f"{label} has no System Settings pane; use the request flow instead.",
            )
        )
    if dry_run:
        return _operation(
            permission_id,
            "open_settings",
            ok=True,
            dry_run=True,
            message=f"Would open {label} in System Settings.",
        )
    runtime = _runtime(request)
    refusal = runtime.limiter.admit("open_settings", family)
    if refusal is not None:
        return _rate_limited(refusal, "open_settings", permission_id)
    service = _service(request)
    opened = service.open_settings(permission_id)
    return _operation_response(
        _operation(
            permission_id,
            "open_settings",
            ok=opened,
            performed=opened,
            message="System Settings opened." if opened else "System Settings could not be opened.",
            row=_single_row(service, runtime, permission_id),
        )
    )


@router.post(
    "/{permission_id}/reset",
    summary="Reset this app's own system permission record",
    openapi_extra={"x-jarvis-dangerous": True},
    response_model=None,
)
def reset_permission(
    permission_id: PermissionId,
    request: Request,
    dry_run: bool = Query(default=False),
) -> Any:
    """Drop the app's own macOS TCC row so the question can be asked again ("Ask again").

    Recovery for the stranded decision: macOS auto-denies an app that ever listened
    before it was asked, or whose signature changed (BUG-083), and then never asks
    again. Scoped strictly to this app's bundle id by the port, so only the
    installed app can do it; other apps stay untouched. Refused with 409 while the
    permission is live GRANTED (a reset would throw a working grant away) and
    whenever the live state cannot be read.
    """
    service = _service(request)
    runtime = _runtime(request)
    family = PANE_FAMILY[permission_id]
    label = _ROW_LABELS.get(family, permission_id.value)
    info = service.app_info()
    if info.platform != "darwin":
        return _operation_response(
            _operation(
                permission_id,
                "reset",
                ok=False,
                dry_run=dry_run,
                message="Resetting a privacy permission is only possible on macOS.",
            )
        )
    if family is PermissionId.AUTOMATION:
        live, _answered = _automation_state(service, runtime)
    else:
        live = service.check_deep(permission_id)
    if live in _READY:
        return _operation_response(
            _operation(
                permission_id,
                "reset",
                ok=False,
                dry_run=dry_run,
                message=f"{label} is allowed right now, so there is nothing to reset.",
            )
        )
    if live not in _RESETTABLE:
        return _operation_response(
            _operation(
                permission_id,
                "reset",
                ok=False,
                dry_run=dry_run,
                message=f"{label} cannot be reset from here (it reads {live.value}).",
            )
        )
    port = _permissions_module.get_system_permission_port()
    operation = port.reset(permission_id, dry_run=dry_run)
    if operation.ok and operation.performed:
        # "Ask again" must really ask: forget this process's request cooldown.
        service.note_reset(permission_id)
    return _operation_response(
        _operation(
            permission_id,
            "reset",
            ok=operation.ok,
            performed=operation.performed,
            dry_run=operation.dry_run,
            message=operation.message,
            row=None if dry_run else _single_row(service, runtime, permission_id),
        )
    )


__all__ = [
    "AppIdentityBlock",
    "EnsurePayload",
    "NeededEpisode",
    "OperationPayload",
    "PermissionRequestBody",
    "PermissionRow",
    "PermissionsSnapshot",
    "RateLimitedPayload",
    "router",
]
