"""Render the CONNECTED CLIS system-prompt section.

Design §5.3 (docs/superpowers/specs/2026-06-10-cli-first-class-capabilities-
design.md): only connected/usable CLIs appear; the section is "" when none
are. Mirrors render_available_skills_section (jarvis/skills/prompt_injection.py):
static per connect/disconnect, cheap to render, defensive against any registry
fault — the system-prompt build must never crash.
"""

from __future__ import annotations

import logging
from typing import Any

from jarvis.clis.tool import TOOL_NAME_PREFIX

log = logging.getLogger(__name__)

_HEADER = (
    "CONNECTED CLIS\n"
    "You have direct command-line tools for these connected services. Prefer "
    "them for matching requests instead of refusing, spawning a worker, or "
    "using an equivalent plugin — these CLIs are faster and cheaper, and a "
    "plugin is only a fallback when no CLI covers the task:\n"
)
_FOOTER = (
    "\nAnswer ONLY from the tool result — never invent external data. Prefer "
    "machine-readable output flags (--json, --format json) when the CLI "
    "supports them. If you are unsure of the exact command or flags, first run "
    "`<cli> --help` or `<cli> <group> --help` (read-only) to discover them, "
    "then issue the real command."
)


def render_connected_clis_section(cli_registry: Any) -> str:
    try:
        active = {t.name for t in cli_registry.active_tools()}
        if not active:
            return ""
        lines: list[str] = []
        for spec in cli_registry.catalog().all().values():
            tool_name = f"{TOOL_NAME_PREFIX}{spec.name}"
            if tool_name not in active:
                continue
            if spec.capabilities:
                summary = " ".join(dict.fromkeys(d.description for d in spec.capabilities))
            else:
                summary = spec.description
            line = f"• {tool_name} — {spec.display_name}: {summary}"
            examples = ", ".join(f"`{e}`" for e in spec.tool_schema_examples[:2])
            if examples:
                line += f" (e.g. {examples})"
            lines.append(line)
        if not lines:
            return ""
        return _HEADER + "\n".join(lines) + _FOOTER
    except Exception:  # noqa: BLE001 — prompt build must never crash
        log.debug("connected-CLIs section render failed", exc_info=True)
        return ""


__all__ = ["render_connected_clis_section"]
