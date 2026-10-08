"""Account-scoped earned resets; never start a model turn or buy usage.

Codex's documented app-server protocol supplies inventory and an idempotent
redemption operation. Other providers remain explicit browser handoffs until
their subscription transport exposes a verified reset contract.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from typing import Any

from jarvis.agent_accounts import AgentAccount

log = logging.getLogger(__name__)
TIMEOUT_S = 25
_OUTCOMES = {"reset", "alreadyRedeemed", "nothingToReset", "noCredit"}
_MANAGE_URLS = {
    "codex": "https://chatgpt.com/codex/settings/usage",
    "claude": "https://claude.ai/settings/usage",
    "grok-build": "https://grok.com/",
}


class ResetError(ValueError):
    """A safe, displayable failure; provider error bodies never leave here."""

    def __init__(self, message: str, code: str = "uncertain") -> None:
        super().__init__(message)
        self.code = code


def _empty(account: AgentAccount, status: str) -> dict[str, Any]:
    return {
        "account_id": account.id,
        "status": status,
        "available_count": None,
        "credits": None,
        "can_redeem": False,
        "account_key": None,
        "manage_url": _MANAGE_URLS.get(account.platform),
        "as_of": time.time(),
        "usage": None,
    }


def _identity(account: AgentAccount, remote: dict[str, Any]) -> str:
    from jarvis.agent_usage import _read_json_file

    auth = _read_json_file(account.config_dir / "auth.json") or {}
    tokens = auth.get("tokens") or {}
    ref = tokens.get("account_id") if isinstance(tokens, dict) else None
    material = [str(account.config_dir.resolve()), ref, remote.get("email"), remote.get("type")]
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def _snapshot(account: AgentAccount, payload: dict[str, Any], key: str) -> dict[str, Any]:
    from jarvis.agent_usage import AccountUsage, _codex_rate_limit_windows, _iso_from_epoch

    result = _empty(account, "unavailable")
    result["account_key"] = key
    summary = payload.get("rateLimitResetCredits")
    if isinstance(summary, dict):
        count = summary.get("availableCount")
        if type(count) is int and count >= 0:
            result.update(status="ok", available_count=count, can_redeem=count > 0)
            rows = summary.get("credits")
            if isinstance(rows, list):
                result["credits"] = []
                for row in rows:
                    if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                        continue
                    expiry = row.get("expiresAt")
                    expiry_valid = expiry is None or (type(expiry) is int and expiry > time.time())
                    result["credits"].append(
                        {
                            "id": row["id"],
                            "title": row.get("title")
                            if isinstance(row.get("title"), str)
                            else None,
                            "description": row.get("description")
                            if isinstance(row.get("description"), str)
                            else None,
                            "expires_at": _iso_from_epoch(expiry),
                            "redeemable": bool(row["id"])
                            and row.get("status") == "available"
                            and row.get("resetType") == "codexRateLimits"
                            and expiry_valid,
                        }
                    )
                # Empty/unknown detail rows are not permission to consume an
                # unspecified credit. Count-only responses have their own path.
                result["can_redeem"] = count > 0 and any(
                    row["redeemable"] for row in result["credits"]
                )
    buckets = payload.get("rateLimitsByLimitId")
    limits = buckets.get("codex") if isinstance(buckets, dict) else None
    if not isinstance(limits, dict):
        limits = payload.get("rateLimits")
    if isinstance(limits, dict):
        normalized = {"limit_id": limits.get("limitId")}
        for slot in ("primary", "secondary"):
            window = limits.get(slot)
            if isinstance(window, dict):
                normalized[slot] = {
                    "used_percent": window.get("usedPercent"),
                    "window_minutes": window.get("windowDurationMins"),
                    "resets_at": window.get("resetsAt"),
                }
        windows = _codex_rate_limit_windows(normalized)
        if windows:
            result["usage"] = AccountUsage(
                account.id,
                account.platform,
                "ok",
                windows,
                as_of=result["as_of"],
                plan=limits.get("planType"),
            ).to_dict()
    return result


async def _codex(
    account: AgentAccount,
    *,
    attempt: str | None = None,
    credit_id: str | None = None,
    account_key: str | None = None,
) -> dict[str, Any]:
    from jarvis.agent_chat.native_control import GoalRpc
    from jarvis.agent_chat.runner_cli import codex_argv_prefix
    from jarvis.codex_app_server import _subscription_env_allowed

    env = {k: v for k, v in os.environ.items() if _subscription_env_allowed(k)}
    env["CODEX_HOME"] = str(account.config_dir)
    # File storage prevents a missing redirected login from falling through to
    # another account in a shared OS credential store. No thread is ever opened.
    argv = [
        *codex_argv_prefix(),
        "app-server",
        "-c",
        'model_provider="openai"',
        "-c",
        'cli_auth_credentials_store="file"',
    ]

    async def deny(*_: Any) -> str:
        return "deny"

    async with GoalRpc(argv, env, str(account.config_dir), deny) as rpc:

        async def request(method: str, params: dict[str, Any]) -> dict[str, Any]:
            limit = TIMEOUT_S if method.endswith("/consume") else 8
            return await asyncio.wait_for(rpc.request(method, params), timeout=limit)

        await request(
            "initialize",
            {
                "clientInfo": {"name": "jarvis-subscription-resets", "version": "1"},
                "capabilities": {"experimentalApi": True},
            },
        )
        await rpc.send({"method": "initialized"})
        auth = await request("account/read", {"refreshToken": False})
        remote = auth.get("account")
        if not isinstance(remote, dict) or remote.get("type") != "chatgpt":
            raise ResetError(
                "Sign in to this Codex subscription before using a reset.", "signed_out"
            )
        key = _identity(account, remote)
        if attempt is not None:
            if account_key != key:
                raise ResetError(
                    "This subscription login changed. Refresh before using a reset.",
                    "account_changed",
                )
            params = {"idempotencyKey": attempt}
            if credit_id is not None:
                params["creditId"] = credit_id
            # Do not recheck inventory before replaying: a successful first
            # attempt may have consumed the last credit and lost its reply.
            answer = await request("account/rateLimitResetCredit/consume", params)
            outcome = answer.get("outcome")
            if not isinstance(outcome, str) or outcome not in _OUTCOMES:
                raise ResetError(
                    "The reset outcome is unknown. Retry the same request to check it."
                )
            # Preserve the confirmed receipt even if the refresh fails.
            try:
                payload = await request("account/rateLimits/read", {})
                snapshot = _snapshot(account, payload, key)
            except (ValueError, ConnectionError, TimeoutError):
                log.info("Reset completed; usage refresh unavailable for %s", account.id)
                snapshot = _empty(account, "unavailable")
            return {"outcome": outcome, "resets": snapshot}
        payload = await request("account/rateLimits/read", {})
        return _snapshot(account, payload, key)


async def read_resets(account: AgentAccount) -> dict[str, Any]:
    """Read only this login; missing support is distinct from zero credits."""
    if account.platform != "codex":
        return _empty(account, "browser" if account.platform == "claude" else "unsupported")
    try:
        return await _codex(account)
    except (OSError, ValueError, RuntimeError, ConnectionError, TimeoutError) as exc:
        log.info("Subscription reset inventory unavailable (%s)", type(exc).__name__)
        return _empty(account, "unavailable")


async def consume_reset(
    account: AgentAccount, *, attempt: str, credit_id: str | None, account_key: str
) -> dict[str, Any]:
    """One deliberate attempt. A retry MUST carry the same idempotency key."""
    if account.platform != "codex":
        raise ResetError(
            "This provider requires its own usage page to manage resets.", "unsupported"
        )
    try:
        result = await _codex(
            account, attempt=attempt, credit_id=credit_id, account_key=account_key
        )
    except ResetError:
        raise
    except (OSError, ValueError, RuntimeError, ConnectionError, TimeoutError) as exc:
        log.info("Subscription reset response unavailable (%s)", type(exc).__name__)
        raise ResetError(
            "No confirmed reset response. Retry the same request to check it."
        ) from exc
    from jarvis.agent_usage import clear_cache

    clear_cache()
    return result
