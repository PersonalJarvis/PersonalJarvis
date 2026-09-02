"""Connect the Agent MCP surface to an outside client, in one call.

Every MCP client configures servers the same two ways — a command it launches
and speaks stdio to, or a URL it calls with headers — but each keeps its config
in its own place and shape. This module knows both, for the clients people
actually use, and can either hand back a snippet to paste or write the entry
itself.

Path resolution is per-OS by capability, never by assumption: a client whose
config directory does not exist on this machine is reported as "not installed"
rather than created, because writing a config for a client that is not there
leaves a file nobody will ever read.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

log = logging.getLogger(__name__)

#: The server's name in a client's config. Kept stable — renaming it in a
#: client's file orphans the old entry rather than replacing it.
ENTRY_NAME: Final[str] = "jarvis-agents"


@dataclass(frozen=True)
class ClientTarget:
    """One MCP client: where its config lives and which shape it wants."""

    key: str
    label: str
    config_path: Path | None
    #: ``"nested"`` — servers under a top-level ``mcpServers`` object (Claude
    #: Desktop, Cursor, Claude Code). ``"toml"`` — Codex' ``config.toml``.
    shape: str
    installed: bool


def _home() -> Path:
    return Path.home()


def _claude_desktop_config() -> Path | None:
    """Claude Desktop's config, per OS. ``None`` where the app cannot run."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        return Path(appdata) / "Claude" / "claude_desktop_config.json" if appdata else None
    if sys.platform == "darwin":
        return _home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    # Linux: Claude Desktop is unofficial there, but the community builds all
    # read the XDG path, so honour it rather than pretending the client is absent.
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else _home() / ".config"
    return base / "Claude" / "claude_desktop_config.json"


def _cursor_config() -> Path:
    return _home() / ".cursor" / "mcp.json"


def _claude_code_config() -> Path:
    return _home() / ".claude.json"


def _codex_config() -> Path:
    return _home() / ".codex" / "config.toml"


def targets() -> list[ClientTarget]:
    """Every client this machine could connect, with whether it is installed."""
    rows: list[ClientTarget] = []
    desktop = _claude_desktop_config()
    rows.append(
        ClientTarget(
            key="claude-desktop",
            label="Claude Desktop",
            config_path=desktop,
            shape="nested",
            installed=bool(desktop and desktop.parent.is_dir()),
        )
    )
    cursor = _cursor_config()
    rows.append(
        ClientTarget(
            key="cursor",
            label="Cursor",
            config_path=cursor,
            shape="nested",
            installed=cursor.parent.is_dir(),
        )
    )
    code = _claude_code_config()
    rows.append(
        ClientTarget(
            key="claude-code",
            label="Claude Code",
            config_path=code,
            shape="nested",
            installed=code.exists() or (_home() / ".claude").is_dir(),
        )
    )
    codex = _codex_config()
    rows.append(
        ClientTarget(
            key="codex",
            label="Codex CLI",
            config_path=codex,
            shape="toml",
            installed=codex.parent.is_dir(),
        )
    )
    return rows


def stdio_entry(*, python: str | None = None, include_key: bool = False) -> dict[str, Any]:
    """The stdio shape: the client launches the bridge and speaks to it.

    This is the form to prefer. It needs no URL, survives Jarvis changing port,
    and works from a client that has no way to send an Authorization header.

    ``include_key`` writes the control key into the entry as an env var. Off by
    default — the bridge finds the key itself from the credential store, and a
    key in a config file is a key in a backup (AP-12).
    """
    from .bridge import api_base_url, control_key

    env: dict[str, str] = {"JARVIS_API_URL": api_base_url()}
    if include_key:
        key = control_key()
        if key:
            env["JARVIS_CONTROL_KEY"] = key
    return {
        "command": python or sys.executable,
        "args": ["-m", "jarvis.mcp.agents.bridge"],
        "env": env,
    }


def http_entry() -> dict[str, Any]:
    """The direct shape: the client calls the surface over HTTP with a header.

    Fewer moving parts, but the key is IN the config file, and the client must
    support custom headers. Use it for a remote Jarvis or a client that cannot
    launch a process.
    """
    from .bridge import SURFACE_PATH, api_base_url, control_key

    key = control_key() or "<your Jarvis control key>"
    return {
        "type": "http",
        "url": f"{api_base_url()}{SURFACE_PATH}",
        "headers": {"Authorization": f"Bearer {key}"},
    }


def snippet(client: str = "claude-desktop", *, transport: str = "stdio") -> str:
    """A ready-to-paste config block for one client."""
    entry = stdio_entry() if transport == "stdio" else http_entry()
    target = next((t for t in targets() if t.key == client), None)
    if target is not None and target.shape == "toml":
        lines = [f"[mcp_servers.{ENTRY_NAME}]"]
        if transport == "stdio":
            lines.append(f"command = {json.dumps(entry['command'])}")
            lines.append(f"args = {json.dumps(entry['args'])}")
            if entry.get("env"):
                lines.append(f"env = {json.dumps(entry['env'])}")
        else:
            lines.append(f"url = {json.dumps(entry['url'])}")
            lines.append(f"http_headers = {json.dumps(entry['headers'])}")
        return "\n".join(lines)
    return json.dumps({"mcpServers": {ENTRY_NAME: entry}}, indent=2)


def install(client: str, *, transport: str = "stdio", include_key: bool = False) -> dict[str, Any]:
    """Write the entry into the client's own config file.

    Merges rather than replaces: an existing ``mcpServers`` object keeps every
    other server, and only our named entry is rewritten. TOML clients are not
    written to — a hand-formatted ``config.toml`` is easy to corrupt and hard
    to restore, so Codex gets a snippet to paste instead.
    """
    target = next((t for t in targets() if t.key == client), None)
    if target is None:
        known = ", ".join(t.key for t in targets())
        return {"ok": False, "error": f"unknown client {client!r}. Known: {known}"}
    if target.shape == "toml":
        return {
            "ok": False,
            "error": f"{target.label} keeps a hand-formatted config — paste this instead.",
            "snippet": snippet(client, transport=transport),
            "path": str(target.config_path),
        }
    path = target.config_path
    if path is None:
        return {"ok": False, "error": f"{target.label} has no config location on this OS."}
    entry = stdio_entry(include_key=include_key) if transport == "stdio" else http_entry()
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return {"ok": False, "error": f"{path} is not readable JSON: {exc}"}
        if not isinstance(existing, dict):
            return {"ok": False, "error": f"{path} does not hold a JSON object."}
    servers = existing.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        return {"ok": False, "error": f"{path} has an mcpServers key that is not an object."}
    replaced = ENTRY_NAME in servers
    servers[ENTRY_NAME] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write through a sibling temp file: a client reading a half-written config
    # at the wrong moment loses every server it has, not just ours.
    tmp = path.with_suffix(path.suffix + ".jarvis-tmp")
    tmp.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return {
        "ok": True,
        "client": target.label,
        "path": str(path),
        "replaced": replaced,
        "transport": transport,
        "restart_required": True,
    }


__all__ = [
    "ENTRY_NAME",
    "ClientTarget",
    "http_entry",
    "install",
    "snippet",
    "stdio_entry",
    "targets",
]
