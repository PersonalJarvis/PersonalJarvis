"""``describe-app-settings`` tool — the brain's complete view of the Desktop App.

Router-tier, read-only, ``safe``. Returns a single structured, secret-free
snapshot of the running configuration: which provider is active per tier (and
which are configured), the key user settings, and the list of MCP servers.

This is the "Jarvis has a complete overview of the Desktop App" capability. The
brain calls it when the user asks things like "which provider am I on?", "what
are my settings?", "which MCP servers are connected?", or before proposing /
confirming any change with ``switch-provider`` or ``manage-mcp-server``.

Never returns a secret value — only ``configured: true/false`` booleans.
"""
from __future__ import annotations

import logging
from typing import Any, ClassVar

from jarvis.core.protocols import ExecutionContext, ToolResult

log = logging.getLogger(__name__)


class DescribeAppSettingsTool:
    """Read-only overview of the live Desktop App configuration."""

    name: ClassVar[str] = "describe-app-settings"
    risk_tier: ClassVar[str] = "safe"
    description: ClassVar[str] = (
        "Get a complete, current overview of the Jarvis Desktop App configuration: "
        "the active brain/TTS/STT/subagent provider (and which providers have a "
        "stored API key), the key settings (wake word, assistant name, autostart, "
        "reply language, UI theme, TTS voice/speed), and the list of configured MCP "
        "servers. Use this whenever the user asks what the current settings are, "
        "which provider is active, which MCPs are connected, or before you propose "
        "or confirm any settings change. Read-only — never exposes secret values."
    )
    schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
        "input_examples": [{}],
    }

    async def execute(
        self, args: dict[str, Any], ctx: ExecutionContext
    ) -> ToolResult:  # noqa: ARG002 — ctx required by the tool protocol
        if not isinstance(args, dict) or args:
            return ToolResult(
                success=False,
                output=None,
                error="invalid_input: describe-app-settings takes no parameters",
            )
        try:
            from jarvis.brain.app_control import build_settings_snapshot, resolve_running_cfg

            snapshot = build_settings_snapshot(resolve_running_cfg())
        except Exception as exc:  # noqa: BLE001
            log.warning("describe-app-settings failed: %s", exc, exc_info=True)
            return ToolResult(
                success=False,
                output=None,
                error=f"could not read app settings: {type(exc).__name__}: {exc}",
            )
        return ToolResult(success=True, output=snapshot)
