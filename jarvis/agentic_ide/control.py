"""Scoped workflow access to the existing IDE lifecycle and recorded context."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from .session import (
    Registry,
    SessionError,
    accepts_prompts,
    account_home,
    agent_argv,
    resolve_account,
)


class CodingSessionControl:
    """Implements core.protocols.CodingSessionGateway without a browser viewer."""

    def __init__(self, registry: Registry) -> None:
        self.registry = registry

    @staticmethod
    def _accounts() -> list[dict[str, Any]]:
        from jarvis import agent_accounts

        active = agent_accounts.active_ids()
        return [
            {
                "id": snapshot.account.id,
                "agent": platform,
                "name": snapshot.account.label,
                "connected": snapshot.connected,
                "mode": snapshot.mode,
                "active": active.get(platform) == snapshot.account.id,
            }
            for platform in agent_accounts.platforms()
            for snapshot in agent_accounts.snapshots(platform)
        ]

    @staticmethod
    def _state(owner: Any, term: Any) -> dict[str, Any]:
        return {
            "workspace_id": owner.id,
            "terminal_id": "pane:" + term.history_id,
            "name": term.name,
            "cwd": owner.folder,
            "agent": term.agent,
            "account": term.account,
            "model": term.model,
            "effort": term.effort,
            "permission_mode": term.permission_mode,
            "status": term.status,
            "error": term.error,
            "exit_code": term.exit_code,
            "activity": term.reading().activity,
        }

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        from jarvis.workspace import agents, launch_picks

        action = args.get("action")
        if action == "discover":
            live = await launch_picks.live_models()
            return {
                "available": agents.pty_available(),
                "agents": [
                    {
                        "agent": info.name,
                        "installed": info.installed and agent_argv(info.name) is not None,
                        **launch_picks.offered(info.name, live),
                    }
                    for info in await agents.detect_agents()
                    if accepts_prompts(info.name)
                ],
                "accounts": await asyncio.to_thread(self._accounts),
                "connection_note": (
                    "Installed does not prove authenticated. Use the account connection status "
                    "where exposed; other CLIs report login requirements at startup."
                ),
                "sessions": [
                    self._state(owner, term)
                    for owner in self.registry.sessions
                    for term in owner.terminals
                    if accepts_prompts(term.agent)
                ],
            }
        if action == "open":
            if not agents.pty_available():
                raise SessionError("The IDE PTY backend is unavailable on this installation.")
            folder = Path(str(args.get("cwd") or ""))
            if not folder.is_absolute():
                raise SessionError("Choose an explicit absolute project cwd; never guess a folder.")
            agent = str(args.get("agent") or "")
            if not accepts_prompts(agent):
                raise SessionError("Choose a registered coding CLI from discover.")
            account = args.get("account")
            if account and resolve_account(agent, account) != account:
                raise SessionError("The requested account is not available for this coding CLI.")
            for key in ("model", "effort", "permission_mode"):
                value = args.get(key)
                normalizer = getattr(
                    launch_picks,
                    "normalize_permission" if key == "permission_mode" else "normalize_" + key,
                )
                if value and normalizer(agent, value) != value:
                    raise SessionError(f"The requested {key} is not supported by this coding CLI.")
            owner = await self.registry.start(str(folder), [args])
            term = owner.terminals[0]
            try:
                await self._start(owner, term)
            except Exception as exc:
                # The workspace already exists: report its stable identity even on failed startup.
                return {**self._state(owner, term), "started": False, "error": str(exc)}
            return {**self._state(owner, term), "started": True}
        workspace = str(args.get("workspace_id") or "")
        identity = str(args.get("terminal_id") or "")
        if not workspace or not identity.startswith("pane:"):
            raise SessionError(
                "Use the explicit workspace_id and terminal_id returned by discover/open."
            )
        found = self.registry.find_terminal(identity, workspace)
        if found is None:
            raise SessionError(
                "The selected session is closed or not restored. Discover current sessions."
            )
        owner, term = found
        if action == "status":
            return self._state(owner, term)
        input_state = {
            "input_token": self.registry.input_token(term),
            "screen_excerpt": term.transcript.tail(20),
            "response_mode": "dialog" if term.reading().activity == "asking" else "text",
            "screen_scope": "Visible input context only; not a conversation transcript.",
        }
        if action == "input":
            return {**self._state(owner, term), **input_state}
        if action in ("send", "respond"):
            if not str(args.get("prompt") or "").strip():
                raise SessionError("A nonempty assignment is required.")
            if term.status in ("dead", "error", "exited"):
                raise SessionError(
                    "The coding terminal has stopped. Inspect it before opening another."
                )
            if not term.pty_id:
                await self._start(owner, term)
            if action == "respond" and not args.get("input_token"):
                raise SessionError("Read the current input request before responding.")
            term = await self.registry.send_prompt(
                identity,
                str(args["prompt"]),
                workspace_id=workspace,
                typed=str(args["prompt"]),
                require_idle=action != "respond",
                expected_input=str(args.get("input_token") or "") if action == "respond" else "",
                allow_question=action == "respond" and args.get("response_mode") == "dialog",
            )
            return {
                **self._state(owner, term),
                "submitted": term.submitted,
                "delivery": "accepted"
                if term.submitted is True
                else ("not_accepted" if term.submitted is False else "uncertain"),
                "completed": False,
                "retry": "Do not resend automatically. Read context and inspect delivery first.",
            }
        if action not in ("context", "observe"):
            raise SessionError("Unknown coding-session action.")
        from . import agent_transcript

        state = self._state(owner, term)
        readable = agent_transcript.can_read(term.agent)
        handle = term.resume
        result = None
        if readable and handle is not None:
            result = await asyncio.to_thread(
                agent_transcript.read_timeline,
                term.agent,
                handle.id,
                home=account_home(term.agent, term.account),
                live=state["activity"] in ("working", "starting"),
            )
        source = handle.id if handle else None
        cursor = args.get("cursor") or {}
        if cursor and cursor.get("source") != source:
            raise SessionError("Transcript changed; request context without a cursor.")
        offset = max(0, int(cursor.get("offset", 0)))
        limit = max(1, min(100, int(args.get("limit", 30))))
        events = result.events if result else []
        if action == "observe":
            offset = max(0, len(events) - 30)
        if offset > len(events):
            raise SessionError("Transcript was truncated; request context without a cursor.")

        def fingerprint(end: int) -> str:
            return hashlib.sha256(json.dumps(events[:end], sort_keys=True).encode()).hexdigest()

        if cursor and cursor.get("prefix") != fingerprint(offset):
            raise SessionError("Recorded context changed; request context without a cursor.")
        page: list[dict[str, Any]] = []
        size = 0
        selected = events[offset : offset + limit]
        if action == "observe":
            # Preserve the newest question under the output cap.
            selected = list(reversed(selected))
        for event in selected:
            encoded = json.dumps(event, ensure_ascii=False)
            if size + len(encoded) > 24000 and page:
                break
            if len(encoded) > 24000:
                event = {"truncated": True, "recorded_event_excerpt": encoded[:23000]}
            page.append(event)
            size += min(len(encoded), 24000)
        if action == "observe":
            page.reverse()
            offset = len(events) - len(page)
        return {
            **state,
            **(input_state if action == "observe" else {}),
            **({"earlier_events_omitted": offset} if action == "observe" else {}),
            "readable": readable,
            "available": result is not None,
            "events": page,
            "cursor": {
                "source": source,
                "offset": offset + len(page),
                "prefix": fingerprint(offset + len(page)),
            },
            "has_more": offset + len(page) < len(events),
            "context_scope": (
                "Recorded messages, tools, results and provider-exposed notes only. "
                "No unrecorded reasoning."
            ),
        }

    async def _start(self, owner: Any, term: Any) -> None:
        async def discard(value: Any) -> None:
            # The registry itself records output and process exit; no display consumer is needed.
            pass

        identity = "pane:" + term.history_id
        try:
            await self.registry.attach(
                identity,
                120,
                40,
                discard,
                discard,
                workspace_id=owner.id,
                claim_owner=False,
            )
        finally:
            self.registry.detach(identity, workspace_id=owner.id, viewer=discard)
