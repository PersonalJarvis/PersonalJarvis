"""google_calendar tool — read + manage calendar events via a JS/Node bot.

Architecture (matches the user's "JavaScript bot" requirement while reusing the
proven Jarvis token machinery): the *bot logic* lives in ``calendar_bot.mjs``
(a dependency-free Node script that talks to the Google Calendar API v3). This
Python class is a thin bridge — it owns the OAuth side: it reads the
marketplace keyring token (key ``plugin_google_calendar_tokens``), hands a
guaranteed-fresh access token to a short-lived Node process per call, and
self-heals on a 401 by refreshing once and retrying. Keeping the token under the
marketplace model is what makes the connection "never expire" (the refresh
scheduler keeps it warm), exactly like Gmail.

Router-tier tool. Full autonomy by design (user mandate): every action runs
without a confirmation prompt — reads are ``safe``, writes (create/update/
delete) are ``monitor`` (executed + audited, never ``ask``).
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.core.protocols import ExecutionContext, ToolResult

log = logging.getLogger(__name__)

_BOT_PATH = Path(__file__).parent / "calendar_bot.mjs"
_NODE_TIMEOUT_S = 25.0

# User-facing error strings (English per CLAUDE.md; the brain rephrases to the
# user's language). Distinct so the brain can tell the failure modes apart.
_NOT_CONNECTED = (
    "Google Calendar is not connected — connect it in the Plugins view."
)
_NEEDS_RECONNECT = (
    "Google Calendar authorization expired and could not be renewed — "
    "please reconnect Google Calendar in the Plugins view."
)
_NODE_MISSING = (
    "Node.js is required for the Google Calendar bot but was not found — "
    "install Node.js (18+) and restart Jarvis."
)

# A runner takes (action, args, access_token) and returns the bot's JSON result
# dict ({"ok": bool, "status": int, "data"/"error": ...}). Injectable for tests.
NodeRunner = Callable[[str, dict[str, Any], str], Awaitable[dict[str, Any]]]


async def _default_node_runner(
    action: str, args: dict[str, Any], token: str
) -> dict[str, Any]:
    """Spawn the Node calendar bot for one action and return its JSON result.

    The full payload (token + action + args) is piped over stdin so the token
    never lands in the OS process list. Never raises: any spawn/parse problem is
    folded into a ``{"ok": False, ...}`` result the bridge maps to a ToolResult.
    """
    import shutil

    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        return {"ok": False, "status": 0, "error": _NODE_MISSING}

    payload = json.dumps({"access_token": token, "action": action, **args})
    try:
        proc = await asyncio.create_subprocess_exec(
            node,
            str(_BOT_PATH),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except OSError as exc:
        return {"ok": False, "status": 0, "error": f"could not start node: {exc}"}

    try:
        out, err = await asyncio.wait_for(
            proc.communicate(payload.encode("utf-8")), timeout=_NODE_TIMEOUT_S
        )
    except TimeoutError:
        with _suppress_proc_kill():
            proc.kill()
        return {"ok": False, "status": 0, "error": "calendar bot timed out"}

    text = out.decode("utf-8", errors="replace").strip()
    if not text:
        stderr = err.decode("utf-8", errors="replace").strip()
        return {
            "ok": False,
            "status": 0,
            "error": stderr[:200] or "calendar bot produced no output",
        }
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"ok": False, "status": 0, "error": f"bad bot output: {text[:200]}"}


class _suppress_proc_kill:
    """Tiny context manager: kill is best-effort, swallow any error."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> bool:
        return True


def _default_token_provider() -> str | None:
    from jarvis.marketplace.token_store import TokenStore

    tokens = TokenStore().load("google_calendar")
    return tokens.access if tokens is not None else None


async def _default_refresher() -> bool:
    """Refresh the stored Google Calendar token in place. Returns True on success.

    Mirrors the Gmail refresher: on an un-healable failure (revoked /
    invalid_client / placeholder client) it flags ``needs_reauth`` so the
    Plugins view offers a Reconnect instead of a green status that lies."""
    import contextlib
    import dataclasses

    from jarvis.marketplace.connect_helpers import build_handler_from_catalog
    from jarvis.marketplace.token_store import TokenStore

    store = TokenStore()
    tokens = store.load("google_calendar")
    if tokens is None or not tokens.refresh:
        return False
    handler = build_handler_from_catalog("google_calendar")
    if handler is None:
        return False
    try:
        new = await handler.refresh(tokens)
    except Exception as exc:  # noqa: BLE001 — classify, never propagate
        with contextlib.suppress(Exception):
            store.save(
                "google_calendar", dataclasses.replace(tokens, needs_reauth=True)
            )
        log.info("google_calendar token refresh failed, flagged needs_reauth: %s", exc)
        return False
    store.save("google_calendar", dataclasses.replace(new, needs_reauth=False))
    return True


class GoogleCalendarRestTool:
    name: str = "google_calendar"
    risk_tier: str = "monitor"
    description: str = (
        "Read and manage the user's Google Calendar. Use for 'what's on my "
        "calendar today', 'any meetings tomorrow', 'schedule a meeting', "
        "'create an event', 'move/reschedule that event', 'delete the event'. "
        "Actions: list_events (read the schedule in a time window — spans ALL of "
        "the user's calendars, so nothing is missed), create_event (add an "
        "event), update_event (change one by id), delete_event (remove one by "
        "id). list_events returns each event's calendar_id; pass it back as "
        "calendar_id on update_event/delete_event when the event is not on the "
        "primary calendar. Times are RFC3339 (e.g. 2026-06-28T15:00:00) with an "
        "optional IANA time_zone (e.g. Europe/Berlin); a bare date "
        "(2026-06-28) makes an all-day event. Requires the Google Calendar "
        "plugin to be connected in the Plugins view."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list_events", "create_event", "update_event", "delete_event"],
                "default": "list_events",
            },
            "time_min": {
                "type": "string",
                "description": "RFC3339 lower bound for list_events (local day start)",
            },
            "time_max": {
                "type": "string",
                "description": "RFC3339 upper bound for list_events (local day end)",
            },
            "query": {"type": "string", "description": "free-text filter (list_events)"},
            "max_results": {"type": "integer", "default": 25},
            "summary": {"type": "string", "description": "event title (create/update)"},
            "start": {"type": "string", "description": "RFC3339 datetime or YYYY-MM-DD"},
            "end": {"type": "string", "description": "RFC3339 datetime or YYYY-MM-DD"},
            "time_zone": {"type": "string", "description": "IANA tz, e.g. Europe/Berlin"},
            "description": {"type": "string"},
            "location": {"type": "string"},
            "event_id": {"type": "string", "description": "event id (update/delete)"},
            "calendar_id": {
                "type": "string",
                "description": "calendar the event lives on (from list_events); "
                "defaults to 'primary' for writes",
            },
        },
        "required": ["action"],
    }

    def __init__(
        self,
        access_token_provider: Callable[[], str | None] | None = None,
        node_runner: NodeRunner | None = None,
        token_refresher: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        self._token_provider = access_token_provider or _default_token_provider
        self._node_runner = node_runner or _default_node_runner
        self._refresher = token_refresher or _default_refresher

    # -- internal: token-aware call with one-shot 401 self-heal ---------------

    async def _call(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        token = self._token_provider()
        if not token:
            return {"ok": False, "status": 0, "error": _NOT_CONNECTED}
        result = await self._node_runner(action, args, token)
        if result.get("ok") is False and result.get("status") == 401:
            # Access token likely expired — refresh once and retry.
            try:
                refreshed = bool(await self._refresher())
            except Exception:  # noqa: BLE001 — refresher must never crash the tool
                refreshed = False
            if not refreshed:
                return {"ok": False, "status": 401, "error": _NEEDS_RECONNECT}
            token = self._token_provider()
            if not token:
                return {"ok": False, "status": 401, "error": _NEEDS_RECONNECT}
            result = await self._node_runner(action, args, token)
        return result

    # -- public actions (also directly unit-testable) -----------------------

    async def list_events(
        self,
        *,
        time_min: str | None = None,
        time_max: str | None = None,
        query: str = "",
        max_results: int = 25,
    ) -> dict[str, Any]:
        return await self._call(
            "list_events",
            {
                "time_min": time_min,
                "time_max": time_max,
                "query": query,
                "max_results": max_results,
            },
        )

    async def create_event(
        self,
        *,
        summary: str,
        start: str,
        end: str,
        time_zone: str | None = None,
        description: str = "",
        location: str = "",
        calendar_id: str = "",
    ) -> dict[str, Any]:
        return await self._call(
            "create_event",
            {
                "summary": summary,
                "start": start,
                "end": end,
                "time_zone": time_zone,
                "description": description,
                "location": location,
                "calendar_id": calendar_id,
            },
        )

    async def update_event(
        self,
        *,
        event_id: str,
        summary: str = "",
        start: str = "",
        end: str = "",
        time_zone: str | None = None,
        description: str = "",
        location: str = "",
        calendar_id: str = "",
    ) -> dict[str, Any]:
        return await self._call(
            "update_event",
            {
                "event_id": event_id,
                "summary": summary,
                "start": start,
                "end": end,
                "time_zone": time_zone,
                "description": description,
                "location": location,
                "calendar_id": calendar_id,
            },
        )

    async def delete_event(
        self, *, event_id: str, calendar_id: str = ""
    ) -> dict[str, Any]:
        return await self._call(
            "delete_event", {"event_id": event_id, "calendar_id": calendar_id}
        )

    # -- Tool protocol ------------------------------------------------------

    def risk_tier_for_args(self, args: dict[str, Any]) -> str:
        """Per-action risk tier. Full autonomy (user mandate): no action ever
        returns ``ask``. Reads are ``safe``; writes (create/update/delete) are
        ``monitor`` — executed without a prompt but audited via the monitor
        tier. An unrecognised action stays at the static ``monitor`` default,
        never silently ``safe``."""
        action = (args.get("action") or "list_events").strip()
        if action == "list_events":
            return "safe"
        return "monitor"

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        action = (args.get("action") or "list_events").strip()
        try:
            if action == "list_events":
                out = await self.list_events(
                    time_min=args.get("time_min"),
                    time_max=args.get("time_max"),
                    query=args.get("query", ""),
                    max_results=int(args.get("max_results", 25)),
                )
            elif action == "create_event":
                if not args.get("summary"):
                    return ToolResult(success=False, output=None, error="summary missing")
                if not args.get("start") or not args.get("end"):
                    return ToolResult(
                        success=False, output=None, error="start and end are required"
                    )
                out = await self.create_event(
                    summary=args["summary"],
                    start=args["start"],
                    end=args["end"],
                    time_zone=args.get("time_zone"),
                    description=args.get("description", ""),
                    location=args.get("location", ""),
                    calendar_id=args.get("calendar_id", ""),
                )
            elif action == "update_event":
                eid = args.get("event_id")
                if not eid:
                    return ToolResult(success=False, output=None, error="event_id missing")
                out = await self.update_event(
                    event_id=eid,
                    summary=args.get("summary", ""),
                    start=args.get("start", ""),
                    end=args.get("end", ""),
                    time_zone=args.get("time_zone"),
                    description=args.get("description", ""),
                    location=args.get("location", ""),
                    calendar_id=args.get("calendar_id", ""),
                )
            elif action == "delete_event":
                eid = args.get("event_id")
                if not eid:
                    return ToolResult(success=False, output=None, error="event_id missing")
                out = await self.delete_event(
                    event_id=eid, calendar_id=args.get("calendar_id", "")
                )
            else:
                return ToolResult(success=False, output=None, error=f"unknown action {action!r}")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output=None, error=str(exc))

        if isinstance(out, dict) and out.get("ok"):
            return ToolResult(success=True, output=out.get("data"))
        error = out.get("error") if isinstance(out, dict) else None
        return ToolResult(success=False, output=None, error=error or "calendar error")
