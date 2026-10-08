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
    DECLINED,
    MAX_REQUESTS_PER_TURN,
    SAVED,
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
        "REQUIRED whenever a task needs a secret only the user can supply: an API key, bot or "
        "access token, personal access token, password, client secret, webhook URL with a "
        "secret or database connection string (for example a Discord bot token, an OpenAI "
        "API key, a GitHub token). Call it as soon as you know the secret is needed, including "
        "when you set up an app, bot, script or integration that needs one. It opens a secure "
        "field in the user's chat; the value goes to the system credential store. It is the "
        "ONLY way to obtain a secret: never ask for one in your reply or a question card, and "
        "never send the user to settings, a plugin dialog, a .env file or the browser to "
        "enter it. You NEVER see the value: it is set as the environment variable you name "
        "in env for commands you run with society_shell; reference it by name inside them "
        "($GITHUB_TOKEN) and have your code read it from the environment. "
        "Choose a conventional UPPER_SNAKE_CASE name, give a short label, and say in "
        "description what it is for and where the user creates it (with the minimal scopes), "
        "in the user's language. This explanation appears before the secure field. "
        "A credential you already have is not asked for again: the call returns 'available'. "
        "Set replace only when the stored value was rejected by the service. While the user "
        "has not answered yet, the call returns immediately with status 'waiting' and a "
        "request_id. Do not poll, sleep, or keep the turn running for a token. The field "
        "stays open until the user saves or cancels, even if the turn ends. Continue unrelated "
        "work and respond to new messages while the user obtains the credential. If nothing "
        "else can be done now, finish your reply so the user can ask questions. On a later "
        "turn, wait_for checks the existing field's status immediately without opening another."
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
                    "Check a previously opened field on a later turn: the request_id from a "
                    "'waiting' result. Returns immediately. Send it alone; never poll in a loop."
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
            return await self._check_existing(wait_for)
        try:
            env = validate_env_name(args.get("env"))
            spec = CredentialSpec.build(
                env,
                args.get("label"),
                args.get("description"),
                args.get("placeholder"),
                replace=bool(args.get("replace")),
            )
            if not spec.description:
                raise ValueError(
                    "description is required: explain why the task needs this credential"
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
            await credentials.save_requested_credential(agent_id, env, value, label=spec.label)

        try:
            request_id = await service.open_credential_request(
                self._session_id, spec, save, asker=self._asker()
            )
        except TooManyCredentialRequests as exc:  # Return the limit as the tool's visible error.
            return ToolResult(
                success=False,
                output={"status": "limit", "env": env},
                error=(
                    f"Credential field limit reached: {exc}. At most {MAX_REQUESTS_PER_TURN} "
                    "new fields can be opened per turn. Reuse existing pending fields."
                ),
            )
        except RuntimeError as exc:
            log.info("society credential: %s cannot ask here (%s)", agent_id, exc)
            return _nobody(env)
        return await self._status(service, request_id, env)

    async def _check_existing(self, request_id: str) -> ToolResult:
        service = self._runtime.chat_service()
        try:
            await service.restore_credential_requests(self._session_id)
            spec = service.credential_request_spec(self._session_id, request_id)
        except (AttributeError, KeyError):
            # Unknown request id is reported to the agent in the result.
            return ToolResult(
                success=False,
                output=None,
                error=(
                    "No credential field with that id in this chat. Check whether the "
                    "credential is already available before opening a new field."
                ),
            )
        return await self._status(service, request_id, spec.env)

    async def _status(self, service: Any, request_id: str, env: str) -> ToolResult:
        # The field owns its lifetime. Holding the turn here queues the very
        # questions the person needs to ask before supplying the credential.
        status = await service.wait_credential_request(self._session_id, request_id, 0)
        if status is None:
            return ToolResult(
                success=True,
                output={
                    "status": "waiting",
                    "request_id": request_id,
                    "env": env,
                    "note": (
                        "The user has not saved the credential yet. The field stays open "
                        "until they save or cancel, including after this turn ends. Continue "
                        "unrelated work if useful; otherwise finish your reply now so the user "
                        "can ask questions. Do not poll, sleep, or call this tool again in this "
                        "turn just to wait. On a later turn, check this field with "
                        f'{{"wait_for": "{request_id}"}} or use the stored credential by name.'
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

