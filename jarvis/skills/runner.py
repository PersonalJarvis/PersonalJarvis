"""SkillRunner: rendert Body (Jinja2-Sandbox) + führt Tool-Zeilen aus.

Body-Konvention:
    Normaler Markdown-/Prose-Content.

    TOOL: <tool-name> {"arg": "value"}
    TOOL: other_tool {"k": 1}

Zeilen, die mit `TOOL:` beginnen, werden als Tool-Calls extrahiert
(nach dem Jinja-Rendering). Alles andere ist Prosa für LLM-Kontext.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class _NowProxy:
    """Jinja-friendly Wrapper um den aktuellen Zeitpunkt."""

    iso: str
    epoch_ms: int
    epoch_s: int


@dataclass(frozen=True)
class _DateProxy:
    """Jinja-friendly Wrapper um das aktuelle Datum."""

    iso: str
    year: int
    month: int
    day: int


def _build_time_context() -> dict[str, Any]:
    n = datetime.now(timezone.utc)
    today = date.today()
    return {
        "now": _NowProxy(
            iso=n.isoformat(timespec="seconds"),
            epoch_ms=int(n.timestamp() * 1000),
            epoch_s=int(n.timestamp()),
        ),
        "date": _DateProxy(
            iso=today.isoformat(),
            year=today.year,
            month=today.month,
            day=today.day,
        ),
    }

try:
    from jinja2 import select_autoescape  # type: ignore
    from jinja2.sandbox import SandboxedEnvironment  # type: ignore
    _HAVE_JINJA = True
except Exception:  # pragma: no cover
    SandboxedEnvironment = None  # type: ignore
    select_autoescape = None  # type: ignore
    _HAVE_JINJA = False

from jarvis.core.protocols import ExecutionContext, ToolResult

from .schema import (
    Skill,
    SkillCompleted,
    SkillFailed,
    SkillResult,
    SkillStarted,
    SkillStepExecuted,
)

log = logging.getLogger(__name__)

_TOOL_LINE_RE = re.compile(r"^\s*TOOL:\s*(?P<name>\S+)\s*(?P<args>\{.*\})?\s*$")


class SkillRunner:
    """Runtime-Executor für Skills."""

    def __init__(
        self,
        registry: Any,
        tool_registry: Any | None = None,
        bus: Any | None = None,
        safety_enforcer: Any | None = None,
    ) -> None:
        self.registry = registry
        self.tool_registry = tool_registry
        self.bus = bus
        self.safety_enforcer = safety_enforcer
        if _HAVE_JINJA:
            self._env = SandboxedEnvironment(  # type: ignore[call-arg]
                autoescape=select_autoescape(default=False),
            )
        else:  # pragma: no cover
            self._env = None

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render(self, skill: Skill, extra_context: dict[str, Any] | None = None) -> str:
        """Rendert den Skill-Body via Jinja2-Sandbox."""
        if skill.frontmatter is None:
            return skill.body
        ctx: dict[str, Any] = {
            "today": date.today().isoformat(),
            "user_name": "",
            "config": dict(skill.frontmatter.config),
        }
        ctx.update(_build_time_context())
        if extra_context:
            ctx.update(extra_context)
        if self._env is None:
            return skill.body
        try:
            tpl = self._env.from_string(skill.body)
            return tpl.render(**ctx)
        except Exception as exc:  # noqa: BLE001
            log.warning("jinja render failed for %s: %s", skill.name, exc)
            return skill.body

    # ------------------------------------------------------------------
    # Tool-Call-Extraction
    # ------------------------------------------------------------------

    @staticmethod
    def extract_tool_calls(rendered_body: str) -> list[tuple[str, dict[str, Any]]]:
        """Scannt Zeilen ab, die mit ``TOOL:`` beginnen."""
        calls: list[tuple[str, dict[str, Any]]] = []
        for line in rendered_body.splitlines():
            m = _TOOL_LINE_RE.match(line)
            if not m:
                continue
            name = m.group("name")
            raw_args = m.group("args") or "{}"
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                args = {"_raw": raw_args}
            if not isinstance(args, dict):
                args = {"value": args}
            calls.append((name, args))
        return calls

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def _resolve_tool(self, name: str) -> Any | None:
        """Versucht das Tool-Objekt aus dem Tool-Registry zu beschaffen."""
        if self.tool_registry is None:
            return None
        # gängige APIs: .get(name), .resolve(name), __getitem__
        for attr in ("get", "resolve"):
            fn = getattr(self.tool_registry, attr, None)
            if callable(fn):
                try:
                    obj = fn(name)
                    if obj is not None:
                        return obj
                except Exception:  # noqa: BLE001
                    continue
        try:
            return self.tool_registry[name]  # type: ignore[index]
        except Exception:  # noqa: BLE001
            return None

    def _check_risk(self, skill: Skill, tool_name: str) -> tuple[bool, str]:
        """Gibt (allowed, reason). Wenn safety_enforcer fehlt, immer allow."""
        if self.safety_enforcer is None or skill.frontmatter is None:
            return True, "no-enforcer"
        tier = skill.frontmatter.risk_policy.per_tool_overrides.get(
            tool_name, skill.frontmatter.risk_policy.default_tier
        )
        fn = getattr(self.safety_enforcer, "check", None)
        if callable(fn):
            try:
                result = fn(tool_name=tool_name, tier=tier)
                if isinstance(result, tuple):
                    return bool(result[0]), str(result[1])
                return bool(result), "enforcer"
            except Exception as exc:  # noqa: BLE001
                return False, f"enforcer error: {exc}"
        return True, "no-check-method"

    async def _publish(self, event: Any) -> None:
        if self.bus is None:
            return
        try:
            await self.bus.publish(event)
        except Exception as exc:  # noqa: BLE001
            log.debug("bus.publish failed: %s", exc)

    async def run(
        self,
        skill: Skill,
        args: dict[str, Any] | None = None,
    ) -> SkillResult:
        """Führt den Skill aus (inkl. Tool-Calls)."""
        args = args or {}
        trace_id = uuid4()
        trigger_hint = args.get("_trigger", "manual")

        await self._publish(
            SkillStarted(
                trace_id=trace_id,
                source_layer="skills",
                skill_name=skill.name,
                trigger_type=str(trigger_hint),
            )
        )
        t_start = time.monotonic()

        if skill.frontmatter is None:
            err = skill.error or "skill not parsed"
            await self._publish(
                SkillFailed(
                    trace_id=trace_id,
                    source_layer="skills",
                    skill_name=skill.name,
                    error=err,
                )
            )
            return SkillResult(
                skill_name=skill.name,
                success=False,
                error=err,
                duration_ms=int((time.monotonic() - t_start) * 1000),
            )

        rendered = self.render(skill, extra_context=args)
        calls = self.extract_tool_calls(rendered)
        steps: list[dict[str, Any]] = []

        for idx, (tool_name, tool_args) in enumerate(calls):
            allowed, reason = self._check_risk(skill, tool_name)
            if not allowed:
                step = {
                    "tool": tool_name,
                    "args": tool_args,
                    "success": False,
                    "error": f"risk_tier denied: {reason}",
                }
                steps.append(step)
                await self._publish(
                    SkillStepExecuted(
                        trace_id=trace_id,
                        source_layer="skills",
                        skill_name=skill.name,
                        step_index=idx,
                        tool_name=tool_name,
                        success=False,
                        error=step["error"],
                    )
                )
                await self._publish(
                    SkillFailed(
                        trace_id=trace_id,
                        source_layer="skills",
                        skill_name=skill.name,
                        error=step["error"],
                        at_step=idx,
                    )
                )
                return SkillResult(
                    skill_name=skill.name,
                    success=False,
                    steps=tuple(steps),
                    rendered_body=rendered,
                    error=step["error"],
                    duration_ms=int((time.monotonic() - t_start) * 1000),
                )

            tool_obj = await self._resolve_tool(tool_name)
            if tool_obj is None:
                step = {
                    "tool": tool_name,
                    "args": tool_args,
                    "success": False,
                    "error": f"tool '{tool_name}' not found",
                }
                steps.append(step)
                await self._publish(
                    SkillStepExecuted(
                        trace_id=trace_id,
                        source_layer="skills",
                        skill_name=skill.name,
                        step_index=idx,
                        tool_name=tool_name,
                        success=False,
                        error=step["error"],
                    )
                )
                continue

            ctx = ExecutionContext(
                trace_id=trace_id,
                user_utterance=str(args.get("utterance", "")),
                config=dict(skill.frontmatter.config),
                memory_read=None,
                approved_by="skill-runner",
            )
            step_start = time.monotonic()
            try:
                result = await tool_obj.execute(tool_args, ctx)
                if not isinstance(result, ToolResult):
                    result = ToolResult(success=bool(result), output=result)
                step = {
                    "tool": tool_name,
                    "args": tool_args,
                    "success": result.success,
                    "output": result.output,
                    "error": result.error,
                }
            except Exception as exc:  # noqa: BLE001
                step = {
                    "tool": tool_name,
                    "args": tool_args,
                    "success": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            step_duration = int((time.monotonic() - step_start) * 1000)
            steps.append(step)
            await self._publish(
                SkillStepExecuted(
                    trace_id=trace_id,
                    source_layer="skills",
                    skill_name=skill.name,
                    step_index=idx,
                    tool_name=tool_name,
                    success=bool(step["success"]),
                    duration_ms=step_duration,
                    error=step.get("error"),
                )
            )

        duration_ms = int((time.monotonic() - t_start) * 1000)
        overall_success = all(s["success"] for s in steps) if steps else True
        if overall_success:
            await self._publish(
                SkillCompleted(
                    trace_id=trace_id,
                    source_layer="skills",
                    skill_name=skill.name,
                    duration_ms=duration_ms,
                    steps_count=len(steps),
                )
            )
        else:
            await self._publish(
                SkillFailed(
                    trace_id=trace_id,
                    source_layer="skills",
                    skill_name=skill.name,
                    error="one or more steps failed",
                )
            )
        return SkillResult(
            skill_name=skill.name,
            success=overall_success,
            steps=tuple(steps),
            rendered_body=rendered,
            error=None if overall_success else "step failure",
            duration_ms=duration_ms,
        )
