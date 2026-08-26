"""What makes a CLI seat's chat session Jarvis rather than a coding agent in a folder.

Three things are handed to the spawned CLI:

* an **MCP config** pointing at this app's own tool server
  (``jarvis.ui.web.mcp_server_routes``), so the model's hands are Jarvis' hands
  — the same catalog the voice path calls, through the same safety gateway.
  The config carries the chat's session id in a header, so the tool server can
  route an approval the executor asks for back to THIS chat's card;
* an **identity** — Jarvis' real system-prompt layers (soul, persona, the
  assistant file, the user's profile, the identity card, core memory, wiki
  context), the chat's own transcript on a fresh conversation, and the surface
  addendum below that says which hands are whose;
* a **system preamble** (the addendum) that tells it who it is and when to
  reach for those tools instead of a shell command.

Why this shape and not "Jarvis' brain drives the turn": the person pays for
these turns through a *subscription* (Claude Code, Codex, …), and a
subscription only pays for the CLI's own agent loop. Driving the loop from
Jarvis' brain would bill per token against an API key instead. So the CLI keeps
the wheel and Jarvis lends it the hands AND the head — the model works INSIDE
the Jarvis harness while the seat stays the person's own, and only the vendor's
official binary ever spends the subscription (maintainer, 2026-08-25).

This surface is deliberately additive: a CLI that cannot mount MCP servers, an
app whose tool gateway is not up yet, or a brain that has not finished building
simply runs with less. Nothing here is allowed to make a turn fail.

Currently unreachable, on purpose. No surface seats a CLI as Jarvis since the
front page's chat went API-only (2026-08-26, ``SurfaceKit.cli_seats``), so
``run_cli_turn(identity=…)`` is False on every turn today. The service asks the
kit — ``brain_runner and cli_seats`` — rather than a surface name, so a surface
that wants both picks this up without a change here. Keep it working.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

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

#: The request header that carries the chat's session id to the tool server.
#: ``jarvis.mcp.jarvis_tools_server`` reads it (through
#: ``jarvis.ui.web.mcp_server_routes``) and stamps the executor's approval
#: surface with it, so a gate the CLI reaches over MCP is answered by THIS
#: chat's card. Any other client — or a config without it — gets the
#: unattended surface, exactly as before.
HEADER_NAME: Final[str] = "X-Jarvis-Chat-Session"

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
    "Two kinds of hands: your own file and shell tools are your hands in the "
    "working folder this chat is open in; `mcp__jarvis__run_shell` and its "
    "siblings are Jarvis' hands on the whole machine. Use the folder's hands for "
    "the folder, Jarvis' for everything else.\n\n"
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

#: How much of the chat's transcript rides along on a fresh conversation.
TRANSCRIPT_MAX_CHARS: Final[int] = 20_000

#: A compact identity for a CLI whose prompt travels on argv (Grok Build):
#: Windows caps a command line at 32 767 characters.
COMPACT_MAX_CHARS: Final[int] = 16_000


def endpoint() -> str | None:
    """This app's own MCP URL, or ``None`` before the web server bound."""
    from jarvis.core import runtime_refs

    base = runtime_refs.get_api_base_url()
    return f"{base.rstrip('/')}{_MCP_PATH}" if base else None


def control_key() -> str | None:
    from jarvis.core import control_key as ck

    return ck.get_control_key()


def mcp_config_json(session_id: str | None = None) -> str | None:
    """The Claude-shaped ``mcpServers`` config, or ``None`` when unavailable.

    ``None`` whenever anything is missing — the web server has not bound yet,
    there is no control key, the gateway is still coming up. The caller then
    spawns the CLI exactly as before instead of handing it a config that would
    fail to connect and cost the turn a confusing error. With ``session_id``
    the config also carries the chat's session header (see ``HEADER_NAME``).
    """
    try:
        url = endpoint()
        if not url or not control_key():
            return None
        headers: dict[str, str] = {
            # Expanded by the CLI from the child env — see KEY_ENV_VAR.
            "Authorization": f"Bearer ${{{KEY_ENV_VAR}}}",
        }
        if session_id:
            headers[HEADER_NAME] = session_id
        return json.dumps(
            {"mcpServers": {_SERVER_NAME: {"type": "http", "url": url, "headers": headers}}},
            ensure_ascii=False,
        )
    except Exception:  # noqa: BLE001 — no Jarvis tools is a degraded chat, never a broken one
        log.warning("agent chat: could not build the Jarvis MCP config", exc_info=True)
        return None


def codex_config_args(session_id: str | None = None) -> list[str]:
    """``-c`` overrides that mount the tool server in ``codex exec``.

    Codex speaks streamable HTTP MCP with a bearer token read from an
    environment variable (``codex mcp add --url --bearer-token-env-var``),
    which is the same shape used here — no config file is written, so a chat
    turn never edits the person's own ``~/.codex/config.toml``. With
    ``session_id`` the chat's session header rides along as an
    ``http_headers`` inline table.
    """
    try:
        url = endpoint()
        if not url or not control_key():
            return []
        args = [
            "-c",
            f'mcp_servers.{_SERVER_NAME}.url="{url}"',
            "-c",
            f'mcp_servers.{_SERVER_NAME}.bearer_token_env_var="{KEY_ENV_VAR}"',
        ]
        if session_id:
            args += [
                "-c",
                f'mcp_servers.{_SERVER_NAME}.http_headers={{"{HEADER_NAME}"="{session_id}"}}',
            ]
        return args
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


# ------------------------------------------------------------------ identity


@dataclass(slots=True)
class Identity:
    """What a CLI seat is told it is, this turn.

    ``text`` is the full identity (Jarvis' layers, the transcript, the
    addendum); ``compact`` a shorter cut for a CLI whose prompt travels on
    argv; ``path`` the file the full text was written to for a CLI that takes
    a system-prompt file (Claude Code), removed by the runner afterwards.
    """

    session_id: str
    text: str
    compact: str
    path: Path | None = None


def _brain() -> Any | None:
    from jarvis.core import runtime_refs

    return runtime_refs.get_brain_manager()


async def identity_prompt(
    *,
    user_text: str,
    history: list[dict[str, Any]],
    resume: str | None,
) -> str:
    """Jarvis' real prompt layers + the chat's transcript + the addendum.

    The layers come from the live brain (``BrainManager.render_surface_prompt``:
    soul, persona, the assistant file, the user's profile, contacts, the
    identity card, core memory, the wiki context for ``user_text``, and the
    turn context). Without a brain — before it finished building, or on a
    box that runs the chat alone — the addendum stands in, as it did before:
    nothing here may fail a turn.

    The transcript rides along on a FRESH conversation only: a CLI that
    resumes its own session already carries it, and repeating it there would
    tell the model the same story twice.
    """
    parts: list[str] = []
    brain = _brain()
    render = getattr(brain, "render_surface_prompt", None) if brain is not None else None
    if callable(render):
        try:
            system_prompt, turn_context = await render(user_text=user_text)
            if system_prompt.strip():
                parts.append(system_prompt.strip())
            if turn_context.strip():
                parts.append(turn_context.strip())
        except Exception:  # noqa: BLE001 — the addendum alone is still a working chat
            log.warning("agent chat: Jarvis' prompt layers unavailable this turn", exc_info=True)
    if resume is None and history:
        transcript = render_transcript(history)
        if transcript:
            parts.append("## This conversation so far\n\n" + transcript)
    parts.append(SYSTEM_PREAMBLE)
    return "\n\n".join(parts)


def render_transcript(
    history: list[dict[str, Any]], *, max_chars: int = TRANSCRIPT_MAX_CHARS
) -> str:
    """The chat's log as plain prose, newest last, cut to ``max_chars`` from the front."""
    from jarvis.agent_chat.runner_api import messages_from_events

    lines: list[str] = []
    for message in messages_from_events(history):
        if message.role == "user":
            text = message.content if isinstance(message.content, str) else ""
            if text.strip():
                lines.append(f"Person: {text.strip()}")
        elif message.role == "assistant":
            text = _prose(message.content)
            if text.strip():
                lines.append(f"Jarvis: {text.strip()}")
    out = "\n\n".join(lines)
    if len(out) > max_chars:
        out = "…" + out[-max_chars:]
    return out


def _prose(content: str | list[dict[str, Any]]) -> str:
    if isinstance(content, str):
        return content
    bits: list[str] = []
    for block in content:
        if block.get("type") == "text":
            bits.append(str(block.get("text") or ""))
        elif block.get("type") == "tool_use":
            bits.append(f"[used tool {block.get('name') or 'tool'}]")
    return "\n".join(b for b in bits if b.strip())


def compact_identity(text: str, *, max_chars: int = COMPACT_MAX_CHARS) -> str:
    """The identity cut for argv: the addendum is kept whole, the rest trimmed."""
    if len(text) <= max_chars:
        return text
    head_room = max(0, max_chars - len(SYSTEM_PREAMBLE) - 8)
    body = text[: -len(SYSTEM_PREAMBLE)] if text.endswith(SYSTEM_PREAMBLE) else text
    body = body[:head_room].rstrip()
    body = re.sub(r"\n[^\n]*$", "", body)  # do not cut a line in half
    return f"{body}\n\n…\n\n{SYSTEM_PREAMBLE}"


def identity_dir() -> Path:
    """Where identity files live: the app's own data dir, never a shared temp."""
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / "agent_chat" / "identity"


def write_identity_file(text: str, *, turn_id: str) -> Path:
    """Write the identity for a CLI that takes a system-prompt FILE.

    Claude Code's ``--append-system-prompt-file`` exists because argv cannot
    carry a prompt this size on Windows (32 767 characters for the whole
    command line; Jarvis' layers alone run past 50 000). The runner removes
    the file when the turn ends.
    """
    folder = identity_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{turn_id or uuid.uuid4().hex}.md"
    path.write_text(text, encoding="utf-8")
    return path


def remove_identity_file(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.debug("agent chat: identity file %s not removed", path, exc_info=True)


async def build_identity(
    *,
    session_id: str,
    turn_id: str,
    user_text: str,
    history: list[dict[str, Any]],
    resume: str | None,
    with_file: bool,
) -> Identity:
    """The identity for one CLI turn; ``with_file`` for a CLI that takes a file."""
    text = await identity_prompt(user_text=user_text, history=history, resume=resume)
    path = write_identity_file(text, turn_id=turn_id) if with_file else None
    return Identity(session_id=session_id, text=text, compact=compact_identity(text), path=path)


__all__ = [
    "COMPACT_MAX_CHARS",
    "HEADER_NAME",
    "KEY_ENV_VAR",
    "SYSTEM_PREAMBLE",
    "TRANSCRIPT_MAX_CHARS",
    "Identity",
    "apply_env",
    "build_identity",
    "codex_config_args",
    "compact_identity",
    "control_key",
    "endpoint",
    "identity_dir",
    "identity_prompt",
    "mcp_config_json",
    "remove_identity_file",
    "render_transcript",
    "tool_count",
    "write_identity_file",
]
