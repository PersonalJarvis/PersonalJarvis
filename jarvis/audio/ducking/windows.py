"""Windows Core Audio per-app duck/restore via pycaw.

Two modes, chosen by ``[ducking].duck_volume_percent``:

* ``0`` (default) — hard-mute each other session and unmute it afterwards.
* ``1..100`` — lower each other session's volume to that percentage and put
  the previous level back afterwards, the same thing the macOS backend does
  with the known players. The setting is a plain ``[ducking]`` field, not a
  macOS-only one (``macos_master_fallback`` is the one that says so), yet
  this backend used to ignore it entirely and always hard-mute: the number in
  Settings did nothing on Windows and nothing said why (AP-31).

Must be called from a thread that has done ``CoInitialize()`` — the
``AudioDuckController`` runs these inside ``asyncio.to_thread`` with
``comtypes.CoInitialize``. pycaw is imported lazily so this module is harmless
to import on a host without it (the factory only constructs this class when
``sys.platform == 'win32'`` and pycaw is present).
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("jarvis.audio.ducking")


def _normalized_never(never: frozenset[str]) -> set[str]:
    """Map never-mute entries to comparable app tokens.

    ``[ducking].never_mute`` is documented as accepting either a Windows
    process name ("Discord.exe") or a plain app name ("Spotify"), and the
    macOS ducker already normalizes both. This one compared
    ``proc.name()`` against the raw config strings, so it matched ONLY the
    exact-case executable name: a user who wrote "Spotify" or "spotify.exe"
    got their music muted anyway, silently, with the allowlist looking
    correct in Settings. Case-fold and drop the ``.exe`` / ``.app`` suffix
    so one entry means the same thing on both platforms.
    """
    out: set[str] = set()
    for name in never:
        token = name.strip().lower()
        for suffix in (".exe", ".app"):
            if token.endswith(suffix):
                token = token[: -len(suffix)]
                break
        if token:
            out.add(token)
    return out


class WindowsPycawDucker:
    def __init__(self, *, duck_volume_percent: int = 0) -> None:
        self._duck = max(0, min(100, int(duck_volume_percent)))
        #: pid -> master volume scalar before we lowered it. Only used in
        #: volume mode; mute mode needs no state (unmute is unconditional).
        self._saved: dict[int, float] = {}

    @classmethod
    def from_config(cls, cfg: Any | None) -> WindowsPycawDucker:
        try:
            ducking = getattr(cfg, "ducking", None)
            return cls(
                duck_volume_percent=int(
                    getattr(ducking, "duck_volume_percent", 0) or 0
                ),
            )
        except Exception:  # noqa: BLE001 — malformed config degrades to defaults
            log.debug("ducking config read failed; using defaults", exc_info=True)
            return cls()

    def mute_others(self, *, own_pid: int, never: frozenset[str]) -> list[int]:
        """Duck every audio session except our own PID (protects Jarvis's TTS),
        the system-sounds session (PID 0 / no process), and the name allowlist.
        Returns the PIDs actually ducked so restore() touches only those.

        Sessions the user already silenced are left alone in BOTH modes — we
        only ever touch something currently audible, so restore can never turn
        an app back on that the user had muted.
        """
        from pycaw.pycaw import AudioUtilities

        skip = _normalized_never(never)
        target = self._duck / 100.0
        ducked: list[int] = []
        for session in AudioUtilities.GetAllSessions():
            try:
                pid = session.ProcessId
                if not pid or pid == own_pid:  # 0 = system sounds; own = our TTS
                    continue
                proc = session.Process
                if skip and proc is not None:
                    name = (proc.name() or "").strip().lower()
                    if name.endswith(".exe"):
                        name = name[: -len(".exe")]
                    if name in skip:
                        continue
                vol = session.SimpleAudioVolume
                if vol.GetMute():  # already silent — nothing of ours to undo
                    continue
                if self._duck <= 0:
                    vol.SetMute(1, None)
                    ducked.append(pid)
                    continue
                prev = float(vol.GetMasterVolume())
                if prev > target:
                    vol.SetMasterVolume(target, None)
                    self._saved[pid] = prev
                    ducked.append(pid)
            except Exception:  # noqa: BLE001 — COMError on protected sessions; skip
                log.debug("ducking mute skip", exc_info=True)
        return ducked

    def restore(self, pids: list[int]) -> None:
        """Undo exactly the sessions whose PID we ducked.

        A pid with a saved volume gets that level back; anything else was
        hard-muted, so it gets unmuted. Both are keyed off what we recorded at
        duck time, never off the mode currently configured — turning the
        duck-level knob mid-session must not strand an app at 10 % or leave a
        muted one silent.
        """
        from pycaw.pycaw import AudioUtilities

        want = set(pids)
        if not want:
            return
        for session in AudioUtilities.GetAllSessions():
            try:
                pid = session.ProcessId
                if pid not in want:
                    continue
                prev = self._saved.get(pid)
                if prev is None:
                    session.SimpleAudioVolume.SetMute(0, None)
                else:
                    session.SimpleAudioVolume.SetMasterVolume(prev, None)
            except Exception:  # noqa: BLE001
                log.debug("ducking restore skip", exc_info=True)
        # Drop the bookkeeping for every requested pid, including sessions that
        # disappeared while ducked — otherwise a closed app's entry would sit
        # here forever and a later pid reuse would restore a stranger's volume.
        for pid in want:
            self._saved.pop(pid, None)
