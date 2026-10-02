"""The audio-ducker backend contract."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class AudioDucker(Protocol):
    """A backend that lowers other apps' audio for the length of a voice session.

    A backend MAY also offer ``prewarm() -> DuckPermissionReport``: the one place
    that asks the OS for a permission, run when the user switches the feature on.
    ``mute_others`` and ``restore`` never ask (macOS: Automation).
    """

    def mute_others(self, *, own_pid: int, never: frozenset[str]) -> list[int]:
        """Mute every other app's audio session. Returns the PIDs muted."""
        ...

    def restore(self, pids: list[int]) -> None:
        """Unmute exactly the given PIDs."""
        ...


@dataclass(frozen=True, slots=True)
class PlayerPermission:
    """The permission answer for ONE media player, as a route or an inline note shows it.

    ``outcome`` and ``reason`` carry the ``PermissionOutcome`` / ``PermissionNeeded``
    vocabulary of ``jarvis.platform.permission_service`` verbatim; ``detail`` is its
    fixed-template sentence (never exception text, a path or a window title).
    """

    player: str  # display name, e.g. "Music"
    target: str  # bundle id, e.g. "com.apple.Music"
    outcome: str
    reason: str
    can_open_settings: bool
    asked: bool
    outside_installed_app: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "player": self.player,
            "target": self.target,
            "outcome": self.outcome,
            "reason": self.reason,
            "can_open_settings": self.can_open_settings,
            "asked": self.asked,
            "outside_installed_app": self.outside_installed_app,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class DuckPermissionReport:
    """What switching "Mute music while dictating" on found out about permissions.

    Automation is per player and macOS only answers for a player that is RUNNING,
    so ``players`` holds only the players that were running (and therefore checked
    or asked). ``not_running`` names the rest: nothing was asked for them. ``note``
    is a full English sentence, set only when no player could be checked at all.
    """

    players: tuple[PlayerPermission, ...] = ()
    not_running: tuple[str, ...] = ()
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "feature": "audio_ducking",
            "checked": bool(self.players),
            "asked": any(player.asked for player in self.players),
            "note": self.note,
            "not_running": list(self.not_running),
            "players": [player.as_dict() for player in self.players],
        }
