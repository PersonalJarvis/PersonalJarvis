"""SocialProfile — the small, deliberate face this instance shows to friends.

Everything a friend can learn about you is either in this file or derived from
the count-only ledger. That is the point of keeping it separate from the user
profile the assistant builds (``USER.md``, the people store, the curator): that
profile exists to make the assistant better and is full of things nobody else
should see. Reusing it as a social profile would mean every future field added
for the assistant's benefit becomes a field friends can read.

So the social face is its own, short, hand-written object: a name, one line of
text, an optional address to reach this instance at, and two switches. Adding
something to it is a decision someone has to make on purpose.

The two switches are the master controls that sit above the per-friend
profiles:

``share_pulse``
    Off means no usage figures leave this machine for anyone, whatever any
    friend's sharing profile says. A per-friend permission can only ever narrow
    what this switch allows.

``accept_invites``
    Off means inbound handshakes are refused. Existing friends keep working;
    strangers cannot attach themselves to this node.

Stored as JSON next to the activity ledger, atomically, and every read degrades
to defaults rather than raising.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger(__name__)


class SocialProfile(BaseModel):
    """What friends see about this instance, plus the two master switches."""

    model_config = ConfigDict(frozen=False)

    display_name: str = Field(default="", max_length=120)
    tagline: str | None = Field(default=None, max_length=200)
    #: Where friends can reach this instance. Empty when the machine is not
    #: reachable from outside — the normal case, and the UI says so plainly.
    endpoint: str | None = None
    share_pulse: bool = True
    accept_invites: bool = True

    def resolved_display_name(self) -> str:
        """The name to sign invites with, never empty."""
        name = (self.display_name or "").strip()
        if name:
            return name
        return _fallback_display_name()


def default_profile_path() -> Path:
    """``<user data>/data/socials_profile.json``."""
    from jarvis.core.paths import user_data_dir

    return Path(user_data_dir()) / "data" / "socials_profile.json"


class SocialProfileStore:
    """Atomic JSON store for :class:`SocialProfile`."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else default_profile_path()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> SocialProfile:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return SocialProfile()
        except OSError as exc:
            log.warning("socials profile unreadable: %s", exc)
            return SocialProfile()
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError) as exc:
            log.warning("socials profile is corrupt, using defaults: %s", exc)
            return SocialProfile()
        if not isinstance(payload, dict):
            return SocialProfile()
        try:
            return SocialProfile.model_validate(payload)
        except Exception:  # noqa: BLE001 — a bad field must not lock the section
            log.warning("socials profile has invalid fields, using defaults")
            return SocialProfile()

    def save(self, profile: SocialProfile) -> bool:
        """Write the profile atomically. ``False`` when it could not be stored."""
        from .identity import _clean_endpoint

        profile.endpoint = _clean_endpoint(profile.endpoint)
        payload: dict[str, Any] = profile.model_dump()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=str(self._path.parent), prefix=".socials_profile_", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
                os.replace(tmp_name, self._path)
            except Exception:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except Exception:  # noqa: BLE001
            log.warning("could not save the socials profile", exc_info=True)
            return False
        return True

    def update(self, **changes: Any) -> SocialProfile:
        """Apply a partial change and persist it. Unknown keys are ignored."""
        profile = self.load()
        for key, value in changes.items():
            if value is None or key not in SocialProfile.model_fields:
                continue
            setattr(profile, key, value)
        self.save(profile)
        return profile


def _fallback_display_name() -> str:
    """A neutral name for someone who never set one.

    Deliberately not the OS user name, the machine name, or anything else the
    system happens to know: those are real identities the user never agreed to
    publish to somebody else's friends list. An unnamed instance stays unnamed
    until its owner types a name.
    """
    return "Personal Jarvis user"


__all__ = ["SocialProfile", "SocialProfileStore", "default_profile_path"]
