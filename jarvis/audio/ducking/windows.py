"""Windows Core Audio per-app mute/restore via pycaw.

Must be called from a thread that has done ``CoInitialize()`` — the
``AudioDuckController`` runs these inside ``asyncio.to_thread`` with
``comtypes.CoInitialize``. pycaw is imported lazily so this module is harmless
to import on a host without it (the factory only constructs this class when
``sys.platform == 'win32'`` and pycaw is present).
"""
from __future__ import annotations

import logging

log = logging.getLogger("jarvis.audio.ducking")


class WindowsPycawDucker:
    def mute_others(self, *, own_pid: int, never: frozenset[str]) -> list[int]:
        """Mute every audio session except our own PID (protects Jarvis's TTS),
        the system-sounds session (PID 0 / no process), and the name allowlist.
        Returns the PIDs actually muted so restore() touches only those.
        """
        from pycaw.pycaw import AudioUtilities

        muted: list[int] = []
        for session in AudioUtilities.GetAllSessions():
            try:
                pid = session.ProcessId
                if not pid or pid == own_pid:  # 0 = system sounds; own = our TTS
                    continue
                proc = session.Process
                if proc is not None and never and proc.name() in never:
                    continue
                vol = session.SimpleAudioVolume
                if not vol.GetMute():  # only mute ones currently audible
                    vol.SetMute(1, None)
                    muted.append(pid)
            except Exception:  # noqa: BLE001 — COMError on protected sessions; skip
                log.debug("ducking mute skip", exc_info=True)
        return muted

    def restore(self, pids: list[int]) -> None:
        """Unmute exactly the sessions whose PID we muted."""
        from pycaw.pycaw import AudioUtilities

        want = set(pids)
        if not want:
            return
        for session in AudioUtilities.GetAllSessions():
            try:
                if session.ProcessId in want:
                    session.SimpleAudioVolume.SetMute(0, None)
            except Exception:  # noqa: BLE001
                log.debug("ducking restore skip", exc_info=True)
