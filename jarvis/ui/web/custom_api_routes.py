"""Manage user-owned REST integrations and run actions through the supervisor."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from jarvis.core.protocols import SupervisorToolRequest
from jarvis.core.runtime_refs import get_supervisor_tool_gateway
from jarvis.marketplace.custom_api import ApiDefinition, CustomApiStore

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
        f"api_{api_id}_{action_id}",
        body.arguments,
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
