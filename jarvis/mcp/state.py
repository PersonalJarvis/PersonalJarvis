"""MCP config: mcp.json as the primary user-editable source.

The file lives in the project root as ``mcp.json`` and follows the
Claude Desktop format, extended with ``enabled`` and ``description``
per server::

    {
      "mcpServers": {
        "filesystem": {
          "command": "uvx",
          "args": ["mcp-server-filesystem", "--root", "{PROJECT_ROOT}"],
          "env": {},
          "enabled": true,
          "description": "Sicherer Datei-Zugriff"
        }
      }
    }

The user can edit the file directly — UI and file are bidirectionally
synchronised: UI toggles write ``enabled=true/false``, and adding a
custom server via the UI produces a new entry in the ``mcpServers`` dict.

The bootstrap specs from :mod:`jarvis.mcp.registry` remain the code
defaults — mcp.json entries override them when the name matches.

Compatibility: the older ``data/mcp_state.json`` is migrated into
``mcp.json`` once on first load and is not touched again afterwards.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from threading import RLock
from typing import Any

from jarvis.core.config import DATA_DIR, PROJECT_ROOT

log = logging.getLogger(__name__)

# Primary user config
MCP_JSON_PATH = PROJECT_ROOT / "mcp.json"

# Legacy state file (for migration)
LEGACY_STATE_PATH = DATA_DIR / "mcp_state.json"

_lock = RLock()


# ----------------------------------------------------------------------
# Low-level file-IO
# ----------------------------------------------------------------------

def _empty_config() -> dict[str, Any]:
    return {"mcpServers": {}}


def _read_mcp_json() -> dict[str, Any]:
    if not MCP_JSON_PATH.exists():
        return _empty_config()
    try:
        data = json.loads(MCP_JSON_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("mcp.json not readable (%s) — using default", exc)
        return _empty_config()
    if not isinstance(data, dict):
        return _empty_config()
    data.setdefault("mcpServers", {})
    return data


def _write_mcp_json(data: dict[str, Any]) -> None:
    with _lock:
        MCP_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = MCP_JSON_PATH.with_suffix(MCP_JSON_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, MCP_JSON_PATH)


# ----------------------------------------------------------------------
# Migration from legacy data/mcp_state.json
# ----------------------------------------------------------------------

def _migrate_legacy_if_needed() -> None:
    """Read the old ``data/mcp_state.json`` once and transfer it into
    ``mcp.json``. The old file is then renamed to ``.bak``.
    """
    if MCP_JSON_PATH.exists() or not LEGACY_STATE_PATH.exists():
        return
    try:
        raw = json.loads(LEGACY_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    enabled = set(raw.get("enabled", []) or [])
    custom = raw.get("custom", {}) or {}

    cfg = _empty_config()
    # Import custom servers
    for name, spec in custom.items():
        if not isinstance(spec, dict):
            continue
        install = list(spec.get("install_command") or [])
        if not install:
            continue
        cfg["mcpServers"][name] = {
            "command": install[0],
            "args": install[1:],
            "env": {},
            "enabled": name in enabled,
            "description": spec.get("description", ""),
        }

    # Register bootstrap enables as empty overrides (only "enabled": true)
    for name in enabled:
        cfg["mcpServers"].setdefault(name, {"enabled": True})

    _write_mcp_json(cfg)
    try:
        LEGACY_STATE_PATH.rename(LEGACY_STATE_PATH.with_suffix(".json.bak"))
    except OSError:
        pass
    log.info("mcp.json migrated from legacy state (%d servers)", len(cfg["mcpServers"]))


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------

def load_config() -> dict[str, Any]:
    """Read mcp.json (with migration). Returns the root dict."""
    _migrate_legacy_if_needed()
    return _read_mcp_json()


def save_config(cfg: dict[str, Any]) -> None:
    """Write the complete root dict atomically to mcp.json."""
    with _lock:
        _write_mcp_json(cfg)


def get_enabled_names() -> list[str]:
    """Return the list of server names with ``enabled=true``."""
    cfg = load_config()
    return [
        name
        for name, entry in cfg.get("mcpServers", {}).items()
        if isinstance(entry, dict) and entry.get("enabled", False)
    ]


def get_server_entry(name: str) -> dict[str, Any] | None:
    """A single server entry from mcp.json (or ``None``)."""
    cfg = load_config()
    entry = cfg.get("mcpServers", {}).get(name)
    return dict(entry) if isinstance(entry, dict) else None


def set_enabled(name: str, enabled: bool) -> None:
    """Set ``enabled`` for a server. Creates the entry if it does not exist."""
    with _lock:
        cfg = load_config()
        servers = cfg.setdefault("mcpServers", {})
        entry = servers.get(name)
        if not isinstance(entry, dict):
            entry = {}
        entry["enabled"] = bool(enabled)
        servers[name] = entry
        _write_mcp_json(cfg)


def upsert_server(name: str, spec: dict[str, Any]) -> None:
    """Add a new server or update an existing one.

    ``spec`` has the fields ``command``, ``args``, ``env``, and optionally
    ``enabled`` and ``description``.
    """
    with _lock:
        cfg = load_config()
        servers = cfg.setdefault("mcpServers", {})
        existing = servers.get(name) if isinstance(servers.get(name), dict) else {}
        merged = {**existing, **spec}
        servers[name] = merged
        _write_mcp_json(cfg)


def remove_server(name: str) -> bool:
    """Remove a server from mcp.json. Returns True if it was removed."""
    with _lock:
        cfg = load_config()
        servers = cfg.setdefault("mcpServers", {})
        if name not in servers:
            return False
        del servers[name]
        _write_mcp_json(cfg)
        return True


# ----------------------------------------------------------------------
# Claude Desktop import
# ----------------------------------------------------------------------

def import_claude_desktop() -> tuple[int, list[str], str]:
    """Import ``mcpServers`` from the Claude Desktop config into our mcp.json.

    Path: ``%APPDATA%/Claude/claude_desktop_config.json`` (Windows).
    Existing entries with the same name are NOT overwritten — the user
    should resolve merge conflicts manually. New entries are created with
    ``enabled=false`` (safe default).
    """
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return (0, [], "APPDATA variable not set.")
    src = Path(appdata) / "Claude" / "claude_desktop_config.json"
    if not src.exists():
        return (
            0,
            [],
            f"Claude Desktop config not found at {src}.",
        )

    try:
        raw = json.loads(src.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return (0, [], f"Config not readable: {exc}")

    claude_servers = raw.get("mcpServers", {})
    if not isinstance(claude_servers, dict) or not claude_servers:
        return (0, [], "No mcpServers found in the Claude Desktop config.")

    added: list[str] = []
    skipped: list[str] = []
    with _lock:
        cfg = load_config()
        servers = cfg.setdefault("mcpServers", {})
        for name, entry in claude_servers.items():
            if not isinstance(entry, dict):
                continue
            command = entry.get("command")
            if not command:
                continue
            if name in servers:
                skipped.append(name)
                continue
            servers[name] = {
                "command": command,
                "args": list(entry.get("args", [])),
                "env": dict(entry.get("env", {})),
                "enabled": False,
                "description": "Imported from Claude Desktop.",
            }
            added.append(name)
        _write_mcp_json(cfg)

    note = f"{len(added)} new servers imported"
    if skipped:
        note += f", {len(skipped)} skipped (already exist)"
    return (len(added), added, note + ".")


# ----------------------------------------------------------------------
# Legacy-compatible helpers (the old API is still used by mcp_routes)
# ----------------------------------------------------------------------

def load_state() -> dict[str, Any]:
    """Legacy wrapper: reads mcp.json and returns the old
    ``{"enabled": [], "custom": {}}`` schema. Still used by older routes.
    """
    cfg = load_config()
    enabled: list[str] = []
    custom: dict[str, Any] = {}
    for name, entry in cfg.get("mcpServers", {}).items():
        if not isinstance(entry, dict):
            continue
        if entry.get("enabled"):
            enabled.append(name)
        # Custom = everything that contains a command (own specs, not pure enabled overrides)
        if "command" in entry:
            custom[name] = {
                "name": name,
                "display": name.replace("-", " ").title(),
                "description": entry.get("description", ""),
                "install_command": [entry["command"], *entry.get("args", [])],
                "required_auth": list(entry.get("required_auth", [])),
                "transport": entry.get("transport", "stdio"),
                "mandatory": False,
                "platform_notes": entry.get("platform_notes", ""),
            }
    return {"enabled": enabled, "custom": custom}


def enable(name: str) -> None:
    """Legacy alias for set_enabled(name, True)."""
    set_enabled(name, True)


def disable(name: str) -> None:
    """Legacy alias for set_enabled(name, False)."""
    set_enabled(name, False)


def add_custom(name: str, spec_dict: dict[str, Any]) -> None:
    """Legacy alias: maps the old spec schema to the mcp.json format."""
    install = list(spec_dict.get("install_command") or [])
    if not install:
        return
    upsert_server(
        name,
        {
            "command": install[0],
            "args": install[1:],
            "env": {},
            "enabled": False,
            "description": spec_dict.get("description", ""),
        },
    )


def remove_custom(name: str) -> None:
    """Legacy alias for remove_server."""
    remove_server(name)
