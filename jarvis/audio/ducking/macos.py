"""macOS AppleScript audio ducking (tiered: known players, then master volume).

Lowers the app-internal ``sound volume`` of the known media players (Music,
Spotify) via ``osascript`` for the duration of a voice session and restores
the previous volume afterwards. Optionally (opt-in) falls back to lowering the
MASTER output volume when no known player was ducked — note the master
fallback also lowers Jarvis's own TTS voice.

Safe to import on any OS: the factory only constructs this class when
``sys.platform == 'darwin'`` and ``osascript`` is on PATH. Every osascript
call is wrapped — a timeout, a non-zero exit (e.g. the Automation TCC denial
``-1743``), or an unparsable volume degrades to a skipped player, never an
exception out of the runtime path.

The Automation consent is owned by ``jarvis.platform.permission_service`` and
asked ONLY from :meth:`MacOSScriptDucker.prewarm`, which runs when the user
switches the feature on while a player is open. ``mute_others`` never asks: it
scripts a player only when the service reads its Automation grant as GRANTED,
and a player without the grant is skipped for the session (a running one is
recorded through a background episode, status snapshot only). A player whose
Apple Event is refused with ``-1743`` although the read said GRANTED is skipped
too, and the service is told through ``report_failed_use`` (one background
``needs_settings`` episode naming the player); the next send that lands tells it
through ``report_use_ok`` and the episode ends. The player list is shared with
the port so the scripts and the permission row can never disagree.

CRITICAL script shape: a bare ``tell application ...`` LAUNCHES the app, so
every script guards with ``if application id "..." is running`` INSIDE the
same script and returns ``"-"`` when the player is not running.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from jarvis.audio.ducking.protocol import DuckPermissionReport, PlayerPermission
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.platform.permissions import AUTOMATION_TARGETS, PermissionId, PermissionState

if TYPE_CHECKING:
    from jarvis.core.protocols import PermissionGate

log = logging.getLogger("jarvis.audio.ducking")

# Opaque restore tokens — the controller treats them as an opaque list[int]
# of "PIDs", so player tokens and the master token just have to be distinct.
_PLAYERS: dict[int, tuple[str, str]] = dict(enumerate(AUTOMATION_TARGETS, start=1))
_MASTER_TOKEN = 100

# Sentinel a script returns when the player is not running.
_NOT_RUNNING = "-"

# A duck/restore script must never stall a voice session. It never carries an
# Automation consent dialog either: mute_others scripts a player only after the
# permission service read its grant as GRANTED, so there is nothing for a 3 s
# kill to tear down (it used to kill the dialog before the user could click, and
# every session asked again). The ask lives in prewarm(), through the service.
_SCRIPT_TIMEOUT_S = 3.0

# The permission-service feature name every ducking read and ask is filed under.
_FEATURE = "audio_ducking"
# How long mute_others waits for a silent Automation read before it skips the
# player. The read is an in-process Apple Event that can hang for a running
# player without a window (Apple forums thread 666528); it must never stall a
# voice session nor the controller lock above it.
_PROBE_TIMEOUT_S = 2.5
# The budget of ONE ask inside prewarm(): the port's consent runner is killed after
# 120 s, so a call that is still out after this long has hung. It is per player, not
# per prewarm: a shared deadline let the second ask start with only the leftover (a
# few seconds), time out, report "unavailable" and still leave its dialog on screen.
_ASK_BUDGET_S = 130.0
# The Automation request runs on the service's own daemon thread and ensure(wait_s=0)
# answers PENDING at once, so prewarm() (a WORKER thread, never the loop or a lock)
# passes wait_s and waits on the request thread's event. The wait ends a margin
# BEFORE the budget above, so the service can answer "pending" itself and the budget
# stays the backstop for a native call that hangs. The margin also covers reading the
# answer after the consent runner is killed (120 s).
_ASK_WAIT_MARGIN_S = 5.0


def _ask_wait_s() -> float:
    """How long ensure() may wait for one player's dialog: the budget minus a margin."""
    budget = _ASK_BUDGET_S
    return max(0.0, budget - min(_ASK_WAIT_MARGIN_S, budget * 0.1))


def _run_osascript(
    script: str, *, timeout: float = _SCRIPT_TIMEOUT_S
) -> subprocess.CompletedProcess:
    """Default runner: one bounded, windowless osascript invocation."""
    return subprocess.run(  # noqa: S603, S607 — fixed argv, no shell
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )


def _duck_script(bundle_id: str, target: int) -> str:
    """Read the player volume and duck it — one script, is-running-guarded."""
    return (
        f'if application id "{bundle_id}" is running then\n'
        f'    tell application id "{bundle_id}"\n'
        f"        set prev to sound volume\n"
        f"        if prev > {target} then set sound volume to {target}\n"
        f"        return prev\n"
        f"    end tell\n"
        f"else\n"
        f'    return "{_NOT_RUNNING}"\n'
        f"end if"
    )


def _restore_script(bundle_id: str, volume: int) -> str:
    return (
        f'if application id "{bundle_id}" is running then\n'
        f'    tell application id "{bundle_id}" to set sound volume to {volume}\n'
        f"end if"
    )


def _is_running_script(bundle_id: str) -> str:
    """Pure running-state query — never launches or scripts the app itself."""
    return (
        f'if application id "{bundle_id}" is running then\n'
        f'    return "+"\n'
        f"else\n"
        f'    return "{_NOT_RUNNING}"\n'
        f"end if"
    )


def _master_duck_script(target: int) -> str:
    return (
        "set prev to output volume of (get volume settings)\n"
        f"if prev > {target} then set volume output volume {target}\n"
        "return prev"
    )


def _refused_not_authorized(proc: Any) -> bool:
    """Whether a script failed with the Automation denial ``-1743`` (documented)."""
    if getattr(proc, "returncode", 0) == 0:
        return False
    return "-1743" in (getattr(proc, "stderr", "") or "")


class _BoundedCall:
    """Run ONE call at a time on a daemon thread and wait for it at most ``timeout_s``.

    A native Apple Event can hang (see :data:`_PROBE_TIMEOUT_S`) and an Automation
    ask blocks until its dialog is answered. The caller gets ``(False, None)`` after
    the timeout and the call keeps its thread; while that thread lives no second one
    starts, so a hung call cannot pile up threads. A late answer is discarded.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def run(self, call: Callable[[], Any], timeout_s: float) -> tuple[bool, Any]:
        box: dict[str, Any] = {}

        def target() -> None:
            try:
                box["value"] = call()
            except Exception:  # noqa: BLE001 - the thread must not die loudly; the caller degrades
                log.debug("The bounded %s call failed.", self._name, exc_info=True)
            else:
                box["ok"] = True

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                log.debug("A bounded %s call is still out; not starting another.", self._name)
                return False, None
            thread = threading.Thread(target=target, name=f"ducking-{self._name}", daemon=True)
            self._thread = thread
            thread.start()
        thread.join(max(0.0, timeout_s))
        if thread.is_alive():
            log.debug("The bounded %s call did not finish within %.1f s.", self._name, timeout_s)
            return False, None
        return bool(box.get("ok")), box.get("value")


class MacOSScriptDucker:
    """Tiered AppleScript ducker: known players first, opt-in master fallback.

    Automation (Apple Events to Music/Spotify) is the only permission involved.
    ``access_gate`` is the permission layer (``PermissionGate``); the default
    resolves the process-wide service on every call, so a test that installs its
    own port or injects a fake gate is honoured.
    """

    def __init__(
        self,
        *,
        master_fallback: bool = False,
        duck_volume_percent: int = 0,
        run: Callable[[str], subprocess.CompletedProcess] | None = None,
        access_gate: PermissionGate | None = None,
    ) -> None:
        self._master_fallback = bool(master_fallback)
        self._duck = max(0, min(100, int(duck_volume_percent)))
        self._run = run or _run_osascript
        self._access_gate = access_gate
        self._saved: dict[int, int] = {}  # token -> previous volume
        # One bounded slot per player for the silent reads, one for the ask, so a
        # hung read of one player never blocks another player or the ask.
        self._reads = {
            bundle_id: _BoundedCall(f"read-{name}") for name, bundle_id in _PLAYERS.values()
        }
        self._ask_slot = _BoundedCall("ask")
        # Players whose volume command was refused with -1743 although the service
        # read their grant as GRANTED (the grant may belong to another app: the
        # attribution of a child osascript is UNVERIFIED), and whose refusal has not
        # been followed by a send that landed. mute_others() reports the refusal to
        # the service (report_failed_use) and, on the next landed send, the success
        # (report_use_ok, only for a player in this set, so a healthy session never
        # calls the service); prewarm() reports such a player as needs_settings
        # instead of "granted", the same thing the status snapshot says.
        self._refused_after_grant: set[str] = set()

    @classmethod
    def from_config(cls, cfg: Any | None) -> MacOSScriptDucker:
        try:
            ducking = getattr(cfg, "ducking", None)
            return cls(
                master_fallback=bool(getattr(ducking, "macos_master_fallback", False)),
                duck_volume_percent=int(getattr(ducking, "duck_volume_percent", 0) or 0),
            )
        except Exception:  # noqa: BLE001 — malformed config degrades to defaults
            log.debug("ducking config read failed; using defaults", exc_info=True)
            return cls()

    # ---- protocol ---------------------------------------------------------
    def mute_others(self, *, own_pid: int, never: frozenset[str]) -> list[int]:
        """Duck every running known player; master fallback if none was ducked.

        ``own_pid`` is unused (protocol conformance): the player tier changes
        per-app volumes, so Jarvis's own TTS is never affected by it. Returns
        opaque restore tokens (the controller treats them as PIDs).

        NON-INTERACTIVE: a player is scripted only when the permission service
        reads its Automation grant as GRANTED. Scripting a player macOS has not
        been asked about would raise the consent dialog in the middle of a
        dictation, so a player without the grant is skipped for this session and
        nothing is ever asked from here (see :meth:`prewarm`). A player that
        refuses the Apple Event with ``-1743`` although the read said GRANTED is
        skipped too and reported to the service (``report_failed_use``, one
        background episode); the next send that lands reports the success
        (``report_use_ok``). Every service call from here is bounded, because the
        controller holds its lock around this method. Never launches a player.
        """
        del own_pid  # per-app player volumes never touch our own process
        skip = self._normalized_never(never)
        ducked: list[int] = []
        # "A known player is running" is NOT the same as "we ducked one": a
        # player already sitting at (or below) the duck volume is running and
        # handled, it simply needs no change. Falling back to the MASTER output
        # in that case lowered Jarvis's own TTS — the exact side effect the
        # opt-in fallback exists to keep rare — for no gain at all.
        player_running = False
        skipped: list[tuple[str, str]] = []
        for token, (name, bundle_id) in _PLAYERS.items():
            if name.lower() in skip:
                skipped.append((name, bundle_id))
                continue
            try:
                if not self._may_script(name, bundle_id):
                    continue  # no live grant: skipped, never asked from a session
                proc = self._run(_duck_script(bundle_id, self._duck))
                if _refused_not_authorized(proc):
                    self._note_refused_after_grant(name, bundle_id)
                    continue
                prev = self._parse_volume(proc, name)
                if prev is None:
                    continue  # not running, or the script failed → not handled
                self._note_send_landed(name, bundle_id)
                player_running = True
                if prev > self._duck:
                    self._saved[token] = prev
                    ducked.append(token)
                elif token in self._saved:
                    # The player is still sitting at the duck volume AND we
                    # still hold the level it had before: a previous restore
                    # never landed (osascript timeout, an Automation prompt
                    # dismissed, Jarvis killed mid-session). Without this the
                    # token can never come back — the duck script reads the
                    # already-ducked volume, `prev > duck` stays false, so no
                    # session ever reports it again and the saved level is
                    # stranded in this dict while the user's music stays
                    # silent for good. Hand it to THIS session's restore.
                    log.info(
                        "ducking: re-adopting %s (still at the duck volume with "
                        "an unrestored level of %d)",
                        name,
                        self._saved[token],
                    )
                    ducked.append(token)
            except Exception:  # noqa: BLE001 — timeout/TCC denial: skip player
                log.debug("ducking skip (%s)", name, exc_info=True)
        if not ducked and not player_running and self._master_fallback:
            # never_mute must beat the fallback: the master output carries a
            # protected player's audio too, so ducking it would silence exactly
            # the app the user told us to leave alone. A merely-listed player
            # that is NOT running keeps the fallback available for unknown
            # audio sources (browsers etc.).
            if self._any_running(skipped):
                log.debug("ducking: master fallback withheld (never-mute player running)")
                return ducked
            try:
                proc = self._run(_master_duck_script(self._duck))
                prev = self._parse_volume(proc, "master")
                if prev is None:
                    pass
                elif prev > self._duck:
                    self._saved[_MASTER_TOKEN] = prev
                    ducked.append(_MASTER_TOKEN)
                elif _MASTER_TOKEN in self._saved:
                    # Same stranded-restore shape as the player re-adoption
                    # above, but for the WHOLE output volume: a master restore
                    # that never landed leaves the Mac quiet for good unless
                    # this session's restore takes the token over.
                    log.info(
                        "ducking: re-adopting the master output (still at the "
                        "duck volume with an unrestored level of %d)",
                        self._saved[_MASTER_TOKEN],
                    )
                    ducked.append(_MASTER_TOKEN)
            except Exception:  # noqa: BLE001
                log.debug("ducking skip (master)", exc_info=True)
        return ducked

    def restore(self, pids: list[int]) -> None:
        """Restore exactly the given tokens. Idempotent; unknown token = no-op.

        Deliberately NOT permission-gated: it only undoes a duck that was made under a
        live grant, a grant revoked since fails with ``-1743`` (kept for re-adoption
        below, and macOS shows no dialog for a decision on file), and a skipped
        restore would leave the user's music silent for good. It also runs on the
        shutdown path, which must never wait for an Automation read.
        """
        for token in pids:
            prev = self._saved.get(token)
            if prev is None:
                continue
            try:
                if token == _MASTER_TOKEN:
                    proc = self._run(f"set volume output volume {prev}")
                else:
                    _name, bundle_id = _PLAYERS[token]
                    proc = self._run(_restore_script(bundle_id, prev))
                if getattr(proc, "returncode", 1) != 0:
                    # rc != 0 (e.g. a dismissed Automation prompt, -1743) is a
                    # restore that never landed. The saved level is the very
                    # thing the next session's re-adoption needs — popping it
                    # here stranded the duck for good.
                    log.debug(
                        "ducking restore did not land (token=%s, rc=%s): %s",
                        token,
                        getattr(proc, "returncode", None),
                        (getattr(proc, "stderr", "") or "").strip(),
                    )
                    continue
                self._saved.pop(token, None)
            except Exception:  # noqa: BLE001
                log.debug("ducking restore skip (token=%s)", token, exc_info=True)

    def prewarm(self) -> DuckPermissionReport:
        """Ask for Automation, once, for each player that is running. THE asking path.

        Called when the user switches "Mute music while dictating" on: the switch
        is the gesture. Run it from a WORKER thread (it blocks until the macOS
        dialog is answered, up to 120 s per player) and never under a lock. The
        ask goes through the permission service and its killable consent runner;
        a player that is not running is neither launched nor asked (macOS only
        shows the dialog for a running player), and a decision already on file is
        never asked again. The returned report says, per player that ran, what
        the OS answered; the request's return value is never taken as a grant.
        "Running" is the pure ``is running`` query, which sends no Apple event to the
        player (UNVERIFIED on a real Mac).
        """
        players: list[PlayerPermission] = []
        not_running: list[str] = []
        for _token, (name, bundle_id) in _PLAYERS.items():
            if not self._any_running([(name, bundle_id)]):
                not_running.append(name)
                continue
            players.append(self._ask_player(name, bundle_id))
        note = ""
        if not players:
            names = " or ".join(name for name, _bundle_id in _PLAYERS.values())
            note = (
                f"Automation access is checked while the player is running. {names} is not "
                "open, so nothing was asked. Switch this on again while a player is open."
            )
        return DuckPermissionReport(
            players=tuple(players), not_running=tuple(not_running), note=note
        )

    # ---- permission ---------------------------------------------------------
    def _gate(self) -> PermissionGate:
        """The permission layer, resolved per call so a test's port or gate applies."""
        if self._access_gate is not None:
            return self._access_gate
        from jarvis.platform.permission_service import get_permission_service

        return get_permission_service()

    def _may_script(self, name: str, bundle_id: str) -> bool:
        """``True`` only for a live GRANTED Automation read. Silent: never asks.

        A player without the grant is skipped. When it is running, the miss is
        recorded as a ``background`` episode (status snapshot only, never a
        toast) so the person can find out why the music was not ducked.
        """
        state = self._read_state(name, bundle_id)
        if state is PermissionState.GRANTED:
            return True
        if state is PermissionState.NOT_REQUIRED:
            return False  # not installed (or not macOS): nothing to duck
        log.debug("ducking: skipping %s (Automation read %s)", name, state.value)
        if self._any_running([(name, bundle_id)]):
            self._record_background_episode(name, bundle_id)
        return False

    def _read_state(self, name: str, bundle_id: str) -> PermissionState:
        """One bounded, silent Automation read of ONE player; UNAVAILABLE on no answer."""
        finished, state = self._reads[bundle_id].run(
            lambda: self._gate().check(PermissionId.AUTOMATION, target=bundle_id),
            _PROBE_TIMEOUT_S,
        )
        if finished:
            try:
                return PermissionState(state)
            except ValueError:
                log.debug("ducking: the Automation read for %s was not a state", name)
        else:
            log.debug("ducking: the Automation read for %s gave no answer", name)
        return PermissionState.UNAVAILABLE

    def _record_background_episode(self, name: str, bundle_id: str) -> None:
        """Open (or join) the ``background`` episode of a running, ungranted player.

        ``interactive=False``: the service never makes a native request for it.
        """
        finished, _result = self._reads[bundle_id].run(
            lambda: self._gate().ensure(
                PermissionId.AUTOMATION,
                feature=_FEATURE,
                interactive=False,
                target=bundle_id,
            ),
            _PROBE_TIMEOUT_S,
        )
        if not finished:
            log.debug("ducking: the background episode for %s was not recorded", name)

    def _note_refused_after_grant(self, name: str, bundle_id: str) -> None:
        """The player refused a volume command (-1743) although its grant read GRANTED.

        The grant may belong to another app (the attribution of a child
        ``osascript`` is UNVERIFIED) or it was just revoked. The player is
        skipped for this session and the permission cache is dropped so the next
        read is live. The service is told through ``report_failed_use``: ONE
        ``background`` ``needs_settings`` episode naming the player (status snapshot
        only, never a toast), and no native request is made
        because of it. A live state that no longer reads granted is described by
        the service itself (its real reason). A gate without the call (a scripted
        stub) gets the older, weaker path: a non-interactive ensure, which opens an
        episode only when the live state is no longer granted. prewarm() keeps
        reporting such a player as ``needs_settings`` until a send lands again.
        """
        log.info(
            "ducking: %s refused the volume command although Automation read as granted "
            "(-1743); skipping it",
            name,
        )
        self._refused_after_grant.add(bundle_id)
        invalidate = getattr(self._gate(), "invalidate", None)
        if callable(invalidate):
            invalidate(PermissionId.AUTOMATION)
        report = getattr(self._gate(), "report_failed_use", None)
        if not callable(report):
            self._record_background_episode(name, bundle_id)
            return
        self._tell_service(
            name,
            bundle_id,
            "report_failed_use",
            lambda: report(
                PermissionId.AUTOMATION,
                feature=_FEATURE,
                target=bundle_id,
                reason="needs_settings",
                origin="background",
            ),
        )

    def _note_send_landed(self, name: str, bundle_id: str) -> None:
        """A volume command to the player landed: a refusal reported earlier is over.

        Only a player that was reported as refused costs a service call, so a
        healthy session never touches the service here. The memory is dropped
        whether or not the call finishes (the send did land; prewarm() must not keep
        calling the player blocked), and a gate without ``report_use_ok`` is simply
        not told (the episode then ends through its own ten minute TTL).
        """
        if bundle_id not in self._refused_after_grant:
            return
        self._refused_after_grant.discard(bundle_id)
        report = getattr(self._gate(), "report_use_ok", None)
        if not callable(report):
            return
        self._tell_service(
            name,
            bundle_id,
            "report_use_ok",
            lambda: report(PermissionId.AUTOMATION, feature=_FEATURE, target=bundle_id),
        )

    def _tell_service(self, name: str, bundle_id: str, what: str, call: Callable[[], Any]) -> None:
        """Run ONE service report on the player's bounded slot, never past the probe timeout.

        ``mute_others`` runs under the controller's lock, so a report is as bounded
        as a read: a service call that hangs costs at most :data:`_PROBE_TIMEOUT_S`
        and the session goes on without it. Nothing here asks the OS anything.
        """
        finished, _result = self._reads[bundle_id].run(call, _PROBE_TIMEOUT_S)
        if not finished:
            log.debug("ducking: %s for %s was not delivered to the permission service", what, name)

    def _ask_player(self, name: str, bundle_id: str) -> PlayerPermission:
        """Ask the service about ONE running player (interactive, off every lock).

        Every ask gets the full :data:`_ASK_BUDGET_S`, so a consent run (killed by
        the port after 120 s) is never cut off by the time an earlier player used.
        The service runs the consent on its own thread and answers PENDING at once
        for ``wait_s=0``, so this worker thread passes :func:`_ask_wait_s` and waits
        for the answer there; a dialog still open at the end of the wait is reported
        as ``pending`` (the answer then arrives through ``PermissionResolved``).
        """
        finished, result = self._ask_slot.run(
            lambda: self._gate().ensure(
                PermissionId.AUTOMATION,
                feature=_FEATURE,
                interactive=True,
                wait_s=_ask_wait_s(),
                target=bundle_id,
            ),
            _ASK_BUDGET_S,
        )
        if not finished or result is None:
            # A hung native call: nothing was decided, so it is never "granted".
            log.debug("ducking: the Automation ask for %s did not finish", name)
            return self._player_answer(name, bundle_id, "unavailable")
        if result.granted and bundle_id in self._refused_after_grant:
            if result.asked:
                # A fresh ask just went through the real sender and was allowed: that
                # is a send that landed, so the refusal reported earlier is over.
                self._note_send_landed(name, bundle_id)
            else:
                return self._player_answer(name, bundle_id, "needs_settings", refused_use=True)
        return PlayerPermission(
            player=name,
            target=bundle_id,
            outcome=str(result.outcome.value),
            reason=result.reason,
            can_open_settings=result.can_open_settings,
            asked=result.asked,
            outside_installed_app=result.outside_installed_app,
            detail=result.user_detail,
        )

    @staticmethod
    def _player_answer(
        name: str, bundle_id: str, reason: str, *, refused_use: bool = False
    ) -> PlayerPermission:
        """An answer the ducker decided itself (not the service): fixed-template sentence.

        ``refused_use`` is the sentence of a player whose volume command was refused
        although its access reads as allowed (the same one the service's episode carries).
        """
        from jarvis.platform.permission_service import user_detail_for

        return PlayerPermission(
            player=name,
            target=bundle_id,
            outcome=reason,
            reason=reason,
            can_open_settings=reason == "needs_settings",
            asked=False,
            outside_installed_app=False,
            detail=user_detail_for(
                PermissionId.AUTOMATION, reason, target=bundle_id, refused_use=refused_use
            ),
        )

    # ---- internals ---------------------------------------------------------
    def _any_running(self, players: list[tuple[str, str]]) -> bool:
        """True when any of the given players is currently running."""
        for name, bundle_id in players:
            try:
                proc = self._run(_is_running_script(bundle_id))
            except Exception:  # noqa: BLE001 — probe failure = assume not running
                log.debug("ducking is-running probe skip (%s)", name, exc_info=True)
                continue
            out = (getattr(proc, "stdout", "") or "").strip()
            if getattr(proc, "returncode", 1) == 0 and out and out != _NOT_RUNNING:
                return True
        return False

    @staticmethod
    def _normalized_never(never: frozenset[str]) -> set[str]:
        """Map never-mute entries to player names: case-insensitive, and the
        Windows-style ``.exe`` / macOS ``.app`` suffixes are stripped so one
        allowlist entry ("Spotify.exe") covers both platforms.
        """
        out: set[str] = set()
        for name in never:
            n = name.strip().lower()
            for suffix in (".exe", ".app"):
                if n.endswith(suffix):
                    n = n[: -len(suffix)]
            out.add(n)
        return out

    def _parse_volume(self, proc: Any, target: str) -> int | None:
        """Previous volume from a duck script, or None when not duckable."""
        if getattr(proc, "returncode", 1) != 0:
            log.debug(
                "ducking osascript rc=%s (%s): %s",
                getattr(proc, "returncode", None),
                target,
                (getattr(proc, "stderr", "") or "").strip(),
            )
            return None
        out = (getattr(proc, "stdout", "") or "").strip()
        if not out or out == _NOT_RUNNING:
            return None  # player not running
        return int(float(out))  # ValueError degrades via the caller's except
