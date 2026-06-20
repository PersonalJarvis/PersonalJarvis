"""Admin surface — token issue/list/revoke + a usage report.

All endpoints are guarded by the ``KEYPROXY_ADMIN_KEY`` bearer
(``Authorization: Bearer <admin_key>``), compared in constant time. The router
is mounted by :func:`keyproxy.app.create_app` under ``/admin``.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .tokens import TokenStore
from .usage import UsageStore

# --------------------------------------------------------------------------
# Request / response models
# --------------------------------------------------------------------------


class IssueTokenRequest(BaseModel):
    label: str = Field(min_length=1, max_length=200)


class IssueTokenResponse(BaseModel):
    id: str
    label: str
    token: str  # plaintext — returned exactly once, never persisted


class TokenInfo(BaseModel):
    id: str
    label: str
    created_at: int
    revoked_at: int | None


class UsageReportRow(BaseModel):
    token_id: str | None
    calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    est_cost: float


# --------------------------------------------------------------------------
# Auth dependency
# --------------------------------------------------------------------------


def _require_admin(request: Request) -> None:
    admin_key = request.app.state.config.admin_key
    if not admin_key:
        # No admin key configured -> admin surface is disabled (fail closed).
        raise HTTPException(status_code=503, detail="admin disabled (no KEYPROXY_ADMIN_KEY)")
    header = request.headers.get("authorization", "")
    prefix = "bearer "
    presented = header[len(prefix):].strip() if header.lower().startswith(prefix) else ""
    if not presented or not hmac.compare_digest(presented, admin_key):
        raise HTTPException(status_code=401, detail="admin auth required")


# --------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------


def build_admin_router() -> APIRouter:
    router = APIRouter(prefix="/admin", dependencies=[Depends(_require_admin)])

    @router.post("/tokens", response_model=IssueTokenResponse)
    def issue_token(body: IssueTokenRequest, request: Request) -> IssueTokenResponse:
        tokens: TokenStore = request.app.state.tokens
        issued = tokens.issue(body.label)
        return IssueTokenResponse(
            id=issued.id, label=issued.label, token=issued.plaintext
        )

    @router.get("/tokens", response_model=list[TokenInfo])
    def list_tokens(request: Request) -> list[TokenInfo]:
        tokens: TokenStore = request.app.state.tokens
        return [
            TokenInfo(
                id=str(r["id"]),
                label=str(r["label"]),
                created_at=int(r["created_at"]),
                revoked_at=(
                    int(r["revoked_at"]) if r["revoked_at"] is not None else None
                ),
            )
            for r in tokens.list()
        ]

    @router.delete("/tokens/{token_id}")
    def revoke_token(token_id: str, request: Request) -> dict[str, object]:
        tokens: TokenStore = request.app.state.tokens
        ok = tokens.revoke(token_id)
        if not ok:
            raise HTTPException(status_code=404, detail="unknown token id")
        return {"revoked": True, "id": token_id}

    @router.get("/usage", response_model=list[UsageReportRow])
    def usage_report(
        request: Request,
        token_id: str | None = Query(default=None),
        since: int | None = Query(default=None),
        until: int | None = Query(default=None),
    ) -> list[UsageReportRow]:
        usage: UsageStore = request.app.state.usage
        rows = usage.report(token_id=token_id, since=since, until=until)
        return [
            UsageReportRow(
                token_id=r["token_id"],
                calls=int(r["calls"]),
                prompt_tokens=int(r["prompt_tokens"]),
                completion_tokens=int(r["completion_tokens"]),
                total_tokens=int(r["total_tokens"]),
                est_cost=float(r["est_cost"]),
            )
            for r in rows
        ]

    return router
