"""REST API for the Passwords section — stored accounts, and whose they are.

Endpoints (mounted by the WebServer in ``_build_app()``):

    GET    /api/logins                      → {"logins": [summary, ...]}
    POST   /api/logins                      → create or replace one; 201
    PATCH  /api/logins/{service_id}         → partial edit; 404 if unknown
    DELETE /api/logins/{service_id}         → remove; {"removed": bool}
    POST   /api/logins/{service_id}/reveal  → the password, for an explicit click
    GET    /api/identity                    → the assistant's own contact card
    PUT    /api/identity                    → replace it wholesale
    DELETE /api/identity                    → clear it

A record names an owner: the user's own account, or one the assistant holds in
its own name. Listing accepts ``?owner=`` so the section can show the two side
by side without a second endpoint, and the identity routes hold the details the
assistant's own accounts are registered under — the address it types into a
signup form, the number an SMS code arrives at.

Every response EXCEPT ``/reveal`` is built from ``CredentialSummary``, which has
no password field at all. That is deliberate: stripping a secret by remembering
to strip it fails silently exactly once, and then the value is in a log forever.
Here the shape simply cannot carry one.

``/reveal`` is the single exception and is shaped to stay one:

* It is a POST, not a GET. A secret must never sit in a URL, where it lands in
  access logs, in the browser's history and in any proxy in between.
* It is flagged ``x-jarvis-dangerous``, so the generated CLI treats it as a
  destructive-class command and confirms before running it.
* It exists only because a password manager the user cannot read their own
  password out of is not a password manager. It is for the human in the
  desktop section — the model reaches secrets through
  ``jarvis.logins.injection`` and never through HTTP.

No Brain dependency, so the section works headless and with MockBrain.
"""

from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from jarvis.logins import identity as identity_module
from jarvis.logins import store as store_module
from jarvis.logins.identity import AgentIdentity, IdentityStore
from jarvis.logins.store import Credential, CredentialOwner, CredentialStore

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/logins", tags=["logins"])
identity_router = APIRouter(prefix="/api/identity", tags=["logins"])

# Serialises read-modify-write across per-request store instances. The endpoints
# are sync ``def`` (FastAPI threadpool), so a threading.Lock is the right shape.
_LOCK = threading.Lock()

# Generous abuse guards, not usability limits. A markdown note is meant to hold
# a few paragraphs of login instructions; the keychain has to store all of it.
_MAX_NOTE_LEN = 16_000
_MAX_FIELD_LEN = 512
_MAX_DOMAINS = 25


def _store() -> CredentialStore:
    # Per request, resolved through the same seam every other caller uses, so a
    # test sandbox swaps one name and the routes follow.
    return store_module.default_store()


def _owner(raw: str | None, *, default: CredentialOwner) -> CredentialOwner:
    """Parse an owner from the wire, rejecting anything unrecognised.

    Deliberately strict, unlike the store's forgiving read of an old record: a
    typo here comes from a live caller that believes it is filing an account
    under one identity, and silently defaulting it to the other is exactly the
    mix-up this whole feature exists to prevent.
    """
    if raw is None or not str(raw).strip():
        return default
    try:
        return CredentialOwner(str(raw).strip().lower())
    except ValueError as exc:
        raise HTTPException(
            400, "owner must be 'user' (your own account) or 'agent' (the assistant's)"
        ) from exc


class LoginCreate(BaseModel):
    label: str
    domains: list[str] = []
    username: str = ""
    password: str = ""
    notes: str = ""
    totp_secret: str | None = None
    service_id: str | None = None
    #: Defaults to the user's own account — the meaning every record had before
    #: this field existed, so an older client keeps working unchanged.
    owner: str = CredentialOwner.USER.value
    kind: str = store_module.DEFAULT_KIND
    #: Non-secret detail for this kind of connection (an address, a handle).
    fields: dict[str, str] = {}
    #: Additional secret values beyond the password (an API token, an app
    #: password). Write-only: no response model can carry these back out.
    secrets: dict[str, str] = {}


class LoginUpdate(BaseModel):
    """Partial edit. ``None`` means "leave as it is" for every field.

    A password is therefore never cleared by accident: the section has to send
    an explicit empty string to blank one.
    """

    label: str | None = None
    domains: list[str] | None = None
    username: str | None = None
    password: str | None = None
    notes: str | None = None
    totp_secret: str | None = None
    owner: str | None = None
    kind: str | None = None
    fields: dict[str, str] | None = None
    secrets: dict[str, str] | None = None


class IdentityPayload(BaseModel):
    """The assistant's own contact card. Every field optional and replaceable.

    A whole-object PUT rather than a patch: this is one small card edited on one
    screen, and merging half-sent fields would make "clear my phone number"
    indistinguishable from "leave it alone".
    """

    display_name: str = ""
    email: str = ""
    phone: str = ""
    timezone: str = ""
    signature: str = ""
    handles: dict[str, str] = {}
    notes: str = ""


def _validate(
    *,
    label: str | None,
    domains: list[str] | None,
    username: str | None,
    notes: str | None,
) -> None:
    if label is not None and (not label.strip() or len(label) > _MAX_FIELD_LEN):
        raise HTTPException(400, "label must be between 1 and 512 characters")
    if domains is not None:
        if len(domains) > _MAX_DOMAINS:
            raise HTTPException(400, f"at most {_MAX_DOMAINS} domains per login")
        if any(len(d) > _MAX_FIELD_LEN for d in domains):
            raise HTTPException(400, "a domain is too long")
    if username is not None and len(username) > _MAX_FIELD_LEN:
        raise HTTPException(400, "username is too long")
    if notes is not None and len(notes) > _MAX_NOTE_LEN:
        raise HTTPException(400, f"notes must be at most {_MAX_NOTE_LEN} characters")


@router.get("", openapi_extra={"x-jarvis-readonly": True})
def list_logins(owner: str | None = None) -> dict[str, list[dict[str, object]]]:
    """Every stored login, without secrets.

    ``?owner=user`` or ``?owner=agent`` narrows to one side of the ledger;
    omitting it returns both, each summary carrying its own ``owner``.
    """
    selected = (
        None if owner is None or not owner.strip()
        else _owner(owner, default=CredentialOwner.USER)
    )
    try:
        summaries = _store().list_summaries(selected)
    except Exception as exc:  # noqa: BLE001 -- a locked keychain is not a 500
        log.warning("could not read the login vault: %r", exc)
        raise HTTPException(
            503,
            "The credential store could not be opened. On Linux this usually "
            "means no keyring service is running.",
        ) from exc
    return {"logins": [s.to_public_dict() for s in summaries]}


@router.post("", status_code=201)
def create_login(payload: LoginCreate) -> dict[str, object]:
    """Create or replace a login. The service id is derived from the label."""
    _validate(
        label=payload.label,
        domains=payload.domains,
        username=payload.username,
        notes=payload.notes,
    )
    # Parsed BEFORE the write, because the store's broad failure handler below
    # turns anything raised inside it into a 503 — and "you sent an owner I do
    # not recognise" is the caller's mistake, not the keychain's.
    owner = _owner(payload.owner, default=CredentialOwner.USER)
    with _LOCK:
        store = _store()
        try:
            stored = store.save(
                Credential(
                    service_id=payload.service_id or payload.label,
                    label=payload.label.strip(),
                    domains=tuple(payload.domains),
                    username=payload.username,
                    password=payload.password,
                    notes=payload.notes,
                    totp_secret=payload.totp_secret or None,
                    owner=owner,
                    kind=payload.kind,
                    fields=store_module.as_pairs(payload.fields),
                    secrets=store_module.as_pairs(payload.secrets),
                )
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            log.warning("could not save a login: %r", exc)
            raise HTTPException(503, "The credential store rejected the write.") from exc
    log.info("stored a login for %s", stored.service_id)
    return stored.summary().to_public_dict()


@router.patch("/{service_id}")
def update_login(service_id: str, payload: LoginUpdate) -> dict[str, object]:
    _validate(
        label=payload.label,
        domains=payload.domains,
        username=payload.username,
        notes=payload.notes,
    )
    with _LOCK:
        store = _store()
        existing = store.load(service_id)
        if existing is None:
            raise HTTPException(404, f"no stored login with id {service_id!r}")
        merged = Credential(
            service_id=existing.service_id,
            label=(payload.label or existing.label).strip(),
            domains=(
                tuple(payload.domains) if payload.domains is not None
                else existing.domains
            ),
            username=(
                payload.username if payload.username is not None else existing.username
            ),
            password=(
                payload.password if payload.password is not None else existing.password
            ),
            notes=(payload.notes if payload.notes is not None else existing.notes),
            totp_secret=(
                payload.totp_secret
                if payload.totp_secret is not None
                else existing.totp_secret
            ),
            # A changed password is unproven again — otherwise a silent edit
            # would keep the "already confirmed" status of the OLD value and the
            # next unattended login would run without asking.
            status=(
                existing.status
                if payload.password is None or payload.password == existing.password
                else store_module.LoginStatus.UNKNOWN
            ),
            created_at=existing.created_at,
            last_used_at=existing.last_used_at,
            owner=_owner(payload.owner, default=existing.owner),
            kind=payload.kind if payload.kind is not None else existing.kind,
            fields=(
                store_module.as_pairs(payload.fields)
                if payload.fields is not None
                else existing.fields
            ),
            secrets=(
                store_module.as_pairs(payload.secrets)
                if payload.secrets is not None
                else existing.secrets
            ),
        )
        stored = store.save(merged)
    return stored.summary().to_public_dict()


@router.delete("/{service_id}", openapi_extra={"x-jarvis-dangerous": True})
def delete_login(service_id: str) -> dict[str, bool]:
    """Remove a login. Idempotent: an absent one reports ``removed: false``."""
    with _LOCK:
        try:
            removed = _store().delete(service_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            # Verified deletion failed — say so rather than claiming success.
            raise HTTPException(503, str(exc)) from exc
    if removed:
        log.info("removed the stored login %s", service_id)
    return {"removed": removed}


@router.post("/{service_id}/reveal", openapi_extra={"x-jarvis-dangerous": True})
def reveal_login(service_id: str) -> dict[str, str | None]:
    """Return the stored password for an explicit click in the section.

    POST rather than GET on purpose — see the module docstring. This is the
    only endpoint in the application that returns a stored password, and it is
    for the human at the screen, never for the model.
    """
    credential = _store().load(service_id)
    if credential is None:
        raise HTTPException(404, f"no stored login with id {service_id!r}")
    log.info("password revealed in the UI for %s", service_id)
    return {
        "service_id": credential.service_id,
        "username": credential.username,
        "password": credential.password,
        "totp_secret": credential.totp_secret,
    }


def _identity_store() -> IdentityStore:
    return identity_module.default_identity_store()


@identity_router.get("", openapi_extra={"x-jarvis-readonly": True})
def get_identity() -> dict[str, object]:
    """The assistant's own contact card. Absent reads as an empty one.

    Not a 404 when unset: "the assistant has no identity yet" is a normal
    starting state that the section renders as an empty form, and making the
    client special-case a missing resource would buy nothing.
    """
    return _identity_store().load().to_public_dict()


@identity_router.put("")
def put_identity(payload: IdentityPayload) -> dict[str, object]:
    """Replace the card wholesale."""
    for name, value in (
        ("display_name", payload.display_name),
        ("email", payload.email),
        ("phone", payload.phone),
        ("timezone", payload.timezone),
        ("signature", payload.signature),
    ):
        if len(value) > _MAX_FIELD_LEN:
            raise HTTPException(400, f"{name} is too long")
    if len(payload.notes) > _MAX_NOTE_LEN:
        raise HTTPException(400, f"notes must be at most {_MAX_NOTE_LEN} characters")

    with _LOCK:
        try:
            stored = _identity_store().save(
                AgentIdentity(
                    display_name=payload.display_name,
                    email=payload.email,
                    phone=payload.phone,
                    timezone=payload.timezone,
                    signature=payload.signature,
                    handles=store_module.as_pairs(payload.handles),
                    notes=payload.notes,
                )
            )
        except Exception as exc:  # noqa: BLE001 -- a locked keychain is not a 500
            log.warning("could not save the agent identity: %r", exc)
            raise HTTPException(
                503, "The credential store rejected the write."
            ) from exc
    return stored.to_public_dict()


@identity_router.delete("", openapi_extra={"x-jarvis-dangerous": True})
def delete_identity() -> dict[str, bool]:
    """Clear the card. The assistant then has no identity of its own again."""
    with _LOCK:
        removed = _identity_store().clear()
    if removed:
        log.info("the agent identity was cleared")
    return {"removed": removed}


__all__ = ["identity_router", "router"]
