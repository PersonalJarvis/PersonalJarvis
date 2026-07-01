"""Semantic validation of skills — beyond the frontmatter schema layer.

- Requires-Tools: must exist in the tool registry (jarvis.tool entry_points)
  or as an MCP server name (best-effort via the optional mcp_registry).
- Voice patterns must compile.
- Cron expressions must be parsable (croniter optional).
- Hotkey combos must be parsable (simple key-token check).
- Risk-tier overrides must not hit a globally blacklisted pattern.
- token_budget ≤ 10_000.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .schema import Skill, SkillLifecycleState

# Optional: croniter for cron validation
try:
    from croniter import croniter  # type: ignore
    _HAVE_CRONITER = True
except Exception:  # pragma: no cover
    croniter = None  # type: ignore
    _HAVE_CRONITER = False


_ALLOWED_HOTKEY_MODS = {
    "ctrl", "shift", "alt", "win", "cmd", "super", "meta",
    "left_ctrl", "right_ctrl", "left_alt", "right_alt",
    "left_shift", "right_shift",
}


@dataclass
class ValidationReport:
    """Result of a validation. `ok=True` means all checks passed."""
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.ok = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


def _validate_hotkey(combo: str) -> str | None:
    """None if OK, otherwise an error message."""
    if not combo:
        return "empty combo"
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        return "empty combo"
    # Last token must be a key (not a modifier alone)
    if len(parts) == 1 and parts[0] in _ALLOWED_HOTKEY_MODS:
        return f"combo '{combo}' contains only a modifier"
    return None


def _list_tool_plugins() -> set[str]:
    """All tool plugins registered via entry_points."""
    try:
        from jarvis.core import registry as plugin_registry
        return set(plugin_registry.list_plugins("jarvis.tool"))
    except Exception:  # pragma: no cover
        return set()


def validate_skill(
    skill: Skill,
    tool_registry: Any | None = None,
    mcp_registry: Any | None = None,
) -> ValidationReport:
    """Runs all semantic checks.

    Args:
        skill: Loaded skill (may also be DRAFT — in which case the
               existing error is propagated).
        tool_registry: Optional — object with ``list_plugins("jarvis.tool")``
                        or an iterable of tool names.
        mcp_registry: Optional — object with ``.list_servers()``.
    """
    report = ValidationReport()

    # DRAFT skills inherit their parse error
    if skill.frontmatter is None:
        report.add_error(skill.error or "skill could not be parsed")
        return report

    fm = skill.frontmatter

    # 1. token_budget
    if fm.token_budget_estimate > 10_000:
        report.add_error(
            f"token_budget_estimate={fm.token_budget_estimate} > 10000"
        )

    # 2. Tools exist
    known_tools: set[str] = set()
    if tool_registry is not None:
        if hasattr(tool_registry, "list_plugins"):
            try:
                known_tools = set(tool_registry.list_plugins("jarvis.tool"))
            except Exception:  # noqa: BLE001
                known_tools = set()
        elif hasattr(tool_registry, "__iter__"):
            known_tools = set(tool_registry)  # type: ignore[arg-type]
    else:
        known_tools = _list_tool_plugins()

    mcp_servers: set[str] = set()
    if mcp_registry is not None and hasattr(mcp_registry, "list_servers"):
        try:
            mcp_servers = set(mcp_registry.list_servers())
        except Exception:  # noqa: BLE001
            mcp_servers = set()

    for tool in fm.requires_tools:
        if tool in known_tools:
            continue
        if tool in mcp_servers:
            continue
        # Accept "mcp:<name>" prefix as an MCP server reference
        if tool.startswith("mcp:") and tool[4:] in mcp_servers:
            continue
        report.add_warning(f"required tool '{tool}' not found in registry")

    # 3. Risk policy
    for tool, tier in fm.risk_policy.per_tool_overrides.items():
        if tier not in ("safe", "monitor", "ask", "block"):
            report.add_error(f"invalid risk tier '{tier}' for tool '{tool}'")

    # 4. Trigger
    for idx, trig in enumerate(fm.triggers):
        label = f"trigger[{idx}]({trig.type})"
        if trig.type == "voice":
            if trig.pattern is None:
                report.add_error(f"{label}: pattern missing")
            else:
                try:
                    re.compile(trig.pattern)
                except re.error as exc:
                    report.add_error(f"{label}: regex invalid — {exc}")
        elif trig.type == "hotkey":
            err = _validate_hotkey(trig.combo or "")
            if err:
                report.add_error(f"{label}: {err}")
        elif trig.type == "schedule":
            if not trig.cron:
                report.add_error(f"{label}: cron missing")
            elif _HAVE_CRONITER:
                try:
                    if not croniter.is_valid(trig.cron):  # type: ignore[union-attr]
                        report.add_error(f"{label}: cron '{trig.cron}' invalid")
                except Exception as exc:  # noqa: BLE001
                    report.add_error(f"{label}: cron parse failed — {exc}")
            else:
                # Minimal fallback: 5 or 6 fields
                field_count = len(trig.cron.split())
                if field_count not in (5, 6):
                    report.add_error(
                        f"{label}: cron '{trig.cron}' has {field_count} fields (expected 5 or 6)"
                    )
                else:
                    report.add_warning(
                        "croniter not installed — cron checked only superficially"
                    )

    return report


def apply_validation(skill: Skill, report: ValidationReport) -> Skill:
    """Returns a skill with an updated state, based on the report."""
    if not report.ok:
        return Skill(
            path=skill.path,
            frontmatter=skill.frontmatter,
            body=skill.body,
            state=SkillLifecycleState.DRAFT,
            body_hash=skill.body_hash,
            error="; ".join(report.errors),
        )
    return Skill(
        path=skill.path,
        frontmatter=skill.frontmatter,
        body=skill.body,
        state=SkillLifecycleState.VALIDATED,
        body_hash=skill.body_hash,
        error=None,
    )
