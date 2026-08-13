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
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from jarvis import agent_accounts, agent_login_flow
from jarvis.agent_accounts import MAX_LABEL_CHARS, AccountError, platforms
from jarvis.agent_login_flow import GuidedLoginUnavailable
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
    label: str = Field(max_length=MAX_LABEL_CHARS, description="New display name for the account.")


class ActiveRequest(BaseModel):
    platform: str = Field(
        description=(
            "Which coding CLI to switch. GET this endpoint for the accepted values on this build."
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


def _display_name(platform: str) -> str:
    """What this CLI is called on screen, from the one registry that knows."""
    from jarvis.workspace.agents import get_agent

    entry = get_agent(platform)
    return entry.display_name if entry is not None else platform


def _collect() -> dict[str, Any]:
    """Every account of every platform, described, plus who is active."""
    return {
        "platforms": [
            {
                "platform": platform,
                # Sent so the UI renders whatever platforms this build has,
                # rather than keeping its own id-to-label map — a second list of
                # product names is a second place for a new CLI to be missing,
                # and the way it goes missing is invisible: the backend answers
                # correctly and the section simply is not drawn.
                "display_name": _display_name(platform),
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


def _usage(refresh: bool) -> dict[str, Any]:
    """Plan usage for every registered account, keyed by account id."""
    from jarvis import agent_usage

    accounts = agent_accounts.all_accounts()
    readings = agent_usage.collect(accounts, refresh=refresh)
    return {
        "accounts": [readings[a.id].to_dict() for a in accounts if a.id in readings],
        # Told to the UI rather than agreed by convention, so the poll interval
        # follows the server's cache instead of a second number that has to be
        # kept in step with it by hand.
        "ttl_seconds": agent_usage.USAGE_TTL_S,
        "generated_at": time.time(),
    }


@router.get("/usage", summary="Plan usage of every connected subscription")
async def usage(refresh: bool = False) -> dict[str, Any]:
    """How much of each seat's plan is already spent, and when it resets.

    One reading per account: the rolling short window, the weekly one, and any
    per-model budget the provider scopes separately. Each reading says whether
    it came from the provider just now (``source: "live"``) or from what the CLI
    last wrote to disk (``source: "cached"``, with ``as_of``) — a stale weekly
    figure presented as a live one is the one failure this endpoint must not
    have, because it is the number a user picks a subscription on.

    ``refresh=true`` bypasses the short server-side cache. Off the event loop:
    every account is a network round trip, and they run in parallel.
    """
    return await asyncio.to_thread(_usage, refresh)


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
        account = await asyncio.to_thread(agent_accounts.create_account, platform, req.label)
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
        account = await asyncio.to_thread(agent_accounts.set_active, platform, req.account_id)
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


class LoginFlowCodeRequest(BaseModel):
    code: str = Field(
        max_length=4096,
        description="The code the browser showed after approving the sign-in.",
    )


def _flow_or_404(state: dict[str, Any] | None) -> dict[str, Any]:
    if state is None:
        raise HTTPException(
            status_code=404,
            detail="That sign-in is no longer running — start it again.",
        )
    return {"ok": True, "flow": state}


@router.post(
    "/{account_id}/login-flow",
    summary="Start an in-app sign-in for one subscription",
)
async def start_login_flow(account_id: str) -> dict[str, Any]:
    """Run the CLI's own sign-in hidden in-app, surfaced as URL + code field.

    The browser link is returned as data so the user can open it in ANY
    browser profile or private window — the default browser is usually signed
    in as the wrong account when a second subscription is being added. 409
    when the CLI is missing or the host has no PTY capability; the external
    terminal-window sign-in remains the stated fallback.
    """
    account = await asyncio.to_thread(agent_accounts.resolve, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="That account no longer exists.")
    try:
        state = await asyncio.to_thread(agent_login_flow.start_flow, account)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GuidedLoginUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AccountError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "flow": state}


@router.get(
    "/login-flow/{flow_id}",
    summary="State of a running in-app sign-in",
)
async def get_login_flow(flow_id: str) -> dict[str, Any]:
    """Polled by the sign-in dialog: status, the sign-in URL, and what the CLI
    is currently showing. Never carries a credential."""
    return _flow_or_404(await asyncio.to_thread(agent_login_flow.flow_state, flow_id))


@router.post(
    "/login-flow/{flow_id}/code",
    summary="Hand the pasted code to a running in-app sign-in",
)
async def submit_login_flow_code(flow_id: str, req: LoginFlowCodeRequest) -> dict[str, Any]:
    """The code goes straight into the CLI's stdin, exactly as typing it would."""
    try:
        state = await asyncio.to_thread(agent_login_flow.submit_code, flow_id, req.code)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _flow_or_404(state)


@router.delete(
    "/login-flow/{flow_id}",
    summary="Cancel a running in-app sign-in",
)
async def cancel_login_flow(flow_id: str) -> dict[str, Any]:
    return _flow_or_404(await asyncio.to_thread(agent_login_flow.cancel_flow, flow_id))


@router.patch("/{account_id}", summary="Rename a subscription")
async def rename(account_id: str, req: AccountRenameRequest) -> dict[str, Any]:
    """Give an added subscription a different display name."""
    try:
        account = await asyncio.to_thread(agent_accounts.rename_account, account_id, req.label)
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
