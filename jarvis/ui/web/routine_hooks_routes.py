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
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    signature = request.headers.get("X-Hub-Signature-256", "")
    bearer = scheme.lower() == "bearer" and bool(token) and len(token) <= 256
    if not bearer and (not signature.startswith("sha256=") or len(signature) != 71):
        raise HTTPException(401, "A routine webhook token or SHA256 signature is required")
    _, scheduler, row = await _webhook(request, task_id)
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
    _, _, row = await _webhook(request, task_id)
    try:
        token = await asyncio.to_thread(connection_token, row, rotate=True)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return {"path": f"/api/tasks/hooks/{task_id}", "token": token, "method": "POST"}
