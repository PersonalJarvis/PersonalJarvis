"""Bounded, read-only resource checks before a new grant becomes connected.

These checks validate access, not a completed plugin smoke-test audit. No token
is stored here, and provider payloads never become diagnostic messages.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from jarvis.marketplace.catalog import PluginSpec
from jarvis.marketplace.token_store import Tokens

VERIFY_TIMEOUT_SECONDS = 30.0
CLOSE_TIMEOUT_SECONDS = 5.0


class ConnectionVerificationError(RuntimeError):
    """A fixed, safe explanation suitable for the connection dialog."""


# Explicit operations avoid accidentally verifying through a future write tool.
_REST_PROBES: dict[str, tuple[str, dict[str, Any]]] = {
    "outlook": ("list_messages", {"query_params": {"$top": 1, "$select": "id"}}),
    "onedrive": ("list_files", {"query_params": {"$top": 1, "$select": "id"}}),
    "teams": ("list_chats", {"query_params": {"$top": 1}}),
    "sharepoint": ("search_sites", {"query_params": {"search": "*"}}),
    "onenote": ("list_notebooks", {}),
    "microsoft_todo": ("list_lists", {}),
    "azure": ("list_subscriptions", {}),
    "google_cloud": ("list_projects", {}),
    "gitlab": ("list_projects", {"query_params": {"per_page": 1}}),
    "x": ("read_profile", {}),
    "linkedin": ("read_profile", {}),
    "meta": ("list_pages", {}),
    "youtube_studio": ("read_channel", {}),
    "hubspot": ("list_contacts", {"query_params": {"limit": 1}}),
    "figma": ("read_profile", {}),
    "zoom": ("list_meetings", {}),
}

_NATIVE_PROBES: dict[str, tuple[str, dict[str, str], str]] = {
    "gmail": ("https://gmail.googleapis.com/gmail/v1/users/me/profile", {}, "emailAddress"),
    "google_drive": (
        "https://www.googleapis.com/drive/v3/files",
        {"pageSize": "1", "fields": "files(id)"},
        "files",
    ),
    "google_calendar": (
        "https://www.googleapis.com/calendar/v3/users/me/calendarList",
        {"maxResults": "1"},
        "kind",
    ),
    "spotify": ("https://api.spotify.com/v1/me", {}, "id"),
    "youtube_music": (
        "https://www.googleapis.com/youtube/v3/playlists",
        {"part": "id", "mine": "true", "maxResults": "1"},
        "kind",
    ),
}


async def _verify_native(spec: PluginSpec, tokens: Tokens) -> None:
    if spec.id == "home_assistant":
        from jarvis.marketplace.instance_url import normalize_instance_url

        url = normalize_instance_url(tokens.extra.get("instance_url", "")) + "/api/"
        params: dict[str, str] = {}
        expected = "message"
    else:
        url, params, expected = _NATIVE_PROBES[spec.id]
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        response = await client.get(
            url, params=params, headers={"Authorization": f"Bearer {tokens.access}"}
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or expected not in payload or payload.get("error"):
            raise ConnectionVerificationError(
                "The provider returned an unusable resource response."
            )


async def _verify_rest(spec: PluginSpec, tokens: Tokens) -> None:
    from jarvis.plugins.tool.connected_server import ConnectedRestClient

    operation, arguments = _REST_PROBES[spec.id]
    client = ConnectedRestClient(
        spec.id, tokens.access, auth_type="oauth" if tokens.extra.get("client_id") else "pat"
    )
    try:
        async with asyncio.timeout(VERIFY_TIMEOUT_SECONDS):
            payload = await client.call(operation, arguments)
            if not isinstance(payload, (dict, list)) or (
                isinstance(payload, dict)
                and (payload.get("error") or "content_type" in payload or payload.get("accepted"))
            ):
                raise ConnectionVerificationError(
                    "The provider returned an unusable resource response."
                )
    finally:
        async with asyncio.timeout(CLOSE_TIMEOUT_SECONDS):
            await client.close()


async def _verify_mcp(spec: PluginSpec, tokens: Tokens) -> None:
    from jarvis.marketplace.plugin_mcp import plugin_to_mcp_server_spec
    from jarvis.marketplace.plugin_registry import _default_client_factory

    resolved = plugin_to_mcp_server_spec(spec, tokens)
    if resolved is None or resolved[0].transport != "http":
        raise ConnectionVerificationError("This plugin has no supported connection verifier.")
    server_spec, env = resolved
    client = _default_client_factory(server_spec, env_overrides=env)
    # Keep all three lifecycle calls in this task. MCP context managers may
    # own AnyIO cancel scopes that cannot be closed by a wait_for child task.
    try:
        async with asyncio.timeout(VERIFY_TIMEOUT_SECONDS):
            await client.start()
            tools = await client.list_tools()
            if not tools or not all(isinstance(t, dict) and t.get("name") for t in tools):
                raise ConnectionVerificationError("The provider exposes no usable plugin tools.")
    finally:
        async with asyncio.timeout(CLOSE_TIMEOUT_SECONDS):
            await client.stop()


async def verify_connection(spec: PluginSpec, tokens: Tokens) -> None:
    """Raise safely on failed access; leave persistence to the successful caller."""
    try:
        if not tokens.access or tokens.needs_reauth:
            raise ConnectionVerificationError("Authorization is missing or expired. Connect again.")
        if spec.verification_hook is not None:
            if spec.verification_hook != "home_assistant_api" or spec.id != "home_assistant":
                raise ConnectionVerificationError(
                    "This plugin declares an unsupported connection verifier."
                )
        if spec.id in _REST_PROBES:
            await _verify_rest(spec, tokens)
        elif spec.id in _NATIVE_PROBES or spec.id == "home_assistant":
            async with asyncio.timeout(VERIFY_TIMEOUT_SECONDS):
                await _verify_native(spec, tokens)
        else:
            await _verify_mcp(spec, tokens)
    except ConnectionVerificationError:
        raise
    except asyncio.CancelledError:
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise
        # A failed MCP transport can cancel its internal owner and forward
        # that exception without cancellation of this verification task.
        raise ConnectionVerificationError(
            "The provider connection closed during verification. Try again."
        ) from None
    except TimeoutError:
        raise ConnectionVerificationError("The connection check timed out. Try again.") from None
    except Exception:
        # Exception text can contain tokens, response bodies or account data.
        raise ConnectionVerificationError(
            "The provider connection check failed. Check account access and try again."
        ) from None
