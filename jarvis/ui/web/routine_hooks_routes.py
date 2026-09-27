"""Routine webhook ingress, authenticated event publishing and connection UI."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from jarvis.tasks.hook_inbox import MAX_PAYLOAD_BYTES, encode_payload
from jarvis.tasks.schema import TriggerEventHook
from jarvis.tasks.webhook_auth import connection_token, verify_signature, verify_token

from .control_auth import require_control_key_or_session

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get(
    "/triggers/catalog",
    dependencies=[Depends(require_control_key_or_session)],
    summary="List Jarvis trigger families and available listener drivers",
)
async def trigger_catalog() -> dict[str, Any]:
    from jarvis.tasks.event_catalog import event_catalog
    from jarvis.tasks.source_catalog import catalog

    return {"triggers": catalog(), "internal_events": event_catalog()}


class InvokeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payload: dict[str, Any] = Field(default_factory=dict)


@router.post(
    "/{task_id}/invoke",
    status_code=202,
    dependencies=[Depends(require_control_key_or_session)],
    summary="Submit manual input or a routine form",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def invoke_source(task_id: UUID, body: InvokeBody, request: Request) -> dict[str, Any]:
    store, scheduler = _services(request)
    spec = await store.get_spec(str(task_id))
    if (
        spec is None
        or spec.trigger.type != "source"
        or spec.trigger.source.kind not in {"manual", "form"}
    ):
        raise HTTPException(409, "Use the entry point configured for this routine")
    try:
        encode_payload(body.payload)
        result = await scheduler.invoke_source(
            str(task_id), body.payload, _delivery_id(request), mode=spec.trigger.source.kind
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if result not in {"queued", "duplicate", "filtered"}:
        raise HTTPException(409, result)
    return {"status": result, "task_id": str(task_id)}


async def _source_row(request: Request, task_id: UUID):
    store, scheduler = _services(request)
    spec = await store.get_spec(str(task_id))
    row = await store.get(str(task_id))
    if spec is None or spec.trigger.type != "source" or row is None:
        raise HTTPException(404, "Trigger source not found")
    return store, scheduler, spec, row


@router.get(
    "/{task_id}/source-connection",
    dependencies=[Depends(require_control_key_or_session)],
    summary="Read listener status and credential presence without revealing secrets",
)
async def source_connection(task_id: UUID, request: Request) -> dict[str, Any]:
    from jarvis.tasks.source_catalog import available
    from jarvis.tasks.source_credentials import presence

    store, _, spec, row = await _source_row(request, task_id)
    state = await store.sources.read(str(task_id))
    return {
        "source": spec.trigger.source.model_dump(mode="json"),
        "installed": available(spec.trigger.source.kind),
        "credentials": await asyncio.to_thread(presence, row),
        "status": state["status"],
        "detail": state["detail"],
        "updated_ns": state.get("updated_ns"),
        "install": getattr(request.app.state, "source_install_status", {"status": "idle"}),
    }


class CredentialsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str | None = Field(default=None, max_length=4096)
    password: str | None = Field(default=None, max_length=16384)
    token: str | None = Field(default=None, max_length=16384)


@router.put(
    "/{task_id}/source-connection",
    dependencies=[Depends(require_control_key_or_session)],
    summary="Save source credentials securely and reconnect this source",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def save_source_connection(
    task_id: UUID, body: CredentialsBody, request: Request
) -> dict[str, Any]:
    from jarvis.tasks.source_credentials import save

    _, scheduler, spec, row = await _source_row(request, task_id)
    try:
        await asyncio.to_thread(save, row, body.model_dump(exclude_none=True))
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(503, str(exc)) from exc
    if row["state"] == "scheduled":
        scheduler.sources.start(spec)
    return {"saved": True}


@router.post(
    "/{task_id}/source-connection/install",
    status_code=202,
    dependencies=[Depends(require_control_key_or_session)],
    summary="Install this listener's optional protocol client",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def install_source_support(task_id: UUID, request: Request) -> dict[str, Any]:
    from jarvis.tasks.source_catalog import PACKAGES

    _, scheduler, spec, row = await _source_row(request, task_id)
    package = PACKAGES.get(spec.trigger.source.kind)
    if package is None:
        return {"status": "not_required"}
    running = getattr(request.app.state, "source_install_task", None)
    if running is not None and not running.done():
        return {"status": "running"}

    async def install():
        import importlib

        from jarvis.setup.dependencies import install_pip_package

        request.app.state.source_install_status = {"status": "running"}
        try:
            ok, _ = await asyncio.to_thread(install_pip_package, package, only_binary=True)
            importlib.invalidate_caches()
            request.app.state.source_install_status = {"status": "done" if ok else "error"}
            fresh = await scheduler._store.get(str(task_id))
            if ok and fresh and fresh["state"] == "scheduled":
                current = await scheduler._store.get_spec(str(task_id))
                if current and current.trigger.type == "source":
                    scheduler.sources.start(current)
        except Exception as exc:
            request.app.state.source_install_status = {
                "status": "error",
                "detail": type(exc).__name__,
            }

    request.app.state.source_install_task = asyncio.create_task(
        install(), name="trigger-client-install"
    )
    return {"status": "running"}


class ProviderConnectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    secret: str | None = Field(default=None, max_length=16384)
    oidc_audience: str | None = Field(default=None, max_length=2000)
    service_account: str | None = Field(default=None, max_length=320)


@router.put(
    "/{task_id}/webhook-connection",
    dependencies=[Depends(require_control_key_or_session)],
    summary="Configure an external provider's verification settings",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def save_provider_connection(
    task_id: UUID, body: ProviderConnectionBody, request: Request
) -> dict[str, Any]:
    from jarvis.core.config import set_secret
    from jarvis.tasks.schema import TriggerWebhook
    from jarvis.tasks.webhook_auth import _slot

    store, scheduler, row = await _webhook(request, task_id)
    spec = await store.get_spec(str(task_id))
    public = body.model_dump(exclude_none=True, exclude={"secret"})
    if public:
        try:
            updated = TriggerWebhook.model_validate({**spec.trigger.model_dump(), **public})
            await scheduler.update_task(str(task_id), spec.model_copy(update={"trigger": updated}))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    if body.secret is not None:
        if not await asyncio.to_thread(set_secret, _slot(row), body.secret):
            raise HTTPException(503, "Credential storage failed")
    return {"saved": True}


def _services(request: Request) -> tuple[Any, Any]:
    store = getattr(request.app.state, "task_store", None)
    scheduler = getattr(request.app.state, "task_scheduler", None)
    if store is None or scheduler is None:
        raise HTTPException(503, "The routine scheduler is unavailable")
    return store, scheduler


async def _webhook(request: Request, task_id: UUID) -> tuple[Any, Any, dict[str, Any]]:
    store, scheduler = _services(request)
    row = await store.get(str(task_id))
    if row is None or row["trigger_type"] != "webhook":
        raise HTTPException(404, "Webhook routine not found")
    return store, scheduler, row


async def _read_body(request: Request) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_PAYLOAD_BYTES:
            raise HTTPException(413, "Webhook payload exceeds 32 KiB")
        body.extend(chunk)
    return bytes(body)


def _decode_payload(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body)
        encode_payload(payload)
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(422, "A finite JSON object of at most 32 KiB is required") from None
    return payload


async def _payload(request: Request) -> dict[str, Any]:
    return _decode_payload(await _read_body(request))


def _delivery_id(request: Request) -> str:
    value = request.headers.get("Idempotency-Key") or str(uuid4())
    if len(value) > 128 or not value.strip() or any(ord(char) < 32 for char in value):
        raise HTTPException(
            422, "Idempotency-Key must be a printable identifier of at most 128 characters"
        )
    return value


@router.post(
    "/hooks/{task_id}",
    status_code=202,
    summary="Deliver a JSON payload to one webhook routine",
    openapi_extra={
        "x-jarvis-dangerous": True,
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": {"type": "object"}}},
        },
    },
)
async def receive_webhook(task_id: UUID, request: Request, response: Response) -> dict[str, Any]:
    """Requires a scoped Bearer token or a GitHub-compatible SHA256 signature."""
    store, scheduler, row = await _webhook(request, task_id)
    spec = await store.get_spec(str(task_id))
    provider = spec.trigger.provider
    if provider != "generic":
        from jarvis.tasks.external_auth import normalize_provider, verify_provider

        raw = await _read_body(request)
        if not await asyncio.to_thread(verify_provider, row, spec.trigger, raw, request.headers):
            raise HTTPException(
                401, "Invalid provider authentication or incomplete connection settings"
            )
        payload = _decode_payload(raw)
        if provider == "slack" and payload.get("type") == "url_verification":
            response.status_code = 200
            return {"challenge": str(payload.get("challenge", ""))}
        try:
            payload = normalize_provider(provider, payload)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        producer_id = payload.get("event_id") or payload.get("id") or payload.get("message_id")
        delivery = str(producer_id) if producer_id else "sha256:" + hashlib.sha256(raw).hexdigest()
    else:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        signature = request.headers.get("X-Hub-Signature-256", "")
        bearer = scheme.lower() == "bearer" and bool(token) and len(token) <= 256
        if not bearer and (not signature.startswith("sha256=") or len(signature) != 71):
            raise HTTPException(401, "A routine webhook token or SHA256 signature is required")
        if bearer:
            if not await asyncio.to_thread(verify_token, row, token):
                raise HTTPException(401, "Invalid routine webhook token")
            raw = await _read_body(request)
            delivery = _delivery_id(request)
        else:
            raw = await _read_body(request)
            if not await asyncio.to_thread(verify_signature, row, raw, signature):
                raise HTTPException(401, "Invalid webhook signature")
            # GitHub does not sign its delivery-id header. Dedup by signed body so
            # changing that header cannot replay an intercepted signed request.
            delivery = "sha256:" + hashlib.sha256(raw).hexdigest()
        payload = _decode_payload(raw)
    status = await scheduler.receive_hook(str(task_id), payload, delivery)
    if status in ("inactive", "exhausted", "id_conflict"):
        raise HTTPException(409, status)
    if status in ("cooldown", "rate_limited"):
        raise HTTPException(429, status, headers={"Retry-After": "60"})
    if status in ("not_found", "unavailable"):
        raise HTTPException(503, "The webhook routine cannot receive deliveries")
    response.headers["Cache-Control"] = "no-store"
    return {"status": status, "delivery_id": delivery, "task_id": str(task_id)}


class EventBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_name: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)


@router.post(
    "/events",
    status_code=202,
    dependencies=[Depends(require_control_key_or_session)],
    summary="Publish a named integration event to matching routines",
    openapi_extra={
        "x-jarvis-dangerous": True,
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": EventBody.model_json_schema()}},
        },
    },
)
async def publish_event(request: Request) -> dict[str, Any]:
    """Integration events do not impersonate internal system event classes."""
    _, scheduler = _services(request)
    raw = await _payload(request)
    try:
        body = EventBody.model_validate(raw)
        TriggerEventHook(event_name=body.event_name)
    except ValueError:
        raise HTTPException(422, "Use event_name and a JSON object payload") from None
    delivery = _delivery_id(request)
    statuses = await scheduler.emit_hook_event(body.event_name, body.payload, delivery)
    values = set(statuses.values())
    if values and not values.intersection({"queued", "duplicate"}):
        if values.intersection({"rate_limited", "cooldown"}):
            raise HTTPException(429, {"routines": statuses}, headers={"Retry-After": "60"})
        if values.intersection({"unavailable", "not_found"}):
            raise HTTPException(503, {"routines": statuses})
    return {
        "status": "published",
        "event_name": body.event_name,
        "delivery_id": delivery,
        "routines": statuses,
    }


@router.get(
    "/{task_id}/webhook-connection",
    dependencies=[Depends(require_control_key_or_session)],
    summary="Show this routine's webhook connection in the app",
)
async def get_webhook_connection(
    task_id: UUID, request: Request, response: Response
) -> dict[str, Any]:
    """The UI reveals credentials here; chat tools never return these credentials."""
    _, _, row = await _webhook(request, task_id)
    spec = await _services(request)[0].get_spec(str(task_id))
    if spec.trigger.provider not in {"generic", "github"}:
        from jarvis.core.config import get_secret
        from jarvis.tasks.webhook_auth import _slot

        configured = (
            bool(spec.trigger.oidc_audience and spec.trigger.service_account)
            if spec.trigger.provider == "gmail"
            else bool(await asyncio.to_thread(get_secret, _slot(row)))
        )
        response.headers["Cache-Control"] = "no-store"
        return {
            "path": f"/api/tasks/hooks/{task_id}",
            "token": "",
            "method": "POST",
            "provider": spec.trigger.provider,
            "configured": configured,
            "oidc_audience": spec.trigger.oidc_audience,
            "service_account": spec.trigger.service_account,
        }
    try:
        token = await asyncio.to_thread(connection_token, row)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return {"path": f"/api/tasks/hooks/{task_id}", "token": token, "method": "POST"}


@router.post(
    "/{task_id}/webhook-connection/rotate",
    dependencies=[Depends(require_control_key_or_session)],
    summary="Rotate this routine's webhook token and revoke the old one",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def rotate_webhook_connection(
    task_id: UUID, request: Request, response: Response
) -> dict[str, Any]:
    """Only this routine's token changes; already queued deliveries remain queued."""
    store, _, row = await _webhook(request, task_id)
    spec = await store.get_spec(str(task_id))
    if spec.trigger.provider not in {"generic", "github"}:
        raise HTTPException(409, "Update the verification settings supplied by your provider")
    try:
        token = await asyncio.to_thread(connection_token, row, rotate=True)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return {"path": f"/api/tasks/hooks/{task_id}", "token": token, "method": "POST"}
