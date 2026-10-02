"""Selected Codex OAuth login for subscription voice and direct inference.

This is the Codex credential contract, not Sign in with ChatGPT token sharing.
No API credential, cookie, browser session, or alternate account is a fallback.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
import stat
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jarvis.core.http_pool import SyncHttpClientPool

# Public OAuth client identifier from openai/codex, login/src/auth/manager.rs.
_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_TOKEN_URL = "https://auth.openai.com/oauth/token"  # noqa: S105 - public OAuth endpoint
_MAX_AUTH_BYTES = 1024 * 1024
_REFRESH_WINDOW_S = 60
_AUTH_CLOSE_WAIT_S = 20.0
log = logging.getLogger(__name__)


class SubscriptionAuthError(RuntimeError):
    """Safe, actionable auth failure without provider bodies or credentials."""

    def __init__(self, message: str, *, code: str = "subscription_auth_unavailable") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SubscriptionCredentials:
    access_token: str = field(repr=False)
    account_id: str = field(repr=False)


def _claims(token: object) -> dict[str, Any]:
    """Decode metadata for expiry/routing only; the server authenticates tokens."""
    if not isinstance(token, str) or token.count(".") != 2:
        return {}
    try:
        payload = token.split(".")[1]
        value = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, binascii.Error):
        return {}  # Opaque tokens simply do not have locally usable metadata.
    return value if isinstance(value, dict) else {}


def _string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _credentials(data: dict[str, Any]) -> SubscriptionCredentials:
    if data.get("auth_mode") not in (None, "chatgpt"):
        raise SubscriptionAuthError("Select a ChatGPT subscription login in Agent accounts.")
    tokens = data.get("tokens")
    if not isinstance(tokens, dict) or not _string(tokens.get("access_token")):
        raise SubscriptionAuthError("Sign in to ChatGPT in Agent accounts before starting voice.")
    access = _string(tokens.get("access_token"))
    if access.startswith("sk-") or any(c in access for c in "\r\n"):
        raise SubscriptionAuthError("The selected account does not contain a ChatGPT OAuth login.")
    account = _string(tokens.get("account_id")) or _string(data.get("account_id"))
    for token in (tokens.get("id_token"), access):
        auth_claim = _claims(token).get("https://api.openai.com/auth")
        if not account and isinstance(auth_claim, dict):
            account = _string(auth_claim.get("chatgpt_account_id"))
    if not account or any(c in account for c in "\r\n"):
        raise SubscriptionAuthError("This ChatGPT login has no account identity. Sign in again.")
    return SubscriptionCredentials(access_token=access, account_id=account)


def _needs_refresh(data: dict[str, Any]) -> bool:
    tokens = data.get("tokens", {})
    expiry = _claims(tokens.get("access_token")).get("exp")
    if isinstance(expiry, (int, float)) and not isinstance(expiry, bool):
        return expiry <= time.time() + _REFRESH_WINDOW_S
    stamp = data.get("last_refresh")
    if isinstance(stamp, str):
        try:
            return (
                time.time() - datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
                > 8 * 86400
            )
        except ValueError:
            pass  # A missing timestamp is handled by a single reactive 401 refresh.
    return False


def _read_auth(path: Path) -> dict[str, Any]:
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > _MAX_AUTH_BYTES
            or getattr(metadata, "st_nlink", 1) != 1
            or getattr(metadata, "st_file_attributes", 0) & 0x400
        ):
            raise SubscriptionAuthError("The selected ChatGPT credential file is unsafe.")
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise SubscriptionAuthError("Sign in to the selected ChatGPT account again.") from exc
    if not isinstance(data, dict):
        raise SubscriptionAuthError("The selected ChatGPT login is invalid. Sign in again.")
    return data


def _save_auth(path: Path, data: dict[str, Any]) -> None:
    """Replace only after a compare-and-swap check made by the caller."""
    descriptor, temporary = tempfile.mkstemp(
        prefix=".jarvis-oauth-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class SubscriptionAuth:
    """Lazy access to one pinned account; account switches never change a call."""

    def __init__(self, account_id: str = "", *, account: Any = None, http_pool: Any = None) -> None:
        self._selection = account_id
        self._account = account
        self._pool = http_pool or SyncHttpClientPool(timeout_s=15.0)
        self._last_token = ""
        self._failed_refresh = ""
        self._remote_account_id = ""
        self._refreshed_at = 0.0
        self._closing = False
        self._credential_tasks: set[asyncio.Task[SubscriptionCredentials]] = set()
        self._close_task: asyncio.Task[None] | None = None

    @classmethod
    def from_runtime_config(cls, config: Any) -> SubscriptionAuth:
        return cls(str(getattr(getattr(config, "live", None), "subscription_account_id", "")))

    def _selected_account(self) -> Any:
        if self._account is None:
            from jarvis.agent_accounts import active_account, resolve

            account = resolve(self._selection) if self._selection else active_account("codex")
            if account is None or account.platform != "codex":
                raise SubscriptionAuthError("Select an available Codex account for ChatGPT voice.")
            self._account = account
        return self._account

    async def status(self) -> dict[str, Any]:
        return await asyncio.to_thread(self.status_snapshot)

    @property
    def selected_account_id(self) -> str:
        return str(self._selected_account().id)

    def status_snapshot(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "account_id": self._selection,
            "label": "",
            "connected": False,
            "reason": "",
            "voice_verified": False,
        }
        try:
            account = self._selected_account()
            result.update(account_id=account.id, label=account.label)
            data = _read_auth(account.config_dir / "auth.json")
            credentials = _credentials(data)
            refresh = _string(data["tokens"].get("refresh_token"))
            if self._failed_refresh and self._failed_refresh == refresh:
                raise SubscriptionAuthError(
                    "The ChatGPT login expired. Sign in again in Agent accounts."
                )
            result["connected"] = bool(credentials) and (not _needs_refresh(data) or bool(refresh))
            if not result["connected"]:
                result["reason"] = "The ChatGPT login expired. Sign in again in Agent accounts."
        except SubscriptionAuthError as exc:
            log.debug("ChatGPT subscription readiness unavailable (%s).", exc.code)
            result["reason"] = str(exc)
        return result

    async def credentials(self, *, force_refresh: bool = False) -> SubscriptionCredentials:
        if self._closing:
            raise SubscriptionAuthError("This ChatGPT voice connection is closing.")
        # Cancelling to_thread's awaiter cannot stop its thread. Keep the worker
        # task alive until rotated credentials are persisted, including on hangup.
        task = asyncio.create_task(
            asyncio.to_thread(self._load_credentials, force_refresh),
            name="live-subscription-credentials",
        )
        self._credential_tasks.add(task)
        task.add_done_callback(self._credential_done)
        return await asyncio.shield(task)

    def _credential_done(self, task: asyncio.Task[SubscriptionCredentials]) -> None:
        self._credential_tasks.discard(task)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                # Retrieve detached failures after a caller cancels; never log
                # provider bodies, token metadata, or exception payloads.
                log.debug("ChatGPT credential work failed (%s).", type(error).__name__)

    def _pin_identity(self, credentials: SubscriptionCredentials) -> SubscriptionCredentials:
        if self._remote_account_id and credentials.account_id != self._remote_account_id:
            raise SubscriptionAuthError(
                "The selected ChatGPT account changed. Start a new voice call to use it."
            )
        self._remote_account_id = credentials.account_id
        return credentials

    def _load_credentials(self, force_refresh: bool) -> SubscriptionCredentials:
        from filelock import FileLock, Timeout

        from jarvis.agent_config_parity import setup_lock

        account = self._selected_account()
        path = account.config_dir / "auth.json"
        try:
            with setup_lock(account.config_dir):
                data = _read_auth(path)
                current = self._pin_identity(_credentials(data))
                # Another voice/reasoning caller or Codex may already have refreshed.
                changed = bool(self._last_token and current.access_token != self._last_token)
                refreshed_recently = (
                    self._refreshed_at and time.monotonic() - self._refreshed_at < 30
                )
                if (force_refresh and not changed and not refreshed_recently) or _needs_refresh(
                    data
                ):
                    with FileLock(str(path.with_name(".jarvis-oauth-refresh.lock")), timeout=2):
                        latest = _read_auth(path)
                        if latest != data:
                            current = self._pin_identity(_credentials(latest))
                            if _needs_refresh(latest):
                                raise SubscriptionAuthError(
                                    "ChatGPT sign-in changed. Try voice again."
                                )
                        else:
                            current = self._pin_identity(self._refresh(path, data))
                self._last_token = current.access_token
                return current
        except Timeout as exc:
            raise SubscriptionAuthError(
                "ChatGPT login is being refreshed. Try voice again shortly."
            ) from exc

    def _refresh(self, path: Path, data: dict[str, Any]) -> SubscriptionCredentials:
        import httpx

        refresh = _string(data["tokens"].get("refresh_token"))
        if not refresh or refresh == self._failed_refresh:
            raise SubscriptionAuthError(
                "The ChatGPT login expired. Sign in again in Agent accounts."
            )
        try:
            response = self._pool.client().post(
                _TOKEN_URL,
                json={
                    "client_id": _CODEX_CLIENT_ID,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh,
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise SubscriptionAuthError(
                "ChatGPT sign-in could not be refreshed. Try again later."
            ) from exc
        if response.status_code != 200:
            # External Codex processes do not share Jarvis's refresh lock. Read a
            # rotated login once before deciding this refresh token is unusable.
            latest = _read_auth(path)
            if latest != data:
                return _credentials(latest)
            if response.status_code in (400, 401, 403):
                self._failed_refresh = refresh
                raise SubscriptionAuthError(
                    "The ChatGPT login expired. Sign in again in Agent accounts."
                )
            raise SubscriptionAuthError(
                "ChatGPT sign-in is temporarily unavailable. Try again later."
            )
        try:
            updated = response.json()
        except ValueError as exc:
            raise SubscriptionAuthError("ChatGPT returned an invalid sign-in response.") from exc
        if not isinstance(updated, dict) or not _string(updated.get("access_token")):
            raise SubscriptionAuthError("ChatGPT returned an incomplete sign-in response.")
        merged = dict(data)
        merged["tokens"] = dict(data["tokens"])
        for name in ("access_token", "refresh_token", "id_token"):
            if _string(updated.get(name)):
                merged["tokens"][name] = updated[name]
        merged["last_refresh"] = datetime.now(UTC).isoformat()
        credentials = _credentials(merged)
        for name in ("access_token", "id_token"):
            claims = _claims(updated.get(name)).get("https://api.openai.com/auth", {})
            identity = _string(claims.get("chatgpt_account_id")) if isinstance(claims, dict) else ""
            if identity and identity != credentials.account_id:
                raise SubscriptionAuthError("ChatGPT refreshed a different account. Sign in again.")
        # Never overwrite a concurrent sign-out, account switch or token rotation.
        latest = _read_auth(path)
        if latest != data:
            return _credentials(latest)
        _save_auth(path, merged)
        self._failed_refresh = ""
        self._refreshed_at = time.monotonic()
        return credentials

    async def aclose(self) -> None:
        if self._close_task is None:
            # No await separates this flag from credential task registration.
            self._closing = True
            self._close_task = asyncio.create_task(
                self._finish_close(), name="live-subscription-auth-close"
            )
            self._close_task.add_done_callback(self._close_done)
        try:
            await asyncio.wait_for(asyncio.shield(self._close_task), timeout=_AUTH_CLOSE_WAIT_S)
        except TimeoutError:
            # HTTP refresh already has a 15-second timeout. An unusually slow
            # filesystem/lock must not make voice teardown unbounded, and closing
            # the pool under that worker would lose a rotated login. The tracked
            # task completes cleanup as soon as the worker finishes.
            log.warning("ChatGPT credential cleanup is waiting for outstanding refresh work.")

    async def _finish_close(self) -> None:
        pending = tuple(self._credential_tasks)
        if pending:
            # Credential errors are reported to their callers (or safely consumed
            # by _credential_done); teardown still has to release the HTTP pool.
            await asyncio.gather(*pending, return_exceptions=True)
        close = getattr(self._pool, "close", None)
        if close is not None:
            await asyncio.to_thread(close)

    @staticmethod
    def _close_done(task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                log.warning("ChatGPT credential cleanup failed (%s).", type(error).__name__)


async def subscription_login_ready(config: Any) -> bool:
    return bool((await SubscriptionAuth.from_runtime_config(config).status())["connected"])


def subscription_login_ready_sync(config: Any) -> bool:
    """Factory capability check: local metadata only, no HTTP or refresh."""
    return bool(SubscriptionAuth.from_runtime_config(config).status_snapshot()["connected"])
