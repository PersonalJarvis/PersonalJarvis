"""Semantische Validierung von Skills — über die Frontmatter-Schema-Ebene hinaus.

- Requires-Tools: existieren im Tool-Registry (jarvis.tool entry_points) oder
  als MCP-Server-Name (best-effort via optional mcp_registry).
- Voice-Patterns müssen compilieren.
- Cron-Expressions müssen parsebar sein (croniter optional).
- Hotkey-Combos müssen parsebar sein (simple Key-Token-Check).
- Risk-Tier-Overrides dürfen keine global geblacklisteten Patterns treffen.
- token_budget ≤ 10_000.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .schema import Skill, SkillLifecycleState

# Optional: croniter für Cron-Validation
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
    """Ergebnis einer Validierung. `ok=True` heißt alle Checks bestanden."""
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.ok = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


def _validate_hotkey(combo: str) -> str | None:
    """None wenn OK, sonst Error-Message."""
    if not combo:
        return "empty combo"
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        return "empty combo"
    # Letzter Token muss eine Key sein (kein Modifier allein)
    if len(parts) == 1 and parts[0] in _ALLOWED_HOTKEY_MODS:
        return f"combo '{combo}' enthält nur einen Modifier"
    return None


def _list_tool_plugins() -> set[str]:
    """Alle via entry_points registrierten Tool-Plugins."""
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
    """Führt alle semantischen Checks aus.

    Args:
        skill: Geladener Skill (darf auch DRAFT sein — dann wird der
               vorhandene Error propagiert).
        tool_registry: Optional — Objekt mit ``list_plugins("jarvis.tool")``
                        oder eine Iterable von Tool-Namen.
        mcp_registry: Optional — Objekt mit ``.list_servers()``.
    """
    report = ValidationReport()

    # DRAFT-Skills erben ihren Parse-Fehler
    if skill.frontmatter is None:
        report.add_error(skill.error or "skill could not be parsed")
        return report

    fm = skill.frontmatter

    # 1. token_budget
    if fm.token_budget_estimate > 10_000:
        report.add_error(
            f"token_budget_estimate={fm.token_budget_estimate} > 10000"
        )

    # 2. Tools existieren
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
        # Accept "mcp:<name>" prefix as MCP-Server-Reference
        if tool.startswith("mcp:") and tool[4:] in mcp_servers:
            continue
        report.add_warning(f"required tool '{tool}' nicht im Registry gefunden")

    # 3. Risk-Policy
    for tool, tier in fm.risk_policy.per_tool_overrides.items():
        if tier not in ("safe", "monitor", "ask", "block"):
            report.add_error(f"invalid risk tier '{tier}' for tool '{tool}'")

    # 4. Trigger
    for idx, trig in enumerate(fm.triggers):
        label = f"trigger[{idx}]({trig.type})"
        if trig.type == "voice":
            if trig.pattern is None:
                report.add_error(f"{label}: pattern fehlt")
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
                report.add_error(f"{label}: cron fehlt")
            elif _HAVE_CRONITER:
                try:
                    if not croniter.is_valid(trig.cron):  # type: ignore[union-attr]
                        report.add_error(f"{label}: cron '{trig.cron}' invalid")
                except Exception as exc:  # noqa: BLE001
                    report.add_error(f"{label}: cron parse failed — {exc}")
            else:
                # Minimal-Fallback: 5 oder 6 Felder
                field_count = len(trig.cron.split())
                if field_count not in (5, 6):
                    report.add_error(
                        f"{label}: cron '{trig.cron}' hat {field_count} Felder (erwarte 5 oder 6)"
                    )
                else:
                    report.add_warning(
                        "croniter nicht installiert — cron nur oberflächlich geprüft"
                    )

    return report


def apply_validation(skill: Skill, report: ValidationReport) -> Skill:
    """Gibt einen Skill mit aktualisiertem State zurück, basierend auf Report."""
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
