"""Persistence for the user's own website logins.

Storage is the OS keychain — Windows Credential Manager, macOS Keychain, Linux
Secret Service — via the portable ``jarvis.core.config`` secret helpers, with
the documented headless 0600-file fallback underneath. Nothing about a login
lands in ``jarvis.toml`` or in any file the repo can see.

WHY THE WHOLE RECORD, NOT JUST THE PASSWORD
The obvious split — password in the keychain, "harmless" metadata in a JSON
file — is wrong here. The metadata IS the sensitive part in aggregate: a list
of every service the user holds an account with, each with the exact address it
is registered under, is an identity inventory. This project has already shipped
one of those into a public repo by accident. So the entire record goes into the
keychain and the vault has no on-disk footprint at all.

That choice costs one thing: a Credential Manager entry caps at ~1280 chars and
a markdown note easily exceeds it. ``ChunkedBackend`` already solves exactly
that for OAuth blobs, so it is reused here rather than reimplemented — it is a
generic storage utility, and the one in ``marketplace.token_store`` is the
battle-tested copy (its own docstring warns against "simplifying" it away).

ENUMERATION
A keychain cannot be listed, so the set of known service ids is kept in its own
entry (``credential_index``). Writes go record-first, index-second: a crash
between the two leaves an unreferenced record, which is invisible and harmless,
whereas the opposite order would leave the index pointing at nothing and break
every listing. ``list_summaries`` self-heals an index entry whose record has
gone missing instead of raising.

WHOSE ACCOUNT IS IT
Every record names an :class:`CredentialOwner`. ``USER`` is the maintainer's own
account, used to act *on their behalf*; ``AGENT`` is an account the assistant
holds in its own name — its own mailbox, its own forge account, its own number —
used to act *as itself*. The distinction is the point of the field: an assistant
that only ever borrows the user's logins puts the user's name on everything it
does and cannot be revoked without revoking the user. A record written before
this field existed reads back as ``USER``, which is what it always was.

A lookup that names no owner defaults towards the user: when both identities
hold a login for the same site, :meth:`CredentialStore.find_for_url` returns
the user's record. Acting on the user's behalf is the assistant's default
posture, so the unnamed case must resolve to the account the user recognises;
the assistant's own account is only picked when the caller asks for it.

The owner lives on the record rather than in a second, parallel vault on
purpose. Two stores would mean two code paths that can leak a password, two
index entries to keep consistent, and a merge problem the first time something
needs to ask "is there any login for this domain at all". One store with an
owner column answers that in a single pass.

NOT ONLY WEBSITES
``kind`` is a free-form string, not a closed type, because the set of things an
assistant can be given an account for is open-ended — a mailbox, a phone number,
a forge account, an API key, a smart-home hub, whatever exists next year. A
closed enum here would have to be widened in every layer that mirrors it, which
is exactly the multi-layer drift the contract's AP-4 warns about. ``fields``
carries whatever non-secret detail that kind needs (an address, a handle, a base
URL) and ``secrets`` carries any additional secret values beyond the password,
so an API token and a website login are the same shape of record.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final
from urllib.parse import urlsplit

# Generic chunk-aware keychain storage. See the module docstring for why this
# import crosses to the marketplace package instead of being copied.
from jarvis.marketplace.token_store import ChunkedBackend, KeyringBackend, TokenBackend

log = logging.getLogger(__name__)

#: Keychain entry holding the JSON list of known service ids.
_INDEX_KEY: Final[str] = "credential_index"

#: Per-record entry prefix. One logical record per service id.
_RECORD_PREFIX: Final[str] = "credential_"

#: Characters allowed in a service id. It becomes part of a keychain key, so it
#: stays boring on purpose — no separators, no spaces, no case surprises.
_ID_ALLOWED: Final[frozenset[str]] = frozenset(
    "abcdefghijklmnopqrstuvwxyz0123456789-_"
)


#: What a record is for when nobody says otherwise. A free string rather than a
#: member of a closed type — see the module docstring on "NOT ONLY WEBSITES".
DEFAULT_KIND: Final[str] = "website"


class CredentialOwner(StrEnum):
    """Whose account a record describes.

    Not a permission and not a secrecy level: both owners live in the same
    keychain and neither is readable by the model. It answers one question —
    when this credential is used, whose name is on the action?
    """

    USER = "user"
    """The maintainer's own account. The assistant acts on their behalf."""
    AGENT = "agent"
    """An account the assistant holds itself. It acts as itself."""


class LoginStatus(StrEnum):
    """Did the stored credential actually work the last time it was used?

    Without this a stale password is indistinguishable from an unused one until
    a mission fails at 3am. It is set by whoever performs a login, never guessed.
    """

    UNKNOWN = "unknown"
    """Never used for a login yet."""
    OK = "ok"
    """The last login with this credential succeeded."""
    REJECTED = "rejected"
    """The last login was refused — most likely the password changed."""


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_dt(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_owner(raw: object) -> CredentialOwner:
    """Read an owner out of stored JSON, defaulting to the user's own account.

    A record written before the field existed has no owner, and one written by a
    newer build may name an owner this version has never heard of. Both read as
    ``USER``. That is the safe direction to fail: the worst outcome is a
    confirmation the user did not strictly need, never an action quietly
    attributed to an identity that did not perform it.
    """
    if not isinstance(raw, str):
        return CredentialOwner.USER
    try:
        return CredentialOwner(raw)
    except ValueError:
        return CredentialOwner.USER


def as_pairs(raw: object) -> tuple[tuple[str, str], ...]:
    """Normalise mapping-shaped input into the immutable pair form used here.

    A record is a frozen dataclass, and a plain dict inside one makes it
    unhashable — which breaks silently, at the first ``set`` of records, far
    from the line that introduced it. Pairs keep the record a proper value
    object; :meth:`Credential.field_map` hands a dict back to callers that want
    one. Empty keys and ``None`` values are dropped rather than stored as holes.

    Accepts both shapes it can legitimately arrive in: a dict, as JSON decodes
    it, and the pair form a caller already holds. Handling only one would push a
    conversion onto every call site, and the one that forgot would store a dict
    inside the frozen record.
    """
    if isinstance(raw, Mapping):
        pairs: list[tuple[object, object]] = list(raw.items())
    elif isinstance(raw, list | tuple):
        pairs = [
            (item[0], item[1])
            for item in raw
            if isinstance(item, list | tuple) and len(item) == 2
        ]
    else:
        return ()
    return tuple(
        (str(key).strip(), str(value))
        for key, value in pairs
        if str(key).strip() and value is not None
    )


def normalize_service_id(raw: str) -> str:
    """Fold a free-form name into a usable service id, or raise.

    Accepts what a human types ("GitHub", "my bank") and returns the slug the
    keychain key is built from.
    """
    slug = "".join(
        ch if ch in _ID_ALLOWED else "-" for ch in (raw or "").strip().lower()
    ).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    if not slug:
        raise ValueError(f"cannot derive a service id from {raw!r}")
    return slug


def normalize_domain(raw: str) -> str:
    """Reduce a URL or host to the bare lowercase host used for matching.

    ``https://www.GitHub.com/login?x=1`` and ``github.com`` both become
    ``github.com`` — the user should not have to think about which form to type.
    """
    text = (raw or "").strip().lower()
    if not text:
        return ""
    if "//" not in text:
        # urlsplit only recognises a host when a scheme is present.
        text = f"//{text}"
    host = urlsplit(text).hostname or ""
    return host[4:] if host.startswith("www.") else host


@dataclass(frozen=True, slots=True)
class CredentialSummary:
    """Everything about a login EXCEPT its secrets.

    This is the shape the brain, the tools and the REST layer see. It exists as
    a separate type rather than as a "remember to strip the password" habit,
    because a habit fails silently exactly once and then the password is in a
    transcript forever.
    """

    service_id: str
    label: str
    domains: tuple[str, ...]
    username: str
    notes: str
    has_password: bool
    has_totp: bool
    status: LoginStatus
    created_at: datetime | None
    updated_at: datetime | None
    last_used_at: datetime | None
    owner: CredentialOwner = CredentialOwner.USER
    kind: str = DEFAULT_KIND
    #: Non-secret detail for this kind of connection — an address, a handle, a
    #: base URL. Safe to show and safe to hand the model, which is exactly why
    #: it is a separate field from ``secret_names``.
    fields: tuple[tuple[str, str], ...] = ()
    #: NAMES of the additional secrets held for this record, never their values.
    #: The model needs the names to reference them; the values travel only
    #: through :mod:`jarvis.logins.injection`.
    secret_names: tuple[str, ...] = ()

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "service_id": self.service_id,
            "label": self.label,
            "domains": list(self.domains),
            "username": self.username,
            "notes": self.notes,
            "has_password": self.has_password,
            "has_totp": self.has_totp,
            "status": self.status.value,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
            "last_used_at": _iso(self.last_used_at),
            "owner": self.owner.value,
            "kind": self.kind,
            "fields": dict(self.fields),
            "secret_names": list(self.secret_names),
        }


@dataclass(frozen=True, slots=True)
class Credential:
    """One stored login, secrets included.

    ``password`` and ``totp_secret`` carry ``repr=False``: a dataclass repr is
    the classic accidental-leak path, because it turns up in exception messages,
    debug logs and test failure output without anyone deciding to print it.
    """

    service_id: str
    label: str
    domains: tuple[str, ...]
    username: str
    password: str = field(repr=False, default="")
    notes: str = ""
    totp_secret: str | None = field(repr=False, default=None)
    status: LoginStatus = LoginStatus.UNKNOWN
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_used_at: datetime | None = None
    owner: CredentialOwner = CredentialOwner.USER
    kind: str = DEFAULT_KIND
    fields: tuple[tuple[str, str], ...] = ()
    secrets: tuple[tuple[str, str], ...] = field(repr=False, default=())

    def field_map(self) -> dict[str, str]:
        """Non-secret detail as a dict, for callers that prefer one."""
        return dict(self.fields)

    def secret_map(self) -> dict[str, str]:
        """Additional secret values. Never hand the result to a model."""
        return dict(self.secrets)

    def summary(self) -> CredentialSummary:
        return CredentialSummary(
            service_id=self.service_id,
            label=self.label,
            domains=self.domains,
            username=self.username,
            notes=self.notes,
            has_password=bool(self.password),
            has_totp=bool(self.totp_secret),
            status=self.status,
            created_at=self.created_at,
            updated_at=self.updated_at,
            last_used_at=self.last_used_at,
            owner=self.owner,
            kind=self.kind,
            fields=self.fields,
            # Names only. Building this from the keys is what keeps a value from
            # ever reaching the summary type by accident.
            secret_names=tuple(name for name, _ in self.secrets),
        )

    def matches(self, url_or_host: str) -> bool:
        """Does this record cover the given URL or host?

        Matches the host itself and any subdomain of it, so one entry for
        ``google.com`` also answers for ``accounts.google.com``.
        """
        host = normalize_domain(url_or_host)
        if not host:
            return False
        return any(
            host == domain or host.endswith(f".{domain}")
            for domain in self.domains
            if domain
        )

    def to_json(self) -> str:
        payload = {
            "service_id": self.service_id,
            "label": self.label,
            "domains": list(self.domains),
            "username": self.username,
            "password": self.password,
            "notes": self.notes,
            "totp_secret": self.totp_secret,
            "status": self.status.value,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
            "last_used_at": _iso(self.last_used_at),
            "owner": self.owner.value,
            "kind": self.kind,
            "fields": dict(self.fields),
            "secrets": dict(self.secrets),
        }
        return json.dumps(payload, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> Credential:
        data = json.loads(raw)
        status_raw = data.get("status")
        try:
            status = LoginStatus(status_raw)
        except ValueError:
            # An unknown status from a newer build is not a reason to strand a
            # record the user can still log in with.
            status = LoginStatus.UNKNOWN
        return cls(
            service_id=str(data["service_id"]),
            label=str(data.get("label") or data["service_id"]),
            domains=tuple(str(d) for d in (data.get("domains") or ())),
            username=str(data.get("username") or ""),
            password=str(data.get("password") or ""),
            notes=str(data.get("notes") or ""),
            totp_secret=data.get("totp_secret") or None,
            status=status,
            created_at=_parse_dt(data.get("created_at")),
            updated_at=_parse_dt(data.get("updated_at")),
            last_used_at=_parse_dt(data.get("last_used_at")),
            owner=_parse_owner(data.get("owner")),
            kind=str(data.get("kind") or DEFAULT_KIND),
            fields=as_pairs(data.get("fields")),
            secrets=as_pairs(data.get("secrets")),
        )


def _record_key(service_id: str) -> str:
    if not service_id or set(service_id) - _ID_ALLOWED:
        raise ValueError(f"invalid service_id: {service_id!r}")
    return f"{_RECORD_PREFIX}{service_id}"


class CredentialStore:
    """Read/write access to the login vault.

    The backend is injectable so tests never touch the real keychain; the
    default wraps the shared keyring helpers in the chunking layer.
    """

    def __init__(self, backend: TokenBackend | None = None) -> None:
        self._backend: TokenBackend = (
            backend if backend is not None else ChunkedBackend(KeyringBackend())
        )

    # -- index -----------------------------------------------------------

    def _read_index(self) -> list[str]:
        raw = self._backend.get(_INDEX_KEY)
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except ValueError:
            log.warning("credential index is corrupted — treating the vault as empty")
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item) for item in parsed if isinstance(item, str)]

    def _write_index(self, ids: list[str]) -> None:
        self._backend.set(_INDEX_KEY, json.dumps(sorted(set(ids)), separators=(",", ":")))

    # -- reads -----------------------------------------------------------

    def load(self, service_id: str) -> Credential | None:
        """Full record including secrets. Callers must not hand this to a model."""
        raw = self._backend.get(_record_key(service_id))
        if raw is None:
            return None
        try:
            return Credential.from_json(raw)
        except (ValueError, KeyError) as exc:
            raise RuntimeError(
                f"stored login for {service_id!r} is unreadable: {exc}"
            ) from exc

    def list_summaries(
        self, owner: CredentialOwner | None = None
    ) -> list[CredentialSummary]:
        """Every stored login, secrets stripped, sorted by label.

        Self-heals the index when it names a record that is no longer there, so
        an interrupted delete cannot make the whole section fail to load.

        Args:
            owner: Restrict to one side of the ledger. ``None`` lists both,
                which is what the Passwords section wants; a caller deciding
                which identity to act with passes the one it means. The
                self-healing sweep runs over the whole index either way, so a
                filtered call still repairs a stale entry it did not return.
        """
        ids = self._read_index()
        summaries: list[CredentialSummary] = []
        missing: list[str] = []
        for service_id in ids:
            try:
                cred = self.load(service_id)
            except RuntimeError:
                log.warning("skipping unreadable login record %r", service_id)
                continue
            if cred is None:
                missing.append(service_id)
                continue
            if owner is not None and cred.owner is not owner:
                continue
            summaries.append(cred.summary())
        if missing:
            log.info("dropping %d stale credential index entries", len(missing))
            self._write_index([i for i in ids if i not in set(missing)])
        return sorted(summaries, key=lambda s: s.label.lower())

    def find_for_url(
        self, url_or_host: str, owner: CredentialOwner | None = None
    ) -> Credential | None:
        """The record covering this URL, or None.

        Prefers the most specific match, so an entry for
        ``accounts.google.com`` wins over one for ``google.com``.

        Args:
            owner: Which identity the caller intends to act as. Passing one is
                a hard filter, never a preference: a caller that asked for the
                assistant's own account and got the user's would sign the user
                in to a site the assistant was meant to enter under its own
                name — silently, and with the user's session left behind in the
                browser profile. Absent means "any" — and there the user's own
                record outranks an agent-owned one covering the same site,
                before domain specificity. A caller that names no owner is
                presumed to act on the user's behalf (that is the assistant's
                default posture), so the pick must be deterministic and err
                towards the account the user recognises, never towards
                whichever record happens to sit earlier in the index. The
                assistant's own account is still found when it is the only
                match, and is chosen deliberately via ``owner=AGENT``.
        """
        host = normalize_domain(url_or_host)
        if not host:
            return None
        best: Credential | None = None
        # (owner rank, domain-match length). With an owner filter set the rank
        # is constant and the key degrades to plain specificity.
        best_key = (-1, -1)
        for service_id in self._read_index():
            try:
                cred = self.load(service_id)
            except RuntimeError:
                continue
            if cred is None or not cred.matches(host):
                continue
            if owner is not None and cred.owner is not owner:
                continue
            longest = max(
                (len(d) for d in cred.domains if host == d or host.endswith(f".{d}")),
                default=0,
            )
            key = (1 if cred.owner is CredentialOwner.USER else 0, longest)
            if key > best_key:
                best, best_key = cred, key
        return best

    # -- writes ----------------------------------------------------------

    def save(self, credential: Credential) -> Credential:
        """Create or replace a record. Returns the stored version with stamps set."""
        service_id = normalize_service_id(credential.service_id)
        existing = self.load(service_id)
        stored = replace(
            credential,
            service_id=service_id,
            domains=tuple(
                dict.fromkeys(
                    normalize_domain(d) for d in credential.domains if normalize_domain(d)
                )
            ),
            kind=(credential.kind or DEFAULT_KIND).strip() or DEFAULT_KIND,
            # Normalised on the way in, so a caller that passed a plain dict
            # cannot leave an unhashable value inside a frozen record.
            fields=as_pairs(credential.fields),
            secrets=as_pairs(credential.secrets),
            created_at=(existing.created_at if existing else None) or _now(),
            updated_at=_now(),
        )
        # Record first, index second — see the module docstring on ordering.
        self._backend.set(_record_key(service_id), stored.to_json())
        self._write_index([*self._read_index(), service_id])
        return stored

    def mark_used(self, service_id: str, status: LoginStatus) -> None:
        """Record the outcome of a login attempt. Never invents a result."""
        cred = self.load(service_id)
        if cred is None:
            return
        self._backend.set(
            _record_key(cred.service_id),
            replace(cred, status=status, last_used_at=_now()).to_json(),
        )

    def delete(self, service_id: str) -> bool:
        """Remove a record and its index entry. Idempotent; verified deletion."""
        service_id = normalize_service_id(service_id)
        ids = self._read_index()
        if service_id in ids:
            # Index first here: a half-finished delete must not leave the entry
            # visible in a section that claims it removed it.
            self._write_index([i for i in ids if i != service_id])
        key = _record_key(service_id)
        if self._backend.get(key) is None:
            return False
        self._backend.delete(key)
        if self._backend.get(key) is not None:
            raise RuntimeError(f"could not delete the stored login for {service_id!r}")
        return True


def default_store() -> CredentialStore:
    """The vault handle every caller should use when it has no store of its own.

    A single seam on purpose: callers reach it by module attribute rather than
    by importing the name, so a test replaces exactly one thing and no code path
    can accidentally open the real keychain behind its back.
    """
    return CredentialStore()


__all__ = [
    "DEFAULT_KIND",
    "Credential",
    "CredentialOwner",
    "CredentialStore",
    "CredentialSummary",
    "LoginStatus",
    "default_store",
    "normalize_domain",
    "normalize_service_id",
]
