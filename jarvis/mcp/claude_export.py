"""Export Jarvis ``mcp.json`` entries to the claude-cli ``mcpServers`` shape.

This is the bridge that lets a self-added MCP server (the "MCPs" sidebar /
``mcp.json``) reach the delegated worker, exactly like a Marketplace plugin.
The worker runs the ``claude`` CLI, which speaks the standard mcpServers config:

* stdio  -> ``{"command", "args", "env"}``
* http   -> ``{"type": "http", "url", "headers"}``
* sse    -> ``{"type": "sse",  "url", "headers"}``

Jarvis-only keys (``enabled``, ``description``, ``transport``, ``required_auth``,
``platform_notes``) are dropped. Two Jarvis-isms are resolved that claude-cli
does not understand on its own: ``$SECRET`` env references (via the credential
store) and ``{PROJECT_ROOT}`` / ``{DATA_DIR}`` templates in command/args.

Enabled rule mirrors :func:`jarvis.mcp.state.get_enabled_names`: a missing
``enabled`` flag means DISABLED.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)


def _default_resolver(key: str) -> str | None:
    from jarvis.core.config import get_secret  # lazy: keep module import-light

    return get_secret(key, env_fallback=key)


def _default_project_root() -> str:
    try:
        from jarvis.core.config import PROJECT_ROOT

        return str(PROJECT_ROOT)
    except Exception:  # noqa: BLE001
        return ""


def _default_data_dir() -> str:
    try:
        from jarvis.core.config import DATA_DIR

        return str(DATA_DIR)
    except Exception:  # noqa: BLE001
        return ""


def mcp_json_to_claude_servers(
    servers: dict[str, Any] | None,
    *,
    secret_resolver: Callable[[str], str | None] | None = None,
    project_root: str | None = None,
    data_dir: str | None = None,
) -> dict[str, dict]:
    """Convert enabled mcp.json entries to a claude-cli ``mcpServers`` map."""
    resolver = secret_resolver or _default_resolver
    proot = str(project_root) if project_root is not None else _default_project_root()
    ddir = str(data_dir) if data_dir is not None else _default_data_dir()

    def _tmpl(value: str) -> str:
        return value.replace("{PROJECT_ROOT}", proot).replace("{DATA_DIR}", ddir)

    out: dict[str, dict] = {}
    for name, entry in (servers or {}).items():
        if not isinstance(entry, dict) or not entry.get("enabled", False):
            continue  # mirror get_enabled_names: missing/false -> skip

        command = entry.get("command")
        url = entry.get("url")

        if command:
            converted: dict[str, Any] = {
                "command": _tmpl(str(command)),
                "args": [_tmpl(str(a)) for a in (entry.get("args") or [])],
            }
            env: dict[str, str] = {}
            for key, val in (entry.get("env") or {}).items():
                if not isinstance(val, str):
                    continue
                if val.startswith("$"):
                    resolved = resolver(val[1:])
                    if resolved:
                        env[str(key)] = resolved
                    else:
                        log.warning(
                            "mcp.json[%s].env[%s]=%s not resolvable — dropped",
                            name, key, val,
                        )
                else:
                    env[str(key)] = _tmpl(val)
            if env:
                converted["env"] = env
            out[name] = converted

        elif url:
            transport = str(entry.get("transport") or "http").lower()
            converted = {
                "type": "sse" if transport == "sse" else "http",
                "url": _tmpl(str(url)),
            }
            headers = entry.get("headers")
            if isinstance(headers, dict):
                converted["headers"] = {
                    str(k): _tmpl(str(v)) for k, v in headers.items()
                }
            out[name] = converted
        # else: neither command nor url -> not runnable -> skip

    return out


__all__ = ["mcp_json_to_claude_servers"]
