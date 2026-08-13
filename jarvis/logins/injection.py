"""Move a stored secret into a browser script without the model ever seeing it.

THE PROBLEM THIS SOLVES
The ``browser`` tool takes a Python script that the *model* writes. That is the
right interface for speed — one brain round per click would spend a hundred
rounds on a single booking flow — but it collides with the vault's first rule:
if the model has to fill a login form, the password would have to be written
into the script it emits. It would then exist in the model's output, in the
stored tool call, in the activity log, and on the wire to the model provider.

THE FIX
The model writes a *reference*, never a value::

    fill(node, SECRET("github", "password"))

``resolve_secrets`` swaps that reference for the real value in this process, on
the way to the harness subprocess's stdin. The tool call that gets logged still
contains the placeholder; only the bytes on the pipe carry the secret, and they
land in a separate process that the model does not observe.

This works *because* the script is executed out-of-process. An in-process
action tool could not draw the line nearly as cleanly.

THE RETURN PATH MATTERS TOO
A script that prints a field's contents would hand the password straight back
through the tool result. ``scrub_secrets`` therefore redacts every injected
value out of stdout before anything is returned. Redaction is a backstop, not a
licence: the skill instructs scripts never to print credential fields.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Final

from jarvis.logins import store as store_module
from jarvis.logins.store import CredentialStore, LoginStatus

log = logging.getLogger(__name__)

#: What may be pulled from a record. ``username`` is not secret — the brain can
#: already read it from ``credential_lookup`` — but allowing it here keeps a
#: login script uniform instead of half-templated.
_FIELDS: Final[frozenset[str]] = frozenset({"password", "username", "totp"})

#: ``SECRET("service", "field")`` in the script text. Both quote styles, and
#: tolerant of whitespace, because the model writes this by hand.
_PLACEHOLDER_RE: Final[re.Pattern[str]] = re.compile(
    r"""SECRET\s*\(\s*(['"])(?P<service>[^'"]+)\1\s*,\s*(['"])(?P<field>[^'"]+)\3\s*\)"""
)

#: Values shorter than this are not redacted from output. A two-character
#: string occurs by chance in ordinary page text, and blanking every occurrence
#: would shred the result the user is trying to read while protecting nothing
#: that was not already trivially guessable.
_MIN_REDACT_LEN: Final[int] = 4

_REDACTED: Final[str] = "[redacted]"


class SecretResolutionError(RuntimeError):
    """A placeholder referenced something the vault cannot supply.

    Raised rather than left in place: an unresolved ``SECRET(...)`` would reach
    the harness as an undefined name, and the resulting ``NameError`` would tell
    the model nothing about what actually went wrong.
    """


@dataclass(frozen=True, slots=True)
class Resolution:
    """The script as it should be piped, plus what has to be scrubbed on return."""

    script: str
    #: Injected values, for redacting the script's output. Never logged, never
    #: returned to a caller that talks to the model.
    secrets: tuple[str, ...]
    #: Service ids the script pulls from — safe to log and to show the user.
    services: tuple[str, ...]

    @property
    def touched_vault(self) -> bool:
        return bool(self.services)


def has_secret_placeholder(script: str) -> bool:
    """Cheap check used before a store is ever opened."""
    return bool(_PLACEHOLDER_RE.search(script or ""))


def placeholder_services(script: str) -> tuple[str, ...]:
    """Service ids a script references, in order, without touching the vault.

    Used to decide the risk tier before anything is resolved, so a confirmation
    can be raised without a single secret being read.
    """
    return tuple(
        dict.fromkeys(
            match.group("service").strip()
            for match in _PLACEHOLDER_RE.finditer(script or "")
        )
    )


def _field_value(store: CredentialStore, service: str, field: str) -> str:
    credential = store.load(service)
    if credential is None:
        raise SecretResolutionError(
            f"No stored login for {service!r}. Ask the user to add it in the "
            "Passwords section — you cannot create one yourself."
        )
    if field == "password":
        value = credential.password
    elif field == "username":
        value = credential.username
    else:
        value = credential.totp_secret or ""
    if not value:
        raise SecretResolutionError(
            f"The stored login for {service!r} has no {field}. "
            "Ask the user to complete it in the Passwords section."
        )
    return value


def resolve_secrets(script: str, store: CredentialStore | None = None) -> Resolution:
    """Replace every ``SECRET(...)`` reference with the stored value.

    Substitution uses ``repr`` so the value is embedded as a proper Python
    string literal — a password containing a quote or a backslash would
    otherwise produce a syntactically broken script, and the failure would look
    like a harness bug rather than a quoting bug.
    """
    text = script or ""
    if not _PLACEHOLDER_RE.search(text):
        return Resolution(script=text, secrets=(), services=())

    # Module attribute, not an imported name: see ``store.default_store``.
    resolver = store if store is not None else store_module.default_store()
    secrets: list[str] = []
    services: list[str] = []

    def _substitute(match: re.Match[str]) -> str:
        service = match.group("service").strip()
        field = match.group("field").strip().lower()
        if field not in _FIELDS:
            raise SecretResolutionError(
                f"Unknown credential field {field!r}. "
                f"Valid fields: {', '.join(sorted(_FIELDS))}."
            )
        value = _field_value(resolver, service, field)
        if service not in services:
            services.append(service)
        # The username is not a secret and redacting it would blank ordinary
        # page text (it is usually an email that appears all over the page).
        if field != "username" and value not in secrets:
            secrets.append(value)
        return repr(value)

    resolved = _PLACEHOLDER_RE.sub(_substitute, text)
    log.info(
        "browser script draws on the login vault for: %s",
        ", ".join(services) or "(none)",
    )
    return Resolution(
        script=resolved,
        secrets=tuple(secrets),
        services=tuple(services),
    )


def scrub_secrets(text: str, secrets: tuple[str, ...] | list[str]) -> str:
    """Redact injected values from script output before it reaches the model."""
    if not text or not secrets:
        return text
    cleaned = text
    # Longest first: a short secret that is a substring of a longer one must not
    # partially redact it and leave the remainder readable.
    for value in sorted(secrets, key=len, reverse=True):
        if len(value) >= _MIN_REDACT_LEN:
            cleaned = cleaned.replace(value, _REDACTED)
    return cleaned


def needs_confirmation(services: tuple[str, ...], store: CredentialStore) -> bool:
    """Should this login be confirmed by the user before it runs?

    The agreed rule: ask once per service, then stay quiet. "Once" is defined by
    the record's own status — a credential that has never produced a successful
    login is unproven, and the first use is exactly where the user wants to see
    that Jarvis picked the right site. Afterwards the entry is trusted until a
    login is rejected, which flips it back to unproven on its own.
    """
    for service in services:
        try:
            credential = store.load(service)
        except RuntimeError:
            return True
        if credential is None or credential.status is not LoginStatus.OK:
            return True
    return False


__all__ = [
    "Resolution",
    "SecretResolutionError",
    "has_secret_placeholder",
    "needs_confirmation",
    "placeholder_services",
    "resolve_secrets",
    "scrub_secrets",
]
