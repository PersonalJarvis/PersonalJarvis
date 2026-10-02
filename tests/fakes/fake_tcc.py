"""FakeTCC: a stateful, framework-level simulator of macOS privacy (TCC).

Per AGENTS.md the project uses real fakes, never ``unittest.mock``. Nobody can
run macOS in CI-less sandboxes, and a static ``SimpleNamespace`` per test (the
style of ``tests/unit/platform/test_permissions.py``) cannot model what makes
macOS permissions hard: a per-service state machine, a dialog that the user
answers (or never answers), no second question after a decision, a Screen
Recording preflight that stays frozen until relaunch, and APIs that make macOS
show a dialog BY THEMSELVES. This module models exactly those, one level below
the product: it builds the ``module_loader`` dict (Foundation, AppKit,
AVFoundation, Quartz, ApplicationServices) plus every seam of
``SystemPermissionPort`` (``iohid_check``, ``screen_capture_live_check``,
``automation_probe``, the credential-store pair), so the REAL port and, later,
the REAL permission service run on top of it.

Every framework call lands in one ordered call log (``FakeTCC.calls``):

* ``probe``           - a read of a permission state; macOS never shows a dialog.
* ``request``         - an explicit prompt API (``requestAccessForMedia...``,
                        ``CGRequest*Access``, ``AXIsProcessTrustedWithOptions``
                        with the prompt option, ``AEDeterminePermission...`` with
                        ``askUserIfNeeded``).
* ``implicit_prompt`` - a call that makes macOS show a dialog BY ITSELF: opening
                        an input stream or grabbing the screen while the service
                        is ``not_determined``, creating an event tap before the
                        user was asked. Consumer tests assert there is none.

The ``outcome`` of a ``request`` says what macOS did with it. A request after a
decision is recorded as ``ignored_request`` (macOS does not re-ask), which makes
a re-prompt loop visible in the log.

Fidelity ledger. Each simulated behaviour carries one of two labels, here and
in the code. Nobody can run macOS in this sandbox, so nothing below is
"verified on a real Mac"; a behaviour marked ``documented`` is stated by Apple,
one marked ``unverified`` is community-observed or inferred from this repo's
own bug history (docs/BUGS.md) and must not be quoted as fact.

* Microphone: tri-state status, prompt only while ``not_determined``, the
  completion handler reports the current answer when already decided; missing
  ``NSMicrophoneUsageDescription`` aborts the process - documented. Denied
  microphone yields digital silence instead of an error - unverified.
* Screen Recording: ``CGPreflightScreenCaptureAccess`` /
  ``CGRequestScreenCaptureAccess`` exist (10.15+) - documented. The preflight
  value is frozen until relaunch (BUG-161) and a capture without the grant
  returns wallpaper-only windows instead of an error - unverified. Whether a
  grant given mid-process works for capture before relaunch is an open
  conflict, so both worlds are available (``screen_grant_needs_relaunch``).
* Accessibility: boolean trust, no ``not_determined`` - documented. Whether the
  prompt API shows again while still untrusted is an open conflict, so both
  worlds are available (``ax_reprompts_while_untrusted``) - unverified.
* Input Monitoring: ``CGPreflightListenEventAccess`` / ``CGRequest...`` /
  ``IOHIDCheckAccess`` (tri-state) - documented. "A listen tap created before
  the request registers the app as denied" (BUG-058 class) and "an Accessibility
  grant also lets a listen-only tap work" - unverified.
* Event posting is modelled as an alias of Accessibility (one switch, one
  state) - unverified.
* Automation: ``-600`` (target not running), ``-1743`` (denied), ``-1744``
  (consent needed), a blocking dialog with ``askUserIfNeeded`` - documented.
* No re-ask after a decision: documented for the microphone, unverified (but
  consistent with this repo's own observations) for the other services.
* A dialog the user dismisses without answering: unverified (a DIALOG-class
  service stays ``not_determined``; a PROMPT-ONCE service ends ``denied``).
* ``tccutil reset <Service> <bundle id>`` returns that bundle's row to
  ``not_determined`` and touches no other app's row - documented (man page).
* Which APIs raise an implicit prompt (input stream start, screen capture,
  listen-tap creation) - the first-access prompt is documented, the exact API
  set is unverified.

Companions ``FakeAudioInput`` / ``FakeEventTap`` / ``FakeScreenGrab`` consult the
same ``FakeTCC`` when "opened", so a consumer test can assert that nothing made
macOS prompt behind the feature's back.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import SimpleNamespace
from typing import Any, Final, Literal

from jarvis.core.branding import MACOS_APP_NAME, MACOS_BUNDLE_ID, MACOS_DMG_BUNDLE_ID
from jarvis.platform import PlatformName
from jarvis.platform.permissions import AUTOMATION_TARGETS, SystemPermissionPort

INSTALLED_BUNDLE_ID: Final = MACOS_BUNDLE_ID
DMG_BUNDLE_ID: Final = MACOS_DMG_BUNDLE_ID
TERMINAL_BUNDLE_ID: Final = "com.apple.Terminal"
INSTALLED_BUNDLE_PATH: Final = f"/Applications/{MACOS_APP_NAME}.app"

# Apple Event Manager OSStatus values (AE headers; stable ABI) - documented.
AE_NO_ERR: Final = 0
AE_PROC_NOT_FOUND: Final = -600
AE_EVENT_NOT_PERMITTED: Final = -1743
AE_EVENT_WOULD_REQUIRE_USER_CONSENT: Final = -1744

# IOHIDCheckAccess tri-state - documented (IOKit/hidsystem/IOHIDLib.h).
_IOHID_GRANTED: Final = 0
_IOHID_DENIED: Final = 1
_IOHID_UNKNOWN: Final = 2
_IOHID_REQUEST_POST_EVENT: Final = 0
_IOHID_REQUEST_LISTEN_EVENT: Final = 1

# AVAuthorizationStatus - documented.
_AV_STATUS: Final = {"not_determined": 0, "restricted": 1, "denied": 2, "granted": 3}

# ``kAXTrustedCheckOptionPrompt`` is the CFString "AXTrustedCheckOptionPrompt".
_AX_PROMPT_KEY: Final = "AXTrustedCheckOptionPrompt"

# Info.plist purpose strings for the services Jarvis may ask for. The Screen
# Recording key is shipped for parity; whether macOS reads it is unverified.
_USAGE_KEYS: Final = {
    "microphone": "NSMicrophoneUsageDescription",
    "automation": "NSAppleEventsUsageDescription",
    "screen_recording": "NSScreenCaptureUsageDescription",
}


class TccService(StrEnum):
    """The services this fake models; values equal ``PermissionId`` values."""

    MICROPHONE = "microphone"
    SCREEN_RECORDING = "screen_recording"
    ACCESSIBILITY = "accessibility"
    INPUT_MONITORING = "input_monitoring"
    POST_EVENT = "event_posting"
    AUTOMATION = "automation"


class TccState(StrEnum):
    """What the TCC database holds for one service (and, for automation, target).

    Accessibility has no ``NOT_DETERMINED`` (boolean trust): an untrusted process
    reads ``DENIED``. Documented.
    """

    NOT_DETERMINED = "not_determined"
    GRANTED = "granted"
    DENIED = "denied"
    RESTRICTED = "restricted"


class DialogPolicy(StrEnum):
    """What the simulated user does with the next dialog.

    ``ALLOW`` / ``DENY``: answer it (for a PROMPT-ONCE service ``ALLOW`` means the
    user opened System Settings and flipped the switch). ``DISMISS``: close it
    without an answer. ``NEVER_ANSWERED``: it stays on screen until the test
    calls :meth:`FakeTCC.answer`.
    """

    ALLOW = "allow"
    DENY = "deny"
    DISMISS = "dismiss"
    NEVER_ANSWERED = "never_answered"


class CallKind(StrEnum):
    PROBE = "probe"
    REQUEST = "request"
    IMPLICIT_PROMPT = "implicit_prompt"


class CallOutcome(StrEnum):
    """What macOS did with a logged call."""

    READ = "read"  # a probe: state returned, nothing shown
    DIALOG_SHOWN = "dialog_shown"
    IGNORED_REQUEST = "ignored_request"  # a decision is on file: no dialog
    DIALOG_ALREADY_OPEN = "dialog_already_open"
    RESTRICTED = "restricted"  # device policy: no dialog, never granted
    AUTO_DENIED = "auto_denied"  # listen tap created before the request
    TARGET_NOT_RUNNING = "target_not_running"  # Automation: -600
    PROCESS_ABORT = "process_abort"  # missing purpose string killed the process


# DIALOG class: the OS dialog has an answer button (microphone, automation).
# PROMPT-ONCE class: the dialog only offers "Open System Settings" (everything
# else).
_DIALOG_CLASS: Final = frozenset({TccService.MICROPHONE, TccService.AUTOMATION})

_TCCUTIL_SERVICES: Final = {
    "Microphone": TccService.MICROPHONE,
    "ScreenCapture": TccService.SCREEN_RECORDING,
    "Accessibility": TccService.ACCESSIBILITY,
    "ListenEvent": TccService.INPUT_MONITORING,
    "PostEvent": TccService.POST_EVENT,
    "AppleEvents": TccService.AUTOMATION,
}

_Key = tuple[TccService, str]


class TccProcessAbort(BaseException):  # noqa: N818 - models SIGABRT, never an Exception
    """Raised (only with ``abort_raises=True``) where macOS would kill the process.

    A BaseException on purpose: a real SIGABRT cannot be caught by the
    ``except Exception`` guards the port wraps around native calls.
    """


@dataclass(frozen=True, slots=True)
class TccCall:
    """One framework call, in the order it happened."""

    seq: int
    kind: CallKind
    service: TccService
    api: str
    outcome: CallOutcome
    thread: str
    target: str = ""
    caller: str = ""
    state_before: TccState | None = None
    state_after: TccState | None = None
    detail: str = ""  # the dialog policy that was applied, if a dialog ran
    grantee: str = ""  # the app macOS would name in the dialog

    def describe(self) -> str:
        target = f"[{self.target}]" if self.target else ""
        caller = f" caller={self.caller}" if self.caller else ""
        before = self.state_before.value if self.state_before else "-"
        after = self.state_after.value if self.state_after else "-"
        detail = f" ({self.detail})" if self.detail else ""
        return (
            f"#{self.seq} {self.kind.value} {self.service.value}{target} "
            f"{self.api} -> {self.outcome.value}{detail} "
            f"{before}->{after} thread={self.thread}{caller}"
        )


@dataclass(slots=True)
class _Pending:
    """A dialog that is on screen and not answered yet."""

    handlers: list[Callable[[bool], None]]


def _service(value: TccService | str) -> TccService:
    return TccService(str(value))


class FakeTCC:
    """A stateful macOS TCC simulator that plugs into ``SystemPermissionPort``.

    See the module docstring for the call log and the fidelity ledger.
    """

    def __init__(
        self,
        *,
        bundle_id: str | None = INSTALLED_BUNDLE_ID,
        bundle_path: str | None = INSTALLED_BUNDLE_PATH,
        responsible_app_if_unbundled: str = TERMINAL_BUNDLE_ID,
        granted: Iterable[TccService | str] = (),
        default_policy: DialogPolicy = DialogPolicy.ALLOW,
        usage_strings: Iterable[TccService | str] = (
            TccService.MICROPHONE,
            TccService.AUTOMATION,
            TccService.SCREEN_RECORDING,
        ),
        foreground: bool = True,
        headless: bool = False,
        preflight_frozen: Iterable[TccService | str] = (TccService.SCREEN_RECORDING,),
        ax_reprompts_while_untrusted: bool = True,
        screen_grant_needs_relaunch: bool = False,
        silent_tap_when_denied: bool = False,
        abort_raises: bool = False,
        installed_players: Iterable[str] = (),
        running_players: Iterable[str] = (),
        credential_backend: str = "platform",
        missing_frameworks: Iterable[str] = (),
    ) -> None:
        self.bundle_id = bundle_id
        self.bundle_path = bundle_path
        self.responsible_app_if_unbundled = responsible_app_if_unbundled
        self.foreground = foreground
        self.headless = headless
        self.credential_backend = credential_backend
        self.keychain_recover_calls = 0
        self.keychain_recover_result = True
        self.default_policy = default_policy
        self._usage_strings = frozenset(_service(item) for item in usage_strings)
        self._preflight_frozen = frozenset(_service(item) for item in preflight_frozen)
        self._ax_reprompts = ax_reprompts_while_untrusted
        self._screen_needs_relaunch = screen_grant_needs_relaunch
        self._silent_tap_when_denied = silent_tap_when_denied
        self._abort_raises = abort_raises
        self._missing_frameworks = set(missing_frameworks)
        self._lock = threading.RLock()
        self._calls: list[TccCall] = []
        self.loaded_modules: list[str] = []
        self.tccutil_calls: list[list[str]] = []
        self.launches: list[tuple[str, bool | None, bool | None]] = []
        self._states: dict[_Key, TccState] = {}
        self._pending: dict[_Key, _Pending] = {}
        self._asked: set[_Key] = set()
        self._policy_default: dict[_Key, DialogPolicy] = {}
        self._policy_queue: dict[_Key, deque[DialogPolicy]] = {}
        self._installed = set(installed_players)
        self._running = set(running_players)
        self._launch_view: dict[_Key, TccState] = {}
        self._launch_count = 0
        # Taps die with the process: a tap remembers the generation it was born in.
        self._generation = 0
        self.workspace_opened_urls: list[str] = []

        for service in TccService:
            if service is TccService.POST_EVENT:
                continue  # alias of Accessibility, no state of its own
            initial = (
                TccState.DENIED if service is TccService.ACCESSIBILITY else TccState.NOT_DETERMINED
            )
            self._states[(service, "")] = initial
        for item in granted:
            service = _service(item)
            if service is TccService.AUTOMATION:
                for _name, target in AUTOMATION_TARGETS:
                    self._states[(TccService.AUTOMATION, target)] = TccState.GRANTED
            else:
                self._states[self._key(service)] = TccState.GRANTED
        self.modules: dict[str, Any] = self._build_modules()
        self._snapshot_launch()

    # ------------------------------------------------------------------ identity

    @property
    def launched_as_bundle(self) -> bool:
        return bool(self.bundle_path and ".app/" in f"{self.bundle_path}/")

    @property
    def grantee(self) -> str:
        """The app macOS records grants against and names in the dialog.

        An installed bundle is its own client. A process started from a terminal
        is attributed to the terminal - community-observed, Apple documents no
        algorithm for child processes - unverified.
        """
        if self.launched_as_bundle and self.bundle_id:
            return self.bundle_id
        return self.responsible_app_if_unbundled

    @property
    def launch_count(self) -> int:
        return self._launch_count

    # ------------------------------------------------------------------ call log

    @property
    def calls(self) -> tuple[TccCall, ...]:
        with self._lock:
            return tuple(self._calls)

    def mark(self) -> int:
        """A position in the log; pair with :meth:`calls_since`."""
        with self._lock:
            return len(self._calls)

    def calls_since(self, mark: int) -> tuple[TccCall, ...]:
        with self._lock:
            return tuple(self._calls[mark:])

    def clear_log(self) -> None:
        with self._lock:
            self._calls.clear()
            self.loaded_modules.clear()

    def calls_of(
        self,
        kind: CallKind | str | None = None,
        service: TccService | str | None = None,
        *,
        target: str | None = None,
        since: int = 0,
    ) -> list[TccCall]:
        wanted_kind = CallKind(str(kind)) if kind is not None else None
        wanted_service = _service(service) if service is not None else None
        return [
            call
            for call in self.calls_since(since)
            if (wanted_kind is None or call.kind is wanted_kind)
            and (wanted_service is None or call.service is wanted_service)
            and (target is None or call.target == target)
        ]

    def kinds(self) -> list[str]:
        return [call.kind.value for call in self.calls]

    def probes(self, service: TccService | str | None = None) -> list[TccCall]:
        return self.calls_of(CallKind.PROBE, service)

    def requests(self, service: TccService | str | None = None) -> list[TccCall]:
        """Every explicit request, ignored ones included."""
        return self.calls_of(CallKind.REQUEST, service)

    def implicit_prompts(self, service: TccService | str | None = None) -> list[TccCall]:
        return self.calls_of(CallKind.IMPLICIT_PROMPT, service)

    def ignored_requests(self, service: TccService | str | None = None) -> list[TccCall]:
        return [
            call
            for call in self.calls_of(CallKind.REQUEST, service)
            if call.outcome is CallOutcome.IGNORED_REQUEST
        ]

    def dialogs_shown(self, service: TccService | str | None = None) -> list[TccCall]:
        return [
            call
            for call in self.calls
            if call.outcome is CallOutcome.DIALOG_SHOWN
            and (service is None or call.service is _service(service))
        ]

    def aborts(self) -> list[TccCall]:
        return [call for call in self.calls if call.outcome is CallOutcome.PROCESS_ABORT]

    def format_log(self) -> str:
        return "\n".join(call.describe() for call in self.calls) or "(no framework call)"

    def assert_silent(self) -> None:
        """No framework was loaded and no call happened (the non-darwin contract)."""
        with self._lock:
            assert not self._calls and not self.loaded_modules, (
                f"expected a silent TCC; loaded={self.loaded_modules}\n{self.format_log()}"
            )

    def assert_no_prompts(self, *, since: int = 0) -> None:
        """No explicit request and no implicit prompt (the boot contract)."""
        offending = [
            call
            for call in self.calls_since(since)
            if call.kind in (CallKind.REQUEST, CallKind.IMPLICIT_PROMPT)
        ]
        assert not offending, "unexpected prompts:\n" + "\n".join(c.describe() for c in offending)

    def _append(
        self,
        kind: CallKind,
        service: TccService,
        api: str,
        outcome: CallOutcome,
        *,
        target: str = "",
        caller: str = "",
        before: TccState | None = None,
        after: TccState | None = None,
        detail: str = "",
    ) -> TccCall:
        with self._lock:
            call = TccCall(
                seq=len(self._calls),
                kind=kind,
                service=service,
                api=api,
                outcome=outcome,
                thread=threading.current_thread().name,
                target=target,
                caller=caller,
                state_before=before,
                state_after=after,
                detail=detail,
                grantee=self.grantee,
            )
            self._calls.append(call)
            return call

    def _probe(
        self,
        service: TccService,
        api: str,
        *,
        target: str = "",
        outcome: CallOutcome = CallOutcome.READ,
        caller: str = "",
    ) -> None:
        state = self._read(service, target)
        self._append(
            CallKind.PROBE,
            service,
            api,
            outcome,
            target=target,
            caller=caller,
            before=state,
            after=state,
        )

    # ------------------------------------------------------------ state machine

    @staticmethod
    def _key(service: TccService, target: str = "") -> _Key:
        # Event posting shares the Accessibility switch (unverified alias).
        if service is TccService.POST_EVENT:
            return (TccService.ACCESSIBILITY, "")
        return (service, target if service is TccService.AUTOMATION else "")

    def _read(self, service: TccService, target: str = "") -> TccState:
        with self._lock:
            return self._states.setdefault(self._key(service, target), TccState.NOT_DETERMINED)

    def state(self, service: TccService | str, target: str = "") -> TccState:
        """The TCC row, for assertions. Not a framework call, never logged."""
        return self._read(_service(service), target)

    def _set(self, service: TccService, state: TccState, target: str = "") -> None:
        if service is TccService.AUTOMATION and not target:
            raise ValueError("automation is per target: pass the player's bundle id")
        handlers: list[Callable[[bool], None]] = []
        key = self._key(service, target)
        with self._lock:
            self._states[key] = state
            if state is TccState.DENIED:
                self._asked.add(key)
            pending = self._pending.pop(key, None)
            if pending is not None:
                handlers = pending.handlers
                self._asked.add(key)
        for handler in handlers:
            handler(state is TccState.GRANTED)

    def grant(self, service: TccService | str, target: str = "") -> None:
        """The user flips the switch on in System Settings (live in TCC).

        Does not touch a frozen preflight: that needs :meth:`relaunch`.
        """
        self._set(_service(service), TccState.GRANTED, target)

    def deny(self, service: TccService | str, target: str = "") -> None:
        """The user answered "Don't Allow" or flipped the switch off."""
        self._set(_service(service), TccState.DENIED, target)

    def restrict(self, service: TccService | str) -> None:
        """Device policy (MDM) forbids the service; no dialog can ever grant it."""
        self._set(_service(service), TccState.RESTRICTED)

    def reset(self, service: TccService | str, target: str | None = None) -> None:
        """``tccutil reset``: back to the state before the first question.

        Accessibility has no ``not_determined`` and goes back to untrusted.
        """
        resolved = _service(service)
        with self._lock:
            if resolved is TccService.AUTOMATION and target is None:
                keys = [key for key in self._states if key[0] is TccService.AUTOMATION]
                keys.extend((TccService.AUTOMATION, t) for _name, t in AUTOMATION_TARGETS)
            else:
                keys = [self._key(resolved, target or "")]
            for key in set(keys):
                self._states[key] = (
                    TccState.DENIED
                    if key[0] is TccService.ACCESSIBILITY
                    else TccState.NOT_DETERMINED
                )
                self._pending.pop(key, None)
                self._asked.discard(key)

    def relaunch(self) -> None:
        """Quit and reopen the app: frozen preflights refresh, taps and dialogs die."""
        with self._lock:
            self._launch_count += 1
            self._generation += 1
            self._pending.clear()
            self._snapshot_launch()

    def _snapshot_launch(self) -> None:
        with self._lock:
            self._launch_view = dict(self._states)
            self._launch_count = max(self._launch_count, 1)

    # ------------------------------------------------------------- dialog policy

    def set_policy(
        self, service: TccService | str, policy: DialogPolicy | str, target: str = ""
    ) -> None:
        """Sticky answer for every later dialog of this service (or target)."""
        self._policy_default[self._key(_service(service), target)] = DialogPolicy(str(policy))

    def script(
        self, service: TccService | str, *policies: DialogPolicy | str, target: str = ""
    ) -> None:
        """Queue one answer per upcoming dialog; the sticky policy applies after."""
        queue = self._policy_queue.setdefault(self._key(_service(service), target), deque())
        queue.extend(DialogPolicy(str(policy)) for policy in policies)

    def _next_policy(self, key: _Key) -> DialogPolicy:
        queue = self._policy_queue.get(key)
        if queue:
            return queue.popleft()
        if key in self._policy_default:
            return self._policy_default[key]
        service_wide = (key[0], "")
        return self._policy_default.get(service_wide, self.default_policy)

    def dialog_open(self, service: TccService | str, target: str = "") -> bool:
        with self._lock:
            return self._key(_service(service), target) in self._pending

    def answer(
        self,
        service: TccService | str,
        policy: DialogPolicy | str = DialogPolicy.ALLOW,
        *,
        target: str = "",
    ) -> None:
        """The user finally answers a ``NEVER_ANSWERED`` dialog."""
        resolved = _service(service)
        key = self._key(resolved, target)
        with self._lock:
            pending = self._pending.pop(key, None)
            if pending is None:
                raise LookupError(f"no open dialog for {resolved.value} {target}".strip())
            granted = self._apply_policy(key, DialogPolicy(str(policy)))
        if granted is not None:
            for handler in pending.handlers:
                handler(granted)

    def _apply_policy(self, key: _Key, policy: DialogPolicy) -> bool | None:
        """Apply the user's answer; ``None`` means no answer was given."""
        service = key[0]
        self._asked.add(key)
        if policy is DialogPolicy.ALLOW:
            self._states[key] = TccState.GRANTED
            return True
        if policy is DialogPolicy.DENY:
            self._states[key] = TccState.DENIED
            return False
        # Dismissed (unverified): a DIALOG-class service keeps asking later; a
        # PROMPT-ONCE service has already registered the app with its switch off.
        if service not in _DIALOG_CLASS:
            self._states[key] = TccState.DENIED
        return None

    def _bundled_without_purpose_string(self, service: TccService) -> bool:
        # Documented for the microphone: a missing NSMicrophoneUsageDescription
        # aborts the process (the BUG-058 SIGABRT class). A terminal-started
        # process is covered by the terminal's own Info.plist.
        return (
            service is TccService.MICROPHONE
            and self.launched_as_bundle
            and service not in self._usage_strings
        )

    def _ask(
        self,
        service: TccService,
        *,
        api: str,
        kind: CallKind,
        target: str = "",
        caller: str = "",
        on_answer: Callable[[bool], None] | None = None,
    ) -> CallOutcome:
        """Run one request or implicit prompt through the state machine."""
        key = self._key(service, target)
        answer: bool | None = None
        abort = False
        with self._lock:
            before = self._states.setdefault(key, TccState.NOT_DETERMINED)
            detail = ""
            if self._bundled_without_purpose_string(service):
                outcome = CallOutcome.PROCESS_ABORT
                abort = True
            elif before is TccState.RESTRICTED:
                outcome = CallOutcome.RESTRICTED
                answer = False
            elif key in self._pending:
                outcome = CallOutcome.DIALOG_ALREADY_OPEN
                if on_answer is not None:
                    self._pending[key].handlers.append(on_answer)
                    on_answer = None
            elif self._decision_on_file(key, before):
                # macOS does not re-ask after a decision. For the microphone the
                # completion handler simply reports the current answer.
                outcome = CallOutcome.IGNORED_REQUEST
                answer = before is TccState.GRANTED
            else:
                policy = self._next_policy(key)
                detail = policy.value
                outcome = CallOutcome.DIALOG_SHOWN
                if policy is DialogPolicy.NEVER_ANSWERED:
                    handlers = [on_answer] if on_answer is not None else []
                    self._pending[key] = _Pending(handlers=handlers)
                    on_answer = None
                else:
                    answer = self._apply_policy(key, policy)
            after = self._states[key]
            self._append(
                kind,
                service,
                api,
                outcome,
                target=target,
                caller=caller,
                before=before,
                after=after,
                detail=detail,
            )
        if abort and self._abort_raises:
            raise TccProcessAbort(f"missing purpose string for {service.value}")
        if on_answer is not None and answer is not None:
            on_answer(answer)
        return outcome

    def _decision_on_file(self, key: _Key, state: TccState) -> bool:
        if state is TccState.GRANTED:
            return True
        if key[0] is TccService.ACCESSIBILITY:
            # No not_determined: DENIED means "untrusted". The first prompt always
            # shows; a later one shows again only in the re-prompt world (unverified).
            return state is TccState.DENIED and key in self._asked and not self._ax_reprompts
        return state is TccState.DENIED

    # ------------------------------------------------- effective grants (no log)

    def _preflight_value(self, service: TccService) -> bool:
        key = self._key(service)
        if service in self._preflight_frozen:
            return self._launch_view.get(key, TccState.NOT_DETERMINED) is TccState.GRANTED
        return self._read(service) is TccState.GRANTED

    def screen_capture_works(self) -> bool:
        """Whether a capture right now sees other apps' windows. Not logged.

        Without the grant a capture returns the wallpaper and no error
        (unverified). Whether a mid-process grant already works before a
        relaunch is an open conflict: ``screen_grant_needs_relaunch`` selects
        the pessimistic world.
        """
        state = self._read(TccService.SCREEN_RECORDING)
        if state is not TccState.GRANTED:
            return False
        if self._screen_needs_relaunch:
            launch = self._launch_view.get((TccService.SCREEN_RECORDING, ""))
            return launch is TccState.GRANTED
        return True

    def audio_input_is_audible(self) -> bool:
        """Whether an open input stream carries real audio, else digital silence.

        A denied microphone delivering zeros instead of an error is unverified.
        """
        return self._read(TccService.MICROPHONE) is TccState.GRANTED

    # ------------------------------------------------------ companion consults

    def consult_audio_input_start(self, caller: str = "") -> bool:
        """An input stream starts. ``True`` when it will carry real audio.

        While ``not_determined`` the start itself makes macOS ask (documented:
        first capture prompts) - recorded as an implicit prompt.
        """
        if self._read(TccService.MICROPHONE) is TccState.NOT_DETERMINED:
            self._ask(
                TccService.MICROPHONE,
                api="input stream start",
                kind=CallKind.IMPLICIT_PROMPT,
                caller=caller,
            )
        return self.audio_input_is_audible()

    def consult_screen_grab(self, caller: str = "") -> bool:
        """A capture starts. ``True`` when it sees other apps' windows."""
        if self._read(TccService.SCREEN_RECORDING) is TccState.NOT_DETERMINED:
            self._ask(
                TccService.SCREEN_RECORDING,
                api="screen capture",
                kind=CallKind.IMPLICIT_PROMPT,
                caller=caller,
            )
        return self.screen_capture_works()

    def consult_listen_tap(
        self, *, listen_only: bool = True, caller: str = ""
    ) -> Literal["live", "silent", "failed"]:
        """``CGEventTapCreate``.

        Unverified rules: a listen-only tap works with Input Monitoring OR
        Accessibility; created while Input Monitoring is ``not_determined`` (and
        no Accessibility) it registers the app as DENIED with no dialog (BUG-058
        class); denied, creation fails, or (``silent_tap_when_denied``) "succeeds"
        and receives nothing. An active tap needs Accessibility.
        """
        with self._lock:
            ax = self._read(TccService.ACCESSIBILITY)
            monitoring = self._read(TccService.INPUT_MONITORING)
            if ax is TccState.GRANTED:
                return "live"
            if listen_only and monitoring is TccState.GRANTED:
                return "live"
            if (
                listen_only
                and monitoring is TccState.NOT_DETERMINED
                and (TccService.INPUT_MONITORING, "") not in self._pending
            ):
                self._states[(TccService.INPUT_MONITORING, "")] = TccState.DENIED
                self._asked.add((TccService.INPUT_MONITORING, ""))
                self._append(
                    CallKind.IMPLICIT_PROMPT,
                    TccService.INPUT_MONITORING,
                    "CGEventTapCreate",
                    CallOutcome.AUTO_DENIED,
                    caller=caller,
                    before=monitoring,
                    after=TccState.DENIED,
                )
                return "failed"
            return "silent" if self._silent_tap_when_denied else "failed"

    @property
    def generation(self) -> int:
        return self._generation

    # ------------------------------------------------------------ framework API

    def _require_audio(self, media_type: Any) -> None:
        if media_type != "soun":
            raise NotImplementedError("FakeTCC models the microphone only (AVMediaTypeAudio)")

    def _av_status(self, media_type: Any) -> int:
        self._require_audio(media_type)
        self._probe(TccService.MICROPHONE, "authorizationStatusForMediaType:")
        return _AV_STATUS[self._read(TccService.MICROPHONE).value]

    def _av_request(self, media_type: Any, handler: Callable[[bool], None]) -> None:
        self._require_audio(media_type)
        self._ask(
            TccService.MICROPHONE,
            api="requestAccessForMediaType:completionHandler:",
            kind=CallKind.REQUEST,
            on_answer=handler,
        )

    def _cg_preflight(self, service: TccService, api: str) -> bool:
        self._probe(service, api)
        return self._preflight_value(service)

    def _cg_request(self, service: TccService, api: str) -> bool:
        # The requesters report the state at the moment of the call; the user
        # answers later. Documented.
        before = self._preflight_value(service)
        self._ask(service, api=api, kind=CallKind.REQUEST)
        return before

    def _ax_trusted(self) -> bool:
        self._probe(TccService.ACCESSIBILITY, "AXIsProcessTrusted")
        return self._read(TccService.ACCESSIBILITY) is TccState.GRANTED

    def _ax_trusted_with_options(self, options: Mapping[str, Any] | None) -> bool:
        wants_prompt = bool(options and options.get(_AX_PROMPT_KEY))
        if not wants_prompt:
            self._probe(TccService.ACCESSIBILITY, "AXIsProcessTrustedWithOptions")
            return self._read(TccService.ACCESSIBILITY) is TccState.GRANTED
        before = self._read(TccService.ACCESSIBILITY) is TccState.GRANTED
        self._ask(
            TccService.ACCESSIBILITY,
            api="AXIsProcessTrustedWithOptions(prompt)",
            kind=CallKind.REQUEST,
        )
        return before

    def iohid_check(self, request_type: int) -> int | None:
        """Seam ``iohid_check``: the live tri-state of ``IOHIDCheckAccess``."""
        service = (
            TccService.INPUT_MONITORING
            if request_type == _IOHID_REQUEST_LISTEN_EVENT
            else TccService.POST_EVENT
        )
        self._probe(service, "IOHIDCheckAccess")
        state = self._read(service)
        if state is TccState.GRANTED:
            return _IOHID_GRANTED
        if state is TccState.NOT_DETERMINED:
            return _IOHID_UNKNOWN
        if service is TccService.POST_EVENT and self._key(service) not in self._asked:
            # Accessibility has no not_determined, but the HID tri-state still
            # reads "unknown" until the user was asked once - unverified.
            return _IOHID_UNKNOWN
        return _IOHID_DENIED

    def iohid_request(self, request_type: int) -> bool:
        """``IOHIDRequestAccess``; no port seam uses it yet."""
        service = (
            TccService.INPUT_MONITORING
            if request_type == _IOHID_REQUEST_LISTEN_EVENT
            else TccService.POST_EVENT
        )
        before = self._read(service) is TccState.GRANTED
        self._ask(service, api="IOHIDRequestAccess", kind=CallKind.REQUEST)
        return before

    def screen_capture_live_check(self) -> bool | None:
        """Seam ``screen_capture_live_check``: another app's window title is readable."""
        self._probe(TccService.SCREEN_RECORDING, "window title oracle")
        return True if self.screen_capture_works() else None

    def automation_probe(self, bundle_id: str, ask: bool) -> int | None:
        """Seam ``automation_probe``: ``AEDeterminePermissionToAutomateTarget``.

        A real ``ask`` blocks until the user answers. A ``NEVER_ANSWERED`` dialog
        would hang a test, so the call returns ``-1744`` at once (documented
        deviation) and the dialog stays open for :meth:`answer`.
        """
        service = TccService.AUTOMATION
        if bundle_id not in self._running:
            self._append(
                CallKind.REQUEST if ask else CallKind.PROBE,
                service,
                "AEDeterminePermissionToAutomateTarget",
                CallOutcome.TARGET_NOT_RUNNING,
                target=bundle_id,
            )
            return AE_PROC_NOT_FOUND
        if ask:
            self._ask(
                service,
                api="AEDeterminePermissionToAutomateTarget(ask)",
                kind=CallKind.REQUEST,
                target=bundle_id,
            )
        else:
            self._probe(service, "AEDeterminePermissionToAutomateTarget", target=bundle_id)
        state = self._read(service, bundle_id)
        if state is TccState.GRANTED:
            return AE_NO_ERR
        if state in (TccState.DENIED, TccState.RESTRICTED):
            return AE_EVENT_NOT_PERMITTED
        return AE_EVENT_WOULD_REQUIRE_USER_CONSENT

    def automation_consent_runner(self, script: str) -> SimpleNamespace:
        """Seam ``automation_consent_runner``: the killable ``osascript`` child.

        Stands in for ``subprocess.run(["osascript", "-e", script])`` of the
        port's Automation request. The guarded script only sends its Apple Event
        when the player RUNS, so a player that is not running is left alone and
        nothing is asked (the AppleScript answers ``-``). The runner is
        synchronous like the real child; a ``NEVER_ANSWERED`` dialog stays open
        for :meth:`answer` exactly as in :meth:`automation_probe`.
        """
        for _name, bundle_id in AUTOMATION_TARGETS:
            if f'"{bundle_id}"' not in script:
                continue
            running = bundle_id in self._running
            if running:
                self.automation_probe(bundle_id, True)
            return SimpleNamespace(returncode=0, stdout="+" if running else "-", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="unknown target")

    # --------------------------------------------------------- credential store

    def credential_store_backend(self) -> str:
        return self.credential_backend

    def credential_store_recover(self) -> bool:
        self.keychain_recover_calls += 1
        if self.keychain_recover_result:
            self.credential_backend = "platform"
        return self.keychain_recover_result

    # ----------------------------------------------------------------- tccutil

    def run_tccutil(self, argv: Iterable[str], **_kwargs: Any) -> SimpleNamespace:
        """A ``subprocess.run`` stand-in for ``/usr/bin/tccutil reset <Service> <id>``.

        Only the row of the NAMED bundle id is reset (documented); naming another
        app's id leaves this app's rows alone.
        """
        command = [str(part) for part in argv]
        self.tccutil_calls.append(command)
        if len(command) != 4 or command[1] != "reset" or command[2] not in _TCCUTIL_SERVICES:
            return SimpleNamespace(returncode=1, stdout="", stderr="tccutil: invalid arguments")
        if command[3] == self.grantee:
            self.reset(_TCCUTIL_SERVICES[command[2]])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    # ------------------------------------------------------------------ players

    def launch_player(self, bundle_id: str) -> None:
        self._installed.add(bundle_id)
        self._running.add(bundle_id)

    def quit_player(self, bundle_id: str) -> None:
        self._running.discard(bundle_id)

    def player_running(self, bundle_id: str) -> bool:
        return bundle_id in self._running

    # ------------------------------------------------------ module loader / port

    def module_loader(self, name: str) -> Any:
        """Seam ``module_loader``: a framework the app imports lazily."""
        with self._lock:
            self.loaded_modules.append(name)
        if name in self._missing_frameworks or name not in self.modules:
            raise ModuleNotFoundError(name)
        return self.modules[name]

    def drop_framework(self, name: str) -> None:
        """Make a framework unimportable, as on a build that did not bundle it."""
        self._missing_frameworks.add(name)

    def port(
        self, platform_name: PlatformName = "darwin", **overrides: Any
    ) -> SystemPermissionPort:
        """A REAL ``SystemPermissionPort`` wired to every seam of this fake.

        ``overrides`` replace single seams. A seam the port gains later and this
        method does not pass keeps its production default, which on a real Mac
        reads the machine's real state: extend this method when one is added.
        """
        seams: dict[str, Any] = {
            "platform_name": platform_name,
            "module_loader": self.module_loader,
            "iohid_check": self.iohid_check,
            "screen_capture_live_check": self.screen_capture_live_check,
            "credential_store_backend": self.credential_store_backend,
            "credential_store_recover": self.credential_store_recover,
            "automation_probe": self.automation_probe,
            "automation_consent_runner": self.automation_consent_runner,
            "iohid_request": self.iohid_request,
        }
        seams.update(overrides)
        return SystemPermissionPort(**seams)

    def _build_modules(self) -> dict[str, Any]:
        tcc = self
        process_id = 4242

        class _Bundle:
            def bundleIdentifier(self) -> str | None:  # noqa: N802 - Cocoa selector
                return tcc.bundle_id

            def bundlePath(self) -> str | None:  # noqa: N802
                return tcc.bundle_path

            def objectForInfoDictionaryKey_(self, key: str) -> str | None:  # noqa: N802
                for service, plist_key in _USAGE_KEYS.items():
                    if plist_key == key and TccService(service) in tcc._usage_strings:
                        return f"Jarvis needs {service} access."
                return None

            def infoDictionary(self) -> dict[str, str]:  # noqa: N802
                return {
                    plist_key: f"Jarvis needs {service} access."
                    for service, plist_key in _USAGE_KEYS.items()
                    if TccService(service) in tcc._usage_strings
                }

        class _RunningApp:
            def __init__(self, *, active: bool, pid: int) -> None:
                self._active = active
                self._pid = pid

            def isActive(self) -> bool:  # noqa: N802
                return self._active

            def processIdentifier(self) -> int:  # noqa: N802
                return self._pid

        class _PlayerApp:
            def __init__(self, bundle_id: str) -> None:
                self.bundle_id = bundle_id

            def terminate(self) -> None:
                tcc.quit_player(self.bundle_id)

            def isTerminated(self) -> bool:  # noqa: N802
                return not tcc.player_running(self.bundle_id)

        class _OpenConfiguration:
            def __init__(self) -> None:
                self.activates: bool | None = None
                self.hides: bool | None = None

            def setActivates_(self, value: bool) -> None:  # noqa: N802
                self.activates = value

            def setHides_(self, value: bool) -> None:  # noqa: N802
                self.hides = value

        _app_url = "file:///Applications/{}.app"

        class _Workspace:
            def frontmostApplication(self) -> _RunningApp | None:  # noqa: N802
                if tcc.headless:
                    return None
                if tcc.foreground:
                    return _RunningApp(active=True, pid=process_id)
                return _RunningApp(active=False, pid=process_id + 1)

            def openURL_(self, url: str) -> bool:  # noqa: N802
                tcc.workspace_opened_urls.append(url)
                return True

            def URLForApplicationWithBundleIdentifier_(  # noqa: N802
                self, bundle_id: str
            ) -> str | None:
                return _app_url.format(bundle_id) if bundle_id in tcc._installed else None

            def openApplicationAtURL_configuration_completionHandler_(  # noqa: N802
                self, url: str, configuration: _OpenConfiguration, handler: Callable[..., None]
            ) -> None:
                bundle_id = url.removeprefix("file:///Applications/").removesuffix(".app")
                tcc.launches.append((bundle_id, configuration.activates, configuration.hides))
                if bundle_id in tcc._installed:
                    tcc._running.add(bundle_id)
                handler(_PlayerApp(bundle_id), None)

        workspace = _Workspace()

        def _running_with_bundle_id(bundle_id: str) -> list[_PlayerApp]:
            return [_PlayerApp(bundle_id)] if bundle_id in tcc._running else []

        return {
            "Foundation": SimpleNamespace(
                NSBundle=SimpleNamespace(mainBundle=_Bundle),
                NSURL=SimpleNamespace(URLWithString_=lambda value: value),
            ),
            "AppKit": SimpleNamespace(
                NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: workspace),
                NSRunningApplication=SimpleNamespace(
                    currentApplication=lambda: _RunningApp(active=tcc.foreground, pid=process_id),
                    runningApplicationsWithBundleIdentifier_=_running_with_bundle_id,
                ),
                NSWorkspaceOpenConfiguration=SimpleNamespace(configuration=_OpenConfiguration),
            ),
            "AVFoundation": SimpleNamespace(
                AVCaptureDevice=SimpleNamespace(
                    authorizationStatusForMediaType_=self._av_status,
                    requestAccessForMediaType_completionHandler_=self._av_request,
                ),
                AVMediaTypeAudio="soun",
                AVAuthorizationStatusNotDetermined=0,
                AVAuthorizationStatusRestricted=1,
                AVAuthorizationStatusDenied=2,
                AVAuthorizationStatusAuthorized=3,
            ),
            "Quartz": SimpleNamespace(
                CGPreflightScreenCaptureAccess=lambda: self._cg_preflight(
                    TccService.SCREEN_RECORDING, "CGPreflightScreenCaptureAccess"
                ),
                CGRequestScreenCaptureAccess=lambda: self._cg_request(
                    TccService.SCREEN_RECORDING, "CGRequestScreenCaptureAccess"
                ),
                CGPreflightListenEventAccess=lambda: self._cg_preflight(
                    TccService.INPUT_MONITORING, "CGPreflightListenEventAccess"
                ),
                CGRequestListenEventAccess=lambda: self._cg_request(
                    TccService.INPUT_MONITORING, "CGRequestListenEventAccess"
                ),
                CGPreflightPostEventAccess=lambda: self._cg_preflight(
                    TccService.POST_EVENT, "CGPreflightPostEventAccess"
                ),
                CGRequestPostEventAccess=lambda: self._cg_request(
                    TccService.POST_EVENT, "CGRequestPostEventAccess"
                ),
            ),
            "ApplicationServices": SimpleNamespace(
                AXIsProcessTrusted=self._ax_trusted,
                AXIsProcessTrustedWithOptions=self._ax_trusted_with_options,
                kAXTrustedCheckOptionPrompt=_AX_PROMPT_KEY,
            ),
        }

    @classmethod
    def all_granted(cls, **kwargs: Any) -> FakeTCC:
        """The upgrader whose every grant is already in place at launch."""
        return cls(granted=tuple(TccService), **kwargs)


# --------------------------------------------------------------------------
# Companions: a consumer "opens" something and the same FakeTCC decides what
# macOS would have done.
# --------------------------------------------------------------------------

_AUDIBLE_SAMPLE = b"\x00\x01"  # int16 256: not digital silence
_SILENT_SAMPLE = b"\x00\x00"


class FakeAudioStream:
    """A ``sounddevice.InputStream`` stand-in; see :class:`FakeAudioInput`."""

    def __init__(self, owner: FakeAudioInput, kwargs: dict[str, Any]) -> None:
        self.owner = owner
        self.kwargs = kwargs
        self.callback: Callable[..., None] | None = kwargs.get("callback")
        self.channels = int(kwargs.get("channels") or 1)
        self.blocksize = int(kwargs.get("blocksize") or 512)
        self.started = False
        self.stopped = False
        self.closed = False
        self.silent_blocks = 0
        self.audible_blocks = 0

    @property
    def active(self) -> bool:
        return self.started and not self.stopped and not self.closed

    def start(self) -> None:
        self.started = True
        self.owner.starts.append(self)
        if self.owner.tcc is not None:
            self.owner.tcc.consult_audio_input_start(self.owner.caller)

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True

    def pump(self, blocks: int = 1) -> int:
        """Deliver ``blocks`` callback blocks; zeros while macOS withholds the mic."""
        tcc = self.owner.tcc
        audible = tcc is None or tcc.audio_input_is_audible()
        sample = _AUDIBLE_SAMPLE if audible else _SILENT_SAMPLE
        for _ in range(blocks):
            if audible:
                self.audible_blocks += 1
            else:
                self.silent_blocks += 1
            if self.callback is not None:
                self.callback(sample * self.blocksize * self.channels, self.blocksize, None, None)
        return blocks


class FakeAudioInput:
    """Callable ``sounddevice.InputStream`` factory that consults a ``FakeTCC``.

    ``tcc=None`` models an OS without TCC (Windows, Linux): nothing is consulted.
    Install with ``monkeypatch.setattr(capture, "sd", SimpleNamespace(InputStream=fake))``.
    ``streams`` lists every stream created, ``starts`` every one that was started:
    a consumer test asserts "no device was opened" with ``fake.starts == []``.
    """

    def __init__(self, tcc: FakeTCC | None = None, *, caller: str = "audio") -> None:
        self.tcc = tcc
        self.caller = caller
        self.streams: list[FakeAudioStream] = []
        self.starts: list[FakeAudioStream] = []

    def __call__(self, *_args: Any, **kwargs: Any) -> FakeAudioStream:
        stream = FakeAudioStream(self, kwargs)
        self.streams.append(stream)
        return stream


class FakeEventTap:
    """A ``CGEventTapCreate`` stand-in that consults a ``FakeTCC``."""

    def __init__(self, tcc: FakeTCC, *, caller: str = "event_tap") -> None:
        self.tcc = tcc
        self.caller = caller
        self.attempts: list[str] = []
        self.events_received = 0
        self._result: str = "failed"
        self._generation = -1
        self._callback: Callable[[int], None] | None = None

    def create(
        self, callback: Callable[[int], None] | None = None, *, listen_only: bool = True
    ) -> bool:
        """Create the tap. ``True`` when macOS handed back a tap object."""
        result = self.tcc.consult_listen_tap(listen_only=listen_only, caller=self.caller)
        self.attempts.append(result)
        self._result = result
        self._generation = self.tcc.generation
        self._callback = callback
        return result in ("live", "silent")

    @property
    def live(self) -> bool:
        """A tap object exists in this process (it may still receive nothing)."""
        return self._result in ("live", "silent") and self._generation == self.tcc.generation

    @property
    def receives_events(self) -> bool:
        return self._result == "live" and self._generation == self.tcc.generation

    def deliver_key(self, keycode: int = 0) -> bool:
        """A key goes down; ``True`` when the tap callback ran."""
        if not self.receives_events:
            return False
        self.events_received += 1
        if self._callback is not None:
            self._callback(keycode)
        return True


@dataclass(frozen=True, slots=True)
class FakeFrame:
    """One captured frame: other apps' windows, or the wallpaper only."""

    wallpaper_only: bool
    window_titles: tuple[str, ...]


class FakeScreenGrab:
    """A screen capture stand-in that consults a ``FakeTCC``.

    Without the grant the frame is wallpaper-only and nothing raises: the
    silent-failure trap the product guards against with a pixel sanity check.
    """

    def __init__(
        self,
        tcc: FakeTCC,
        *,
        caller: str = "screen",
        other_windows: tuple[str, ...] = ("Safari", "Notes"),
    ) -> None:
        self.tcc = tcc
        self.caller = caller
        self.other_windows = other_windows
        self.frames: list[FakeFrame] = []

    def grab(self) -> FakeFrame:
        sees_windows = self.tcc.consult_screen_grab(self.caller)
        frame = FakeFrame(
            wallpaper_only=not sees_windows,
            window_titles=self.other_windows if sees_windows else (),
        )
        self.frames.append(frame)
        return frame


# --------------------------------------------------------------------------
# Convenience constructors.
# --------------------------------------------------------------------------


def make_darwin_port(
    tcc: FakeTCC | None = None, **tcc_kwargs: Any
) -> tuple[SystemPermissionPort, FakeTCC]:
    """A real port for ``darwin`` running as the installed app, plus its fake."""
    tcc = tcc if tcc is not None else FakeTCC(**tcc_kwargs)
    return tcc.port("darwin"), tcc


def make_non_darwin_port(
    platform_name: Literal["win32", "linux"] = "linux",
    tcc: FakeTCC | None = None,
    **tcc_kwargs: Any,
) -> tuple[SystemPermissionPort, FakeTCC]:
    """A real port for Windows/Linux. ``tcc.assert_silent()`` must hold afterwards."""
    tcc = tcc if tcc is not None else FakeTCC(**tcc_kwargs)
    return tcc.port(platform_name), tcc


def install_port(monkeypatch: Any, port: SystemPermissionPort) -> SystemPermissionPort:
    """Make ``get_system_permission_port()`` answer with ``port`` for this test.

    ``monkeypatch`` is pytest's fixture. Modules that imported the getter by name
    at import time keep their own reference; the lazy imports the consumers use
    resolve it per call and see this one.
    """
    import jarvis.platform.permissions as permissions

    monkeypatch.setattr(permissions, "get_system_permission_port", lambda: port)
    monkeypatch.setattr(permissions, "_DEFAULT_SYSTEM_PERMISSION_PORT", port)
    return port


__all__ = [
    "AE_EVENT_NOT_PERMITTED",
    "AE_EVENT_WOULD_REQUIRE_USER_CONSENT",
    "AE_NO_ERR",
    "AE_PROC_NOT_FOUND",
    "DMG_BUNDLE_ID",
    "INSTALLED_BUNDLE_ID",
    "INSTALLED_BUNDLE_PATH",
    "TERMINAL_BUNDLE_ID",
    "CallKind",
    "CallOutcome",
    "DialogPolicy",
    "FakeAudioInput",
    "FakeAudioStream",
    "FakeEventTap",
    "FakeFrame",
    "FakeScreenGrab",
    "FakeTCC",
    "TccCall",
    "TccProcessAbort",
    "TccService",
    "TccState",
    "install_port",
    "make_darwin_port",
    "make_non_darwin_port",
]
