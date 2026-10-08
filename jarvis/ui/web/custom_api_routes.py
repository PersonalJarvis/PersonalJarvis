"""Manage user-owned REST integrations and run actions through the supervisor."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from jarvis.core.protocols import SupervisorToolRequest
from jarvis.core.runtime_refs import get_supervisor_tool_gateway
from jarvis.marketplace.custom_api import ApiDefinition, CustomApiStore
from jarvis.marketplace.custom_api_discovery import ApiDiscoveryError, normalized_name
from jarvis.marketplace.custom_api_openapi import OpenApiImportError

log = logging.getLogger(__name__)


class _PrivateValidationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                # FastAPI's default validation response repeats the invalid
                # input, including a credential nested in the submitted body.
                locations = [".".join(str(p) for p in e["loc"]) for e in exc.errors()]
                raise HTTPException(
                    422, "Invalid API configuration at " + ", ".join(locations)
                ) from None

        return private_handler


router = APIRouter(
    prefix="/api/custom-apis", tags=["custom-apis"], route_class=_PrivateValidationRoute
)


class SaveApiRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    definition: ApiDefinition
    credential: SecretStr | None = Field(default=None, max_length=8192)


class ApiStatus(BaseModel):
    definition: ApiDefinition
    has_credential: bool
    tools_ready: bool


class RunApiActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arguments: dict[str, Any] = Field(default_factory=dict)


class ConnectApiRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[a-f0-9]{32}$")
    name: str = Field(min_length=1, max_length=100)
    credential: SecretStr | None = Field(default=None, max_length=8192)


class ConnectionSummary(BaseModel):
    id: str
    name: str
    website: str
    brand_id: str
    logo_data: str
    categories: list[str]
    action_count: int
    omitted_operations: int
    enabled: bool
    has_credential: bool
    tools_ready: bool
    status: str


class ConnectionEnabledRequest(BaseModel):
    enabled: bool


def _registry(request: Request) -> Any:
    return getattr(request.app.state, "plugin_registry", None)


def _store(request: Request) -> CustomApiStore:
    injected = getattr(request.app.state, "custom_api_store", None)
    if injected is not None:
        return injected
    registry = _registry(request)
    return registry.custom_apis.store if registry is not None else CustomApiStore()


async def _status(request: Request, definition: ApiDefinition) -> ApiStatus:
    has_key = bool(await asyncio.to_thread(_store(request).credential, definition.id))
    registry = _registry(request)
    names = getattr(getattr(registry, "custom_apis", None), "tools", {})
    return ApiStatus(
        definition=definition,
        has_credential=has_key,
        tools_ready=any(name.startswith(f"api_{definition.id}_") for name in names),
    )


async def _refresh(request: Request) -> None:
    registry = _registry(request)
    if registry is not None:
        try:
            await registry.refresh_custom_apis()
        except Exception:  # noqa: BLE001 - persisted definition remains available for recovery
            log.warning("Custom API live refresh failed", exc_info=True)


async def _connection_summary(request: Request, definition: ApiDefinition) -> ConnectionSummary:
    status = await _status(request, definition)
    info = definition.connection
    return ConnectionSummary(
        id=definition.id,
        name=definition.name,
        website=info.website if info else definition.base_url,
        brand_id=info.brand_id if info else "",
        logo_data=info.logo_data if info else "",
        categories=info.categories if info else [],
        action_count=len(definition.actions),
        omitted_operations=info.omitted_operations if info else 0,
        enabled=definition.enabled,
        has_credential=status.has_credential,
        tools_ready=status.tools_ready,
        status=info.status if info else "configured",
    )


@router.get("/connections", response_model=list[ConnectionSummary])
async def list_api_connections(request: Request) -> list[ConnectionSummary]:
    """List connected services without downloading every operation schema."""
    definitions = await asyncio.to_thread(_store(request).list)
    return [await _connection_summary(request, definition) for definition in definitions]


@router.post("/connect", response_model=ConnectionSummary)
async def connect_api_by_name(body: ConnectApiRequest, request: Request) -> ConnectionSummary:
    """Add a service using its name and API key, or replace an existing key."""
    from jarvis.marketplace.custom_api_connect import verify_api_key

    store = _store(request)
    previous = await asyncio.to_thread(store.get, body.id)
    credential = body.credential.get_secret_value() if body.credential else None
    if credential is not None and (not credential.strip() or any(ord(c) < 32 for c in credential)):
        raise HTTPException(422, {"code": "invalid_key"})
    if previous is not None:
        if normalized_name(previous.name) != normalized_name(body.name):
            raise HTTPException(422, {"code": "connection_name_changed"})
        definition = previous
        if credential is None:
            return await _connection_summary(request, previous)
    else:
        if not credential:
            raise HTTPException(422, {"code": "key_required"})
        registry = _registry(request)
        discovery = getattr(request.app.state, "custom_api_discovery", None)
        if discovery is None:
            if registry is None:
                raise HTTPException(503, {"code": "not_ready"})
            discovery = registry.custom_apis.discovery()
        try:
            # The separate interface cannot receive the key, even accidentally.
            definition = await discovery.resolve(body.name)
            definition = definition.model_copy(update={"id": body.id})
        except ApiDiscoveryError as exc:
            raise HTTPException(422, {"code": exc.code, "suggestions": exc.suggestions}) from None
        except OpenApiImportError:
            log.info("Custom API description needs an unsupported authentication or schema feature")
            raise HTTPException(422, {"code": "unsupported_api"}) from None
        except TimeoutError:
            raise HTTPException(504, {"code": "discovery_timeout"}) from None
        except (ValueError, OSError, httpx.HTTPError):
            log.warning("Custom API documentation could not be resolved")
            raise HTTPException(503, {"code": "documentation_unavailable"}) from None
    try:
        verifier = getattr(request.app.state, "custom_api_verifier", verify_api_key)
        status = await verifier(definition, credential or "")
        if definition.connection is not None:
            definition.connection.status = status
        await asyncio.to_thread(
            store.save, definition, credential if definition.auth.mode != "none" else None
        )
    except ApiDiscoveryError as exc:
        raise HTTPException(422, {"code": exc.code}) from None
    except (ValueError, OSError, RuntimeError):
        log.warning("Custom API connection could not be saved")
        raise HTTPException(503, {"code": "storage_unavailable"}) from None
    await _refresh(request)
    return await _connection_summary(request, definition)


@router.patch("/connections/{api_id}", response_model=ConnectionSummary)
async def set_api_connection_enabled(
    api_id: str, body: ConnectionEnabledRequest, request: Request
) -> ConnectionSummary:
    """Pause or resume this service for Jarvis and its agents."""
    try:
        definition = await asyncio.to_thread(_store(request).get, api_id)
        if definition is None:
            raise HTTPException(404, "Connection not found")
        definition.enabled = body.enabled
        await asyncio.to_thread(_store(request).save, definition)
    except ValueError:
        raise HTTPException(422, {"code": "invalid_connection"}) from None
    await _refresh(request)
    return await _connection_summary(request, definition)


@router.get("/connections/{api_id}/actions")
async def list_api_connection_actions(
    api_id: str, request: Request, query: str = ""
) -> dict[str, Any]:
    """Show the capabilities of one service without executing an API call."""
    try:
        definition = await asyncio.to_thread(_store(request).get, api_id)
    except ValueError:
        raise HTTPException(422, "Invalid connection") from None
    if definition is None:
        raise HTTPException(404, "Connection not found")
    words = query.casefold().split()
    actions = [
        a
        for a in definition.actions
        if all(w in f"{a.description} {a.id}".casefold() for w in words)
    ]
    return {
        "total": len(actions),
        "actions": [
            {"id": a.id, "description": a.description, "risk_tier": a.risk_tier} for a in actions
        ],
    }


@router.get("", response_model=list[ApiStatus])
async def list_custom_apis(request: Request) -> list[ApiStatus]:
    """List API definitions and local readiness without calling providers."""
    definitions = await asyncio.to_thread(_store(request).list)
    return [await _status(request, item) for item in definitions]


@router.post("/validate", response_model=ApiDefinition)
def validate_custom_api(definition: ApiDefinition) -> ApiDefinition:
    """Validate and normalize an imported definition without installing it."""
    return definition


@router.put("/{api_id}", response_model=ApiStatus)
async def save_custom_api(api_id: str, body: SaveApiRequest, request: Request) -> ApiStatus:
    """Create or update an API and optionally replace its protected key."""
    if api_id != body.definition.id:
        raise HTTPException(422, "API ID does not match the definition")
    try:
        await asyncio.to_thread(
            _store(request).save,
            body.definition,
            body.credential.get_secret_value() if body.credential is not None else None,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except (OSError, RuntimeError):
        log.warning("Could not persist custom API %s", api_id)
        raise HTTPException(503, "API storage is unavailable; try again") from None
    await _refresh(request)
    return await _status(request, body.definition)


@router.delete("/{api_id}")
async def delete_custom_api(api_id: str, request: Request) -> dict[str, bool]:
    """Remove an API's definition, key and live tools."""
    try:
        await asyncio.to_thread(_store(request).delete, api_id)
    except ValueError:
        raise HTTPException(422, "Invalid API ID") from None
    except (OSError, RuntimeError):
        log.warning("Could not fully remove custom API %s", api_id)
        raise HTTPException(503, "Credential removal failed; retry removal") from None
    finally:
        await _refresh(request)
    return {"deleted": True}


@router.post("/{api_id}/actions/{action_id}/run")
async def run_custom_api_action(
    api_id: str,
    action_id: str,
    body: RunApiActionRequest,
    request: Request,
) -> dict[str, Any]:
    """Run a selected API action through the normal tool permission gate."""
    try:
        definition = await asyncio.to_thread(_store(request).get, api_id)
    except ValueError:
        raise HTTPException(422, "Invalid API ID") from None
    if definition is None or not definition.enabled:
        raise HTTPException(404, "API is missing or disabled")
    if action_id not in {a.id for a in definition.actions}:
        raise HTTPException(404, "Action not found")
    gateway = get_supervisor_tool_gateway()
    if gateway is None:
        raise HTTPException(503, "Jarvis tool execution is not ready yet")
    result = await gateway.execute(
        f"api_{api_id}_call" if definition.connection else f"api_{api_id}_{action_id}",
        {"action": action_id, "arguments": body.arguments}
        if definition.connection
        else body.arguments,
        SupervisorToolRequest(
            trace_id=uuid4(),
            origin="custom-api-ui",
            user_utterance=f"Run {action_id} on my {definition.name} API",
            rationale="The user selected this action in Custom APIs",
        ),
    )
    return asdict(result)


@router.get("/templates/elevenlabs", response_model=ApiDefinition)
def elevenlabs_api_template() -> ApiDefinition:
    """Get an editable ElevenLabs example; no provider call or connection."""
    # Provider references: https://elevenlabs.io/docs/api-reference/voices/search
    # and https://elevenlabs.io/docs/api-reference/text-to-speech/convert
    return ApiDefinition(
        name="ElevenLabs",
        description="List voices and generate speech audio",
        base_url="https://api.elevenlabs.io",
        enabled=False,
        auth={"mode": "header", "header_name": "xi-api-key"},
        actions=[
            {
                "id": "list_voices",
                "description": "List available voices and their IDs",
                "method": "GET",
                "path": "/v2/voices",
                "response": "json",
                "parameters": [{"name": "search"}, {"name": "next_page_token"}],
            },
            {
                "id": "create_speech",
                "description": "Create speech from text and save the audio file",
                "method": "POST",
                "path": "/v1/text-to-speech/{voice_id}",
                "response": "file",
                "parameters": [
                    {"name": "voice_id", "location": "path", "required": True},
                    {"name": "output_format"},
                ],
                "body_schema": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "minLength": 1},
                        "model_id": {"type": "string"},
                        "voice_settings": {"type": "object"},
                    },
                    "required": ["text"],
                    "additionalProperties": False,
                },
            },
        ],
    )
