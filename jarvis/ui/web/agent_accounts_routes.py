"""REST routes for switching between several subscriptions of a coding CLI.

A user with two Claude Max seats (or two ChatGPT/Codex plans) can register both
here and flip which one new terminals run on, without ever logging out. The
mechanism is deliberately thin — an account IS a config directory, and switching
picks the directory the next spawn points at — so everything below is the CRUD
around :mod:`jarvis.agent_accounts` plus one sign-in launcher.

Mount alongside the other routers::

    from .agent_accounts_routes import router as agent_accounts_router
    app.include_router(agent_accounts_router)

Nothing here returns a credential. The snapshots carry booleans, the display
email, and the tier — never a token, and never the contents of a config file.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from jarvis import agent_accounts
from jarvis.agent_accounts import MAX_LABEL_CHARS, AccountError, platforms
from jarvis.core.interactive_terminal import InteractiveTerminalUnavailable

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent-accounts", tags=["agent-accounts"])


class AccountCreateRequest(BaseModel):
    platform: str = Field(
        description=(
            "Which coding CLI this seat belongs to. The accepted values are "
            "whichever CLIs this build can switch logins for; GET this "
            "endpoint to see them."
        )
    )
    label: str = Field(
        max_length=MAX_LABEL_CHARS,
        description="Display name for this subscription, e.g. 'Work seat'.",
    )


class AccountRenameRequest(BaseModel):
    label: str = Field(
        max_length=MAX_LABEL_CHARS, description="New display name for the account."
    )


class ActiveRequest(BaseModel):
    platform: str = Field(
        description=(
            "Which coding CLI to switch. GET this endpoint for the accepted "
            "values on this build."
        )
    )
    account_id: str = Field(description="Id of the account new terminals should use.")


def _platform(value: str) -> str:
    known = platforms()
    if value not in known:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown platform {value!r}. Known: {', '.join(known)}.",
        )
    return value


def _collect() -> dict[str, Any]:
    """Every account of every platform, described, plus who is active."""
    return {
        "platforms": [
            {
                "platform": platform,
                "active_account": agent_accounts.active_account(platform).id,
                "accounts": [s.to_dict() for s in agent_accounts.snapshots(platform)],
            }
            for platform in platforms()
        ]
    }


@router.get("", summary="Stored subscriptions per coding CLI")
async def list_accounts() -> dict[str, Any]:
    """Every registered subscription and which one new terminals will use.

    Reading a handful of small files per account, off the event loop so a slow
    or spun-down drive cannot stall the rest of the server.
    """
    return await asyncio.to_thread(_collect)


@router.post("", summary="Add another subscription")
async def create_account(req: AccountCreateRequest) -> dict[str, Any]:
    """Register a subscription and mint its (still empty) config directory.

    Adding does NOT sign in — that is a separate, deliberate step, because a
    sign-in opens a browser window and must never be a side effect of adding a
    row to a list. Until it happens the account reports itself as not signed in,
    which is exactly true.
    """
    platform = _platform(req.platform)
    try:
        account = await asyncio.to_thread(
            agent_accounts.create_account, platform, req.label
        )
    except AccountError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    snapshot = await asyncio.to_thread(agent_accounts.describe, account)
    return {"ok": True, "account": snapshot.to_dict(), **await asyncio.to_thread(_collect)}


@router.put("/active", summary="Switch which subscription new terminals use")
async def set_active(req: ActiveRequest) -> dict[str, Any]:
    """Point new terminals of one CLI at another subscription.

    Running panes are untouched on purpose: each carries the account it was
    opened with, so this can never move an agent mid-conversation onto a plan
    whose history does not contain that conversation.
    """
    platform = _platform(req.platform)
    try:
        account = await asyncio.to_thread(
            agent_accounts.set_active, platform, req.account_id
        )
    except AccountError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "ok": True,
        "active_account": account.id,
        "message": f"New {platform} terminals will use {account.label}.",
        **await asyncio.to_thread(_collect),
    }


@router.post("/{account_id}/login", summary="Sign in to one subscription")
async def login(account_id: str) -> dict[str, Any]:
    """Run the CLI's own sign-in, pointed at this account's directory.

    409 when the CLI is missing or the host has no terminal to show — both are
    capability answers the user can act on, not failures to hide.
    """
    account = await asyncio.to_thread(agent_accounts.resolve, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="That account no longer exists.")
    try:
        await asyncio.to_thread(agent_accounts.start_login, account)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InteractiveTerminalUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AccountError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"The sign-in could not be started: {type(exc).__name__}: {exc}",
        ) from exc
    return {
        "ok": True,
        "message": (
            f"Sign-in started for {account.label} — finish it in the window that "
            "opened. If the browser signs you straight in without ever showing a "
            "code, it reused the account you are already signed in with at "
            "claude.com: sign out there (or use a private window) and start this "
            "sign-in again, or both entries end up on the same subscription."
        ),
    }


@router.patch("/{account_id}", summary="Rename a subscription")
async def rename(account_id: str, req: AccountRenameRequest) -> dict[str, Any]:
    """Give an added subscription a different display name."""
    try:
        account = await asyncio.to_thread(
            agent_accounts.rename_account, account_id, req.label
        )
    except AccountError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "account": account.to_dict(), **await asyncio.to_thread(_collect)}


@router.delete(
    "/{account_id}",
    summary="Remove a subscription",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def delete(account_id: str, remove_files: bool = False) -> dict[str, Any]:
    """Forget an added subscription; optionally erase its stored login too.

    ``remove_files`` defaults to false because forgetting is reversible and
    erasing a login is not. The default login can never be removed — it is the
    CLI's own, and this feature does not get to take it away.
    """
    try:
        account = await asyncio.to_thread(
            agent_accounts.delete_account, account_id, remove_files=remove_files
        )
    except AccountError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "ok": True,
        "message": f"{account.label} was removed.",
        **await asyncio.to_thread(_collect),
    }
