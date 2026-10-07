"""``society_request_credential``: an agent asks the person for a token or key.

The chat shows a secure field ("GitHub token — stored safely, available as
$GITHUB_TOKEN"); the person pastes the value and saves. The value goes from
that field into the agent's credential vault (credentials.py) and never into
the chat, a prompt or this tool's result: the agent learns only that the
variable is set, and uses it by name inside its commands.

A credential the agent already has is not asked for again unless the agent
says the stored one stopped working (``replace``). A routine never asks: its
chat is unattended, and the tool is not offered there.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Final

from jarvis.agent_chat.credential_requests import (
    CANCELLED,
    CREDENTIAL_TIMEOUT_S,
    DECLINED,
    MAX_REQUESTS_PER_TURN,
    SAVED,
    TIMEOUT,
    CredentialSpec,
    TooManyCredentialRequests,
)
from jarvis.core.protocols import ToolResult

from . import credentials
from .credentials import validate_env_name
from .routine_runner import is_routine_session

log = logging.getLogger(__name__)

__all__ = ["CREDENTIAL_TOOL_NAME", "RequestCredentialTool", "usage_note"]

CREDENTIAL_TOOL_NAME: Final[str] = "society_request_credential"

#: One call waits this long for the card, then hands back "waiting" (CLI
#: seats drop an MCP call after about a minute).
WAIT_SLICE_S: Final[float] = 45.0
_MINUTES: Final[int] = int(CREDENTIAL_TIMEOUT_S // 60)


def usage_note(env: str) -> str:
    """How the agent uses a stored credential without ever seeing it."""
    return (
        f"The value is set as the environment variable {env} in every command you run with "
        f"society_shell (only there, not in any other shell). Reference it by name inside the "
        f"command (bash: ${env}, PowerShell: $env:{env}). Never print, echo, log, commit or "
        "write the value anywhere, and never ask the user to paste it into the chat; Jarvis "
        "masks it in command output."
    )


class RequestCredentialTool:
    """Ask the person for a secret through a secure field in the chat."""

    name: str = CREDENTIAL_TOOL_NAME
    risk_tier: str = "safe"
    is_action_tool: bool = False
    description: str = (
        "Ask the user for a credential you need for the task: an API token, bot token, "
        "access key or password for a service (for example a GitHub token, a Discord bot "
        "token). The chat shows a secure field; the user pastes the value there and it is "
        "stored in the system credential store. You NEVER see the value: it is set as the "
        "environment variable you name in env for commands you run with society_shell, and you "
        "reference it by name inside them ($GITHUB_TOKEN). Use this instead of asking the "
        "user to type a secret into the chat. "
        "Choose a conventional UPPER_SNAKE_CASE name, give a short label, and say in "
        "description what it is for and where the user creates it (with the minimal scopes). "
        "A credential you already have is not asked for again: the call returns 'available'. "
        "Set replace only when the stored value was rejected by the service. While the user "
        "has not answered yet, the call returns status 'waiting' with a request_id: then call "
        "this tool again with only wait_for set to that id, and do nothing else meanwhile."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "env": {
                "type": "string",
                "description": "Environment variable name, UPPER_SNAKE_CASE, e.g. GITHUB_TOKEN.",
            },
            "label": {
                "type": "string",
                "description": "What it is, 1-4 words, e.g. 'GitHub token'.",
            },
            "description": {
                "type": "string",
                "description": (
                    "One or two sentences: what you need it for and where the user creates "
                    "it, with the minimal permissions."
                ),
            },
            "placeholder": {
                "type": "string",
                "description": "Optional hint of the expected shape, e.g. 'ghp_…'.",
            },
            "replace": {
                "type": "boolean",
                "description": "Ask again although a value is stored (the service rejected it).",
            },
            "wait_for": {
                "type": "string",
                "description": (
                    "Only to keep waiting on a card you already opened: the request_id from a "
                    "'waiting' result. Send it alone."
                ),
            },
        },
    }

    def __init__(self, runtime: Any, agent_id: str, *, session_id: str) -> None:
        self._runtime = runtime
        self._agent_id = agent_id
        self._session_id = session_id

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        wait_for = str(args.get("wait_for") or "").strip()
        if wait_for:
            return await self._keep_waiting(wait_for)
        try:
            env = validate_env_name(args.get("env"))
            spec = CredentialSpec.build(
                env,
                args.get("label"),
                args.get("description"),
                args.get("placeholder"),
                replace=bool(args.get("replace")),
            )
        except ValueError as exc:
            # A malformed request goes back to the agent as the error to fix.
            return ToolResult(success=False, output=None, error=f"invalid request: {exc}")
        vault = credentials.current_vault()
        if vault is None:
            return ToolResult(
                success=False, output=None, error="The credential store is not available yet."
            )
        if not spec.replace and await asyncio.to_thread(vault.has, self._agent_id, env):
            return ToolResult(
                success=True,
                output={"status": "available", "env": env, "note": usage_note(env)},
            )
        if is_routine_session(self._session_id):
            return ToolResult(
                success=False,
                output={"status": "unattended", "env": env},
                error=(
                    f"Routines run unattended and cannot ask for {env}. Report in your result "
                    "that the user has to provide it in your chat."
                ),
            )
        service = self._runtime.chat_service()
        if service is None or not callable(getattr(service, "open_credential_request", None)):
            return _nobody(env)
        agent_id = self._agent_id

        async def save(value: str) -> None:
            await asyncio.to_thread(vault.store, agent_id, env, value, label=spec.label)

        try:
            request_id = await service.open_credential_request(
                self._session_id, spec, save, asker=self._asker()
            )
        except TooManyCredentialRequests:
            return ToolResult(
                success=False,
                output={"status": "limit", "env": env},
                error=(
                    f"This turn already asked for {MAX_REQUESTS_PER_TURN} credentials. Ask for "
                    "the rest in one later turn and say which ones are still missing."
                ),
            )
        except RuntimeError as exc:
            log.info("society credential: %s cannot ask here (%s)", agent_id, exc)
            return _nobody(env)
        return await self._wait(service, request_id, env)

    async def _keep_waiting(self, request_id: str) -> ToolResult:
        service = self._runtime.chat_service()
        try:
            spec = service.credential_request_spec(self._session_id, request_id)
        except (AttributeError, KeyError):
            # Unknown request id is reported to the agent in the result.
            return ToolResult(
                success=False,
                output=None,
                error=(
                    "No open credential field with that id in this chat (it closed with the "
                    "turn). Do not ask again in this turn; tell the user what is missing."
                ),
            )
        return await self._wait(service, request_id, spec.env)

    async def _wait(self, service: Any, request_id: str, env: str) -> ToolResult:
        status = await service.wait_credential_request(self._session_id, request_id, WAIT_SLICE_S)
        if status is None:
            return ToolResult(
                success=True,
                output={
                    "status": "waiting",
                    "request_id": request_id,
                    "env": env,
                    "note": (
                        "The user has not saved the credential yet; the field stays open for "
                        f"{_MINUTES} minutes. Call {CREDENTIAL_TOOL_NAME} again now with only "
                        f'{{"wait_for": "{request_id}"}}. Do nothing else meanwhile.'
                    ),
                },
            )
        if status == SAVED:
            return ToolResult(
                success=True, output={"status": SAVED, "env": env, "note": usage_note(env)}
            )
        if status == CANCELLED:
            return ToolResult(
                success=False,
                output={"status": CANCELLED, "env": env},
                error="The user stopped this turn. Do not continue the task.",
            )
        reason = (
            "The user declined to provide it."
            if status == DECLINED
            else f"Nobody filled the field within {_MINUTES} minutes."
            if status == TIMEOUT
            else "The field closed without a value."
        )
        return ToolResult(
            success=False,
            output={"status": status, "env": env},
            error=(
                f"{reason} {env} is not set. Continue without it where you can, and tell the "
                "user what needs it. Do not ask again in this turn."
            ),
        )

    def _asker(self) -> str:
        cached = getattr(self._runtime, "cached_agent", None)
        agent = cached(self._agent_id) if callable(cached) else None
        return str(getattr(agent, "name", "") or "")


def _nobody(env: str) -> ToolResult:
    return ToolResult(
        success=False,
        output={"status": "unattended", "env": env},
        error=(
            f"Nobody can enter {env} in this run. Report that the user has to provide it in "
            "your chat."
        ),
    )

