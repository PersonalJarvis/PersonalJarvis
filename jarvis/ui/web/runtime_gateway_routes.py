"""``/api/runtime-gateway/v1`` — the one model endpoint Hermes / OpenClaw
agents talk to (``jarvis.agent_runtimes.gateway``).

OpenAI wire shapes behind a per-agent Bearer token Jarvis minted:
``POST /chat/completions`` for every API-key and local provider,
``POST /responses`` for the ChatGPT subscription, ``GET /models``. Not part
of the Control API: no person or CLI calls it, so it stays out of the
OpenAPI schema.
"""

from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from jarvis.agent_runtimes import gateway

router = APIRouter(prefix=gateway.BASE_PATH, tags=["runtime-gateway"], include_in_schema=False)


def _grant(request: Request) -> gateway.Grant:
    scheme, _, token = (request.headers.get("authorization") or "").partition(" ")
    found = gateway.verify(token.strip()) if scheme.lower() == "bearer" else None
    if found is None:
        raise HTTPException(401, "Unknown runtime gateway token.")
    return found


def _error(exc: gateway.GatewayError) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": str(exc), "type": exc.code, "code": exc.code}},
        status_code=exc.status,
        headers={"Retry-After": str(math.ceil(exc.retry_after))}
        if exc.retry_after is not None
        else None,
    )


async def _body(request: Request) -> Any:
    try:
        return await request.json()
    except ValueError as exc:
        raise gateway.GatewayError("The request body is not JSON.") from exc


@router.post("/chat/completions")
async def runtime_gateway_chat(request: Request) -> Any:
    """One model call for a Hermes / OpenClaw agent, on its Jarvis provider."""
    grant = _grant(request)
    try:
        if grant.provider == gateway.SUBSCRIPTION_PROVIDER:
            raise gateway.GatewayError("This agent's subscription answers on /responses.")
        body = await _body(request)
        model, brain_request = gateway.chat_request(body)
        gateway.check_model(grant, model)
        # A runtime that sets no temperature gets the model's own default.
        temperature_given = gateway.temperature_given(body)
        if body.get("stream") is True:
            chunks = await gateway.open_chat_stream(
                grant, model, brain_request, temperature_given=temperature_given
            )
            return StreamingResponse(
                chunks, media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
            )
        return await gateway.complete_chat(
            grant, model, brain_request, temperature_given=temperature_given
        )
    except gateway.GatewayError as exc:
        return _error(exc)


@router.post("/responses")
async def runtime_gateway_responses(request: Request) -> Any:
    """One model turn for a Hermes / OpenClaw agent, on its ChatGPT subscription."""
    grant = _grant(request)
    try:
        if grant.provider != gateway.SUBSCRIPTION_PROVIDER:
            raise gateway.GatewayError("This agent's provider answers on /chat/completions.")
        body = await _body(request)
        args = gateway.request_args(body)
        gateway.check_model(grant, args["model"])
        if body.get("stream") is True:
            events = await gateway.open_response_stream(grant, args)
            return StreamingResponse(
                events,
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"},
            )
        return await gateway.complete_response(grant, args)
    except gateway.GatewayError as exc:
        return _error(exc)


@router.get("/models")
async def runtime_gateway_models(request: Request) -> Any:
    """The ChatGPT models the agent's account offers."""
    grant = _grant(request)
    try:
        return {"object": "list", "data": await gateway.list_models(grant)}
    except gateway.GatewayError as exc:
        return _error(exc)
