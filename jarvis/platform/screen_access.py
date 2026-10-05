"""Screen Recording access for the appshot recorder, backed by the permission port.

The screen recorder (``jarvis.appshot.recording`` and its worker) asks this
module, on macOS only, whether the Screen Recording permission lets a capture
run. macOS fails that gate SILENTLY: without the grant a capture returns the
desktop wallpaper and no error, so the recorder checks first and refuses with a
sentence that names the permission.

Two rules:

* **Status reads never ask.** :func:`screen_recording_state` and
  :func:`state_allows_capture` are silent probes for status routes and the
  recorder's once-a-second revoke check.
* **Only a user gesture asks.** :func:`require_screen_recording_async` runs
  when the person starts a recording: it asks macOS once through
  :meth:`SystemPermissionPort.request` and raises :class:`ScreenCaptureRefused`
  unless the grant is live afterwards.

The state comes from :func:`jarvis.platform.permissions.get_system_permission_port`,
the same port Screen Context's capture gate uses. Nothing initialises at import
time (AP-26).
"""

from __future__ import annotations

import asyncio
import logging

from jarvis.platform.permissions import PermissionId, PermissionState

log = logging.getLogger(__name__)

#: States in which a capture may run: granted, or no permission exists here.
_READY = frozenset({PermissionState.GRANTED, PermissionState.NOT_REQUIRED})

_USER_DETAIL = (
    "Allow Screen Recording for Personal Jarvis in System Settings > Privacy & "
    "Security > Screen Recording, then start the recording again."
)
_AGENT_DETAIL = (
    "[permission_needed:screen_recording] Screen Recording is not granted, so no "
    "screen capture may run. Ask the user to allow it in System Settings."
)


class ScreenCaptureRefused(RuntimeError):
    """A screen capture macOS does not allow, with honest permission text.

    ``str(exc)`` is the agent sentence; ``user_detail`` the people-facing one.
    Neither carries exception text, paths or window titles.
    """

    permission = PermissionId.SCREEN_RECORDING.value

    def __init__(
        self, agent_detail: str = _AGENT_DETAIL, user_detail: str = _USER_DETAIL
    ) -> None:
        super().__init__(agent_detail)
        self.agent_detail = agent_detail
        self.user_detail = user_detail


def screen_recording_state() -> PermissionState:
    """The live Screen Recording state. Silent: it never shows a dialog."""
    from jarvis.platform.permissions import get_system_permission_port  # noqa: PLC0415

    return get_system_permission_port().state(PermissionId.SCREEN_RECORDING)


def state_allows_capture(state: PermissionState) -> bool:
    """Whether a state lets a capture proceed: GRANTED, or not required off macOS."""
    return state in _READY


def _request_and_check() -> bool:
    from jarvis.platform.permissions import get_system_permission_port  # noqa: PLC0415

    port = get_system_permission_port()
    if port.runtime_access_granted(PermissionId.SCREEN_RECORDING):
        return True
    operation = port.request(PermissionId.SCREEN_RECORDING)
    if not operation.ok:
        log.info("screen-access: Screen Recording request not performed (%s)", operation.message)
    return port.runtime_access_granted(PermissionId.SCREEN_RECORDING)


async def require_screen_recording_async(feature: str) -> None:
    """Ask macOS for Screen Recording once, for a capture the user started.

    Raises :class:`ScreenCaptureRefused` unless the grant is live afterwards.
    ``feature`` names the caller in the log only.
    """
    if state_allows_capture(await asyncio.to_thread(screen_recording_state)):
        return
    if await asyncio.to_thread(_request_and_check):
        return
    log.info("screen-access: %s refused, Screen Recording is not granted", feature)
    raise ScreenCaptureRefused()


__all__ = [
    "ScreenCaptureRefused",
    "require_screen_recording_async",
    "screen_recording_state",
    "state_allows_capture",
]
