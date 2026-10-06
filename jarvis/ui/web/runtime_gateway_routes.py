"""``/api/runtime-gateway/v1`` — the model endpoint Hermes / OpenClaw agents
on a ChatGPT subscription talk to (``jarvis.agent_runtimes.gateway``).

OpenAI Responses shape (``POST /responses``, ``GET /models``) behind a
per-agent Bearer token Jarvis minted. Not part of the Control API: no person
or CLI calls it, so it stays out of the OpenAPI schema.
"""

from __future__ import annotations

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
    )


@router.post("/responses")
async def runtime_gateway_responses(request: Request) -> Any:
    """One model turn for a Hermes / OpenClaw agent, on its ChatGPT subscription."""
    grant = _grant(request)
    try:
        body = await request.json()
    except ValueError:
        return _error(gateway.GatewayError("The request body is not JSON."))
    try:
        args = gateway.request_args(body)
        if body.get("stream") is True:
            return StreamingResponse(
                gateway.stream_response(grant, args),
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
