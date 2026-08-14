"""Who the assistant is in its own right — the card behind its own accounts.

WHY THIS EXISTS
An assistant that only ever borrows the user's logins has no identity at all.
Every mail it sends is from the user, every commit carries the user's name, and
nothing it did can be revoked without revoking the user. Holding accounts of its
own — ``CredentialOwner.AGENT`` in :mod:`jarvis.logins.store` — only works if it
also knows the details those accounts are registered under: the address to type
into a signup form, the number an SMS code will arrive at, the handle it pushes
under. That is this profile, and nothing more.

It is deliberately NOT a persona, a permission or a second system prompt. It is
the factual card. Behaviour keeps living where behaviour already lives.

THE DISPLAY NAME IS NOT BAKED IN
Contract §4: the user-visible name follows the wake word and is never hardcoded.
So ``display_name`` starts empty and means "whatever the assistant is currently
called"; :meth:`AgentIdentity.display_name_or` takes the resolved name as a
fallback. It is only ever set to something else when the user deliberately wants
the outside world to see a different name from the one they say out loud — a
mailbox registered as "Jarvis" while the wake word is "Athena", say.

STORAGE
The same keychain as the vault, through the same chunk-aware backend, under one
reserved entry. A profile is not a secret in the way a password is, but it is a
name, an address and a phone number in one place — and this project has already
shipped an identity inventory into a public repo by accident once. Keeping it
off disk entirely costs nothing here and removes the whole class of mistake.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Final

from jarvis.logins.store import as_pairs
from jarvis.marketplace.token_store import ChunkedBackend, KeyringBackend, TokenBackend

log = logging.getLogger(__name__)

#: The single keychain entry the profile lives in. Not prefixed like a
#: credential record, so it can never collide with a service id.
_IDENTITY_KEY: Final[str] = "agent_identity"


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    """The assistant's own contact details.

    Every field is optional and empty by default. A half-filled profile is a
    normal state, not a broken one: a user who has given the assistant a mailbox
    but no phone number yet should get exactly the mailbox behaviour, not an
    error, and the assistant should be able to say which piece is missing.
    """

    display_name: str = ""
    """Only set when the outside world should see a name other than the
    wake-word one. Empty means "use whatever I am called"."""

    email: str = ""
    """The assistant's own mailbox. The key that unlocks every other signup,
    because a registration it cannot receive mail for cannot be completed."""

    phone: str = ""
    """The assistant's own number, for SMS verification."""

    timezone: str = ""
    """IANA zone, used when it fills in a form that asks."""

    signature: str = ""
    """How it signs mail sent from its own address."""

    handles: tuple[tuple[str, str], ...] = ()
    """Account names per service — ``("github", "some-machine-user")``. A
    free-form mapping for the same reason ``kind`` is a free string: the set of
    places an assistant can hold a handle is open-ended."""

    notes: str = ""
    """The user's own remarks about this identity."""

    updated_at: datetime | None = None

    # -- reads -----------------------------------------------------------

    @property
    def is_configured(self) -> bool:
        """Does the assistant have any identity of its own yet?

        Anchored on the contactable fields rather than on "any field is set": a
        profile holding only a timezone is not an identity anything can be
        registered under, and reporting it as configured would send the model
        into a signup it cannot finish.
        """
        return bool(self.email.strip() or self.phone.strip())

    def display_name_or(self, fallback: str) -> str:
        """The name to present, falling back to the resolved assistant name."""
        return self.display_name.strip() or (fallback or "").strip()

    def handle_map(self) -> dict[str, str]:
        return dict(self.handles)

    def missing_for_signup(self) -> tuple[str, ...]:
        """What a self-service registration would still get stuck on.

        Exists so the assistant can say "I have no number of my own, so I cannot
        get past the SMS step" instead of starting a signup and failing halfway
        through with a page it cannot read.
        """
        missing: list[str] = []
        if not self.email.strip():
            missing.append("email address")
        if not self.phone.strip():
            missing.append("phone number")
        return tuple(missing)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "display_name": self.display_name,
            "email": self.email,
            "phone": self.phone,
            "timezone": self.timezone,
            "signature": self.signature,
            "handles": dict(self.handles),
            "notes": self.notes,
            "is_configured": self.is_configured,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def describe_for_model(self, fallback_name: str = "") -> str:
        """The profile as a block the brain can read before acting as itself.

        Plain lines rather than JSON because this lands in a prompt, and the
        closing instruction is the load-bearing part: without it a model that
        knows both identities will cheerfully fill a form with whichever address
        it saw most recently.
        """
        # Anchored on details something can actually be registered under, NOT
        # on "any line could be rendered". The assistant always has a name — it
        # comes from the wake word — so a name-only card would announce "your
        # own details" to a model that has no address, no number and no account
        # anywhere, and invite it to fill a form with something it invented.
        has_own_details = bool(
            self.email.strip() or self.phone.strip() or self.handles
        )
        if not has_own_details:
            return (
                "You have no identity of your own yet — no address, no number, "
                "no accounts registered in your name. You can only act on the "
                "user's behalf with their own logins. If a task needs an "
                "account of yours, say that it has to be set up first in the "
                "Passwords section; never invent details and never use the "
                "user's address as if it were yours."
            )

        name = self.display_name_or(fallback_name)
        lines: list[str] = []
        if name:
            lines.append(f"- name: {name}")
        if self.email.strip():
            lines.append(f"- email address: {self.email.strip()}")
        if self.phone.strip():
            lines.append(f"- phone number: {self.phone.strip()}")
        if self.timezone.strip():
            lines.append(f"- timezone: {self.timezone.strip()}")
        for service, handle in self.handles:
            lines.append(f"- {service} account: {handle}")
        if self.signature.strip():
            lines.append(f"- signs off as: {self.signature.strip()}")
        body = "\n".join(lines)
        tail = (
            "Use these when a form asks for YOUR details — a signup you are "
            "making in your own name, mail you send from your own address. "
            "Never enter the user's details there, and never enter yours where "
            "the user's own account is meant."
        )
        gaps = self.missing_for_signup()
        if gaps:
            tail += (
                f" You have no {' and no '.join(gaps)} of your own, so say so "
                "instead of attempting a step that needs one."
            )
        return f"Your own details:\n{body}\n\n{tail}"

    # -- serialisation ---------------------------------------------------

    def to_json(self) -> str:
        payload = {
            "display_name": self.display_name,
            "email": self.email,
            "phone": self.phone,
            "timezone": self.timezone,
            "signature": self.signature,
            "handles": dict(self.handles),
            "notes": self.notes,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        return json.dumps(payload, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> AgentIdentity:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("stored identity is not an object")
        stamp = data.get("updated_at")
        parsed: datetime | None = None
        if isinstance(stamp, str) and stamp:
            try:
                parsed = datetime.fromisoformat(stamp)
            except ValueError:
                parsed = None
            if parsed is not None and parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
        return cls(
            display_name=str(data.get("display_name") or ""),
            email=str(data.get("email") or ""),
            phone=str(data.get("phone") or ""),
            timezone=str(data.get("timezone") or ""),
            signature=str(data.get("signature") or ""),
            handles=as_pairs(data.get("handles")),
            notes=str(data.get("notes") or ""),
            updated_at=parsed,
        )


class IdentityStore:
    """Read/write access to the assistant's own profile.

    Backend injectable for the same reason the vault's is: a test must never
    reach the real keychain, and swapping one constructor argument is the whole
    mechanism.
    """

    def __init__(self, backend: TokenBackend | None = None) -> None:
        self._backend: TokenBackend = (
            backend if backend is not None else ChunkedBackend(KeyringBackend())
        )

    def load(self) -> AgentIdentity:
        """The stored profile, or an empty one.

        Never raises for an absent or damaged entry. "The assistant has no
        identity yet" and "the identity could not be parsed" produce the same
        empty profile, because in both cases the honest answer downstream is
        that there is nothing to act as — and a raise here would take out every
        caller that merely wanted to ask.
        """
        raw = self._backend.get(_IDENTITY_KEY)
        if not raw:
            return AgentIdentity()
        try:
            return AgentIdentity.from_json(raw)
        except (ValueError, KeyError, TypeError) as exc:
            log.warning("stored agent identity is unreadable (%s) — treating as unset", exc)
            return AgentIdentity()

    def save(self, identity: AgentIdentity) -> AgentIdentity:
        """Replace the profile wholesale and return the stored version."""
        stored = replace(
            identity,
            display_name=identity.display_name.strip(),
            email=identity.email.strip(),
            phone=identity.phone.strip(),
            timezone=identity.timezone.strip(),
            handles=as_pairs(identity.handles),
            updated_at=_now(),
        )
        self._backend.set(_IDENTITY_KEY, stored.to_json())
        log.info(
            "agent identity updated (email set: %s, phone set: %s)",
            bool(stored.email),
            bool(stored.phone),
        )
        return stored

    def clear(self) -> bool:
        """Remove the profile. Idempotent; reports whether anything was there."""
        if self._backend.get(_IDENTITY_KEY) is None:
            return False
        self._backend.delete(_IDENTITY_KEY)
        return True


def default_identity_store() -> IdentityStore:
    """The handle every caller should use. One seam, like ``default_store``."""
    return IdentityStore()


__all__ = [
    "AgentIdentity",
    "IdentityStore",
    "default_identity_store",
]
