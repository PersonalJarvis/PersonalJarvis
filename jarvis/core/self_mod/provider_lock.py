"""User-only provider-selection lock.

The active brain provider — and the other brain provider-selection keys that
decide which provider actually runs a turn — is the user's HARD choice. It may
change ONLY through an explicit user action: the control CLI or the manual
provider switch in the desktop app (``actor=AuditActor.USER``). It must NEVER
change through Jarvis itself (a voice/chat self-mod, ``actor != USER``) or any
automatic mechanism.

This is deliberately NARROWER than ``forbidden.py``: a forbidden path is never
self-mutable at all, whereas a locked path stays fully writable by the user —
it is only Jarvis (and other non-user actors) that is refused. So the lock is
enforced *together with* the actor in the writer, not as a blanket deny.

Kept dependency-free (string patterns + ``fnmatch`` only) so both the writer
and the schema introspector can consult it without an import cycle, mirroring
``forbidden.py``.
"""

from __future__ import annotations

from fnmatch import fnmatch

# The brain provider-selection keys. ``brain.primary`` is the active provider;
# the routing/fallback keys are included because the router runs first every
# turn, so letting Jarvis flip them would switch the live provider just as
# effectively as flipping ``primary``. The per-provider MODEL picker
# (``brain.providers.<p>.model``) and the TTS/STT provider switches are
# intentionally NOT here — only the brain provider is locked.
PROVIDER_LOCK_PATHS: tuple[str, ...] = (
    "brain.primary",
    "brain.fallback",
    "brain.deep_brain",
    "brain.routing_provider",
    "brain.router.provider",
    "brain.router.fallback_provider",
)


def is_provider_lock_path(path: str) -> bool:
    """True if ``path`` is a brain provider-selection key that only the user
    (CLI / manual UI switch) may change — never Jarvis itself or an automation.
    """
    return any(fnmatch(path, pattern) for pattern in PROVIDER_LOCK_PATHS)


__all__ = ["PROVIDER_LOCK_PATHS", "is_provider_lock_path"]
