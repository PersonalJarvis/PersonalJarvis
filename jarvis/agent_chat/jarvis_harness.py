"""What makes the chat session Jarvis rather than a coding agent in a folder.

Two things are handed to the spawned CLI:

* an **MCP config** pointing at this app's own tool server
  (``jarvis.ui.web.mcp_server_routes``), so the model's hands are Jarvis' hands
  — the same catalog the voice path calls, through the same safety gateway;
* a **system preamble** that tells it who it is and when to reach for those
  tools instead of a shell command.

Why this shape and not "Jarvis' brain drives the turn": the person pays for
these turns through a *subscription* (Claude Code, Codex, …), and a
subscription only pays for the CLI's own agent loop. Driving the loop from
Jarvis' brain would bill per token against an API key instead. So the CLI keeps
the wheel and Jarvis lends it the hands — the model works INSIDE the Jarvis
harness while the seat stays the person's own.

This surface is deliberately additive: a CLI that cannot mount MCP servers, or
an app whose tool gateway is not up yet, simply runs as it did before. Nothing
here is allowed to make a turn fail.
"""

from __future__ import annotations

import json
import logging
from typing import Final

log = logging.getLogger(__name__)

#: Where the tool server is mounted on the app's own web server. The trailing
#: slash is load-bearing: a mounted ASGI app owns ``<prefix>/…``, and the app
#: has other routes that match the slash-less form first — a CLI pointed at it
#: gets 405, fails to connect, and the chat silently falls back to being an
#: ordinary coding session (the exact symptom, live 2026-08-24).
_MCP_PATH: Final[str] = "/api/control/mcp/"

#: The server name the CLI shows to the model. Claude Code exposes the tools as
#: ``mcp__jarvis__<tool>``; the preamble below names that prefix, so the two
#: must stay in step.
_SERVER_NAME: Final[str] = "jarvis"

SYSTEM_PREAMBLE: Final[str] = (
    "You are Jarvis — the assistant of the person you are talking to, running as "
    "the chat surface of the Personal Jarvis desktop app that is open in front of "
    "them right now. This chat is the same assistant they otherwise talk to by "
    "voice; the only difference is that here they type, and here you are expected "
    "to think longer and go deeper.\n\n"
    "You have Jarvis' own tools, offered to you over MCP under the "
    "`mcp__jarvis__` prefix. They act on the RUNNING app and on the accounts the "
    "person has connected to it — their calendar, mail, contacts, media, wiki, "
    "windows, screen, skills and Jarvis' own settings. Prefer them over a shell "
    "command, a CLI you would have to authenticate yourself, a skill, or the "
    "browser whenever the request is about the person's own apps, data or "
    "machine: those tools are already signed in and already permitted. Reach for "
    "your file and shell tools for code and for the filesystem.\n\n"
    "Some of those tools ask the person for approval before they run. That is "
    "normal and it is not a failure — wait for the answer rather than routing "
    "around it.\n\n"
    "Do not spawn background workers or sub-agents: for this turn, you are the "
    "worker."
)


#: The child environment variable the control key travels in — the SAME name
#: the app itself reads it from (``jarvis.core.control_key.ENV_VAR``), so one
#: secret has one name. It never goes on the command line: argv is readable by
#: every process on the machine, and this key is the Control API's whole
#: security boundary.
KEY_ENV_VAR: Final[str] = "JARVIS_CONTROL_API_KEY"


def endpoint() -> str | None:
    """This app's own MCP URL, or ``None`` before the web server bound."""
    from jarvis.core import runtime_refs

    base = runtime_refs.get_api_base_url()
    return f"{base.rstrip('/')}{_MCP_PATH}" if base else None


def control_key() -> str | None:
    from jarvis.core import control_key as ck

    return ck.get_control_key()


def mcp_config_json() -> str | None:
    """The Claude-shaped ``mcpServers`` config, or ``None`` when unavailable.

    ``None`` whenever anything is missing — the web server has not bound yet,
    there is no control key, the gateway is still coming up. The caller then
    spawns the CLI exactly as before instead of handing it a config that would
    fail to connect and cost the turn a confusing error.
    """
    try:
        url = endpoint()
        if not url or not control_key():
            return None
        return json.dumps(
            {
                "mcpServers": {
                    _SERVER_NAME: {
                        "type": "http",
                        "url": url,
                        # Expanded by the CLI from the child env — see KEY_ENV_VAR.
                        "headers": {"Authorization": f"Bearer ${{{KEY_ENV_VAR}}}"},
                    }
                }
            },
            ensure_ascii=False,
        )
    except Exception:  # noqa: BLE001 — no Jarvis tools is a degraded chat, never a broken one
        log.warning("agent chat: could not build the Jarvis MCP config", exc_info=True)
        return None


def codex_config_args() -> list[str]:
    """``-c`` overrides that mount the tool server in ``codex exec``.

    Codex speaks streamable HTTP MCP with a bearer token read from an
    environment variable (``codex mcp add --url --bearer-token-env-var``),
    which is the same shape used here — no config file is written, so a chat
    turn never edits the person's own ``~/.codex/config.toml``.
    """
    try:
        url = endpoint()
        if not url or not control_key():
            return []
        return [
            "-c",
            f'mcp_servers.{_SERVER_NAME}.url="{url}"',
            "-c",
            f'mcp_servers.{_SERVER_NAME}.bearer_token_env_var="{KEY_ENV_VAR}"',
        ]
    except Exception:  # noqa: BLE001 — see mcp_config_json
        log.warning("agent chat: could not build the Codex MCP overrides", exc_info=True)
        return []


def apply_env(env: dict[str, str]) -> dict[str, str]:
    """Put the control key into a child environment, if there is one to give."""
    key = control_key()
    if key:
        env[KEY_ENV_VAR] = key
    return env


def tool_count() -> int:
    """How many Jarvis tools the chat would currently be offered (0 = none)."""
    try:
        from jarvis.mcp.jarvis_tools_server import offered_tools

        return len(offered_tools())
    except Exception:  # noqa: BLE001 — a count is a nicety, never a failure
        return 0


__all__ = [
    "KEY_ENV_VAR",
    "SYSTEM_PREAMBLE",
    "apply_env",
    "codex_config_args",
    "control_key",
    "endpoint",
    "mcp_config_json",
    "tool_count",
]
