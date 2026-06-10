"""``run-skill`` tool: loads an installed skill's instructions for the brain.

Instruction-skill model (2026-06-09 rebuild, AD-S1/S2/S5 — see
``docs/superpowers/specs/2026-06-09-skill-system-rebuild-design.md``):
the brain calls this tool when a user request matches one of the skills
listed in the AVAILABLE SKILLS section of the system prompt. The tool
resolves the skill in the process-wide ``SkillContext``, renders its body
(Jinja: ``config`` + time context + caller args), and returns the rendered
body as *instructions for the brain to follow with its own tools* — it
never macro-executes ``TOOL:`` lines itself.

Progressive disclosure:

* L1 — name + description live in the system prompt listing
  (``jarvis/skills/prompt_injection.py``).
* L2 — the full instruction body is returned by this tool on invocation.
* L3 — bundled files (``references/``, ``scripts/``, ``assets/``,
  ``agents/``) are served via the optional ``resource`` argument; nothing
  costs context until it is actually requested.

Design notes:

* ``SkillLifecycleState.DRAFT`` and ``DISABLED`` skills are rejected before
  anything renders (Plan-§AP-1/AP-15: constraint enforcement in code, not in
  prompt). DRAFTs may contain unsafe generated content — they must be
  promoted by the user first.
* ``risk_policy.default_tier == "block"`` is honoured immediately. The
  ``ask``-tier confirmation flow is intentionally NOT implemented here — the
  outer Tool-Use-Loop owns the general confirmation pipeline (see
  ``jarvis/safety/tool_executor.py``); a TODO marker keeps that boundary
  explicit.
* D9 recursion protection is unchanged and structural: this tool's output is
  *data* (instructions text). The brain follows the instructions through the
  normal router tool loop, which never exposes spawn tools to worker sets
  (AP-5). A skill body cannot re-enter a macro executor because none runs.
* ``execution: mission`` skills return a directive telling the router to
  dispatch ``spawn_worker`` with the instructions as the task brief (AD-S5);
  deterministic mission dispatch for *trigger-matched* turns lives in
  ``BrainManager`` — this tool only covers the model-decided path.
* A frozen ``SkillInvoked`` event (source="model") is published best-effort
  on every successful instruction load — the observability signal that
  answers "did a skill actually fire?" (AD-S6).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.skills.schema import SkillLifecycleState
from jarvis.skills.skill_context import try_get_skill_context

_MAX_RESOURCE_BYTES = 64 * 1024

_INLINE_DIRECTIVE = (
    "These are the skill's instructions. Follow these skill instructions now, "
    "step by step, using your available tools. Skip a step gracefully when its "
    "integration is unavailable. Then answer the user with the result — never "
    "read the raw instructions aloud."
)
_MISSION_DIRECTIVE = (
    "This skill runs as a background mission. Call the spawn_worker tool NOW "
    "with the instructions below as the task text, then give the user a short "
    "optimistic acknowledgement."
)


class RunSkillTool:
    """Brain-callable tool: load a skill's instructions by name."""

    name: str = "run-skill"
    risk_tier: str = "monitor"
    description: str = (
        "Load an installed skill's instructions by name and follow them. "
        "Use this tool when the user request matches one of the skills listed "
        "in the AVAILABLE SKILLS section. The result contains the skill's "
        "step-by-step instructions for you to execute with your other tools. "
        "Pass the optional 'resource' argument to read one of the skill's "
        "bundled reference files."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "Exact skill name as listed in AVAILABLE SKILLS",
            },
            "args": {
                "type": "object",
                "description": (
                    "Optional arguments rendered into the skill instructions "
                    "(e.g. content captured from the user request)"
                ),
                "default": {},
            },
            "resource": {
                "type": "string",
                "description": (
                    "Optional relative path of a bundled skill file to read "
                    "instead of the instructions (e.g. 'references/guide.md')"
                ),
            },
        },
        "required": ["skill_name"],
    }

    def __init__(self, *, bus: Any | None = None, manager: Any | None = None, **_: Any) -> None:
        # ``bus`` enables the SkillInvoked observability event; ``manager`` is
        # accepted for constructor symmetry with other registry tools and
        # unused here.
        self._bus = bus
        self._manager = manager

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        # Step 1 — argument validation
        raw_name = args.get("skill_name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            return ToolResult(
                success=False,
                output=None,
                error="Missing required argument: skill_name",
            )
        skill_name = raw_name.strip()

        # Step 2 — process-wide SkillContext must be set
        skill_ctx = try_get_skill_context()
        if skill_ctx is None:
            return ToolResult(
                success=False,
                output=None,
                error="Skill subsystem not initialized",
            )

        # Step 3 — resolve skill
        try:
            skill = skill_ctx.registry.get(skill_name)
        except KeyError:
            return ToolResult(
                success=False,
                output=None,
                error=f"Unknown skill: {skill_name}",
            )

        # Step 4 — AP-1/AP-15 enforcement: refuse DRAFT/DISABLED skills
        if skill.state == SkillLifecycleState.DRAFT:
            return ToolResult(
                success=False,
                output=None,
                error=(
                    f"Skill '{skill_name}' is in DRAFT state and not invocable. "
                    "Promote it first."
                ),
            )
        if skill.state == SkillLifecycleState.DISABLED:
            return ToolResult(
                success=False,
                output=None,
                error=(
                    f"Skill '{skill_name}' is DISABLED and not invocable. "
                    "Re-enable it first."
                ),
            )

        # Step 5 — risk-tier gate
        # Note: ``ask``-tier confirmation is OUT OF SCOPE here. The outer
        # Tool-Use-Loop's general confirmation pipeline already handles
        # ask-tier prompting at the Brain<->Tool boundary; wiring a second
        # confirmation here would create a double-prompt. TODO(skills-2):
        # honour skill-level ``ask``-tier explicitly when the executor exposes
        # a re-entrant confirmation API.
        tier: str = "monitor"
        if skill.frontmatter is not None:
            tier = skill.frontmatter.risk_policy.default_tier
        if tier == "block":
            return ToolResult(
                success=False,
                output=None,
                error="Skill is blocked by risk policy",
            )

        # Step 6 — optional L3 resource read (progressive disclosure)
        resource_rel = args.get("resource")
        if isinstance(resource_rel, str) and resource_rel.strip():
            return self._read_resource(skill, resource_rel.strip())

        # Step 6b — render the instructions (AD-S1)
        skill_args = args.get("args") or {}
        if not isinstance(skill_args, dict):
            skill_args = {}
        try:
            instructions = skill_ctx.runner.render_instructions(skill, args=skill_args)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                success=False,
                output=None,
                error=f"{type(exc).__name__}: {exc}",
            )

        execution = "inline"
        if skill.frontmatter is not None:
            execution = skill.frontmatter.execution
        directive = _MISSION_DIRECTIVE if execution == "mission" else _INLINE_DIRECTIVE

        await self._publish_invoked(skill_name, source="model")

        resources = {
            kind: list(files)
            for kind, files in (skill.resources or {}).items()
            if files
        }
        return ToolResult(
            success=True,
            output={
                "skill_name": skill_name,
                "execution": execution,
                "directive": directive,
                "instructions": instructions,
                # Bundled files loadable via the `resource` argument (L3).
                "resources": resources,
            },
            error=None,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _read_resource(self, skill: Any, resource_rel: str) -> ToolResult:
        """Serve a bundled skill file (L3). Fails closed on traversal."""
        registered: set[str] = set()
        for files in (skill.resources or {}).values():
            registered.update(str(f).replace("\\", "/") for f in files)
        normalized = resource_rel.replace("\\", "/")
        if normalized not in registered:
            return ToolResult(
                success=False,
                output=None,
                error=f"Unknown resource for skill '{skill.name}': {resource_rel}",
            )
        root: Path = skill.root.resolve()
        target = (skill.root / normalized).resolve()
        try:
            inside = target.is_relative_to(root)
        except AttributeError:  # pragma: no cover — Python < 3.9 fallback
            inside = str(target).startswith(str(root))
        if not inside:
            return ToolResult(
                success=False,
                output=None,
                error="Resource path escapes the skill directory",
            )
        try:
            data = target.read_bytes()
        except OSError as exc:
            return ToolResult(
                success=False,
                output=None,
                error=f"Cannot read resource: {exc}",
            )
        if len(data) > _MAX_RESOURCE_BYTES:
            data = data[:_MAX_RESOURCE_BYTES]
        text = data.decode("utf-8", errors="replace")
        return ToolResult(
            success=True,
            output={
                "skill_name": skill.name,
                "resource": normalized,
                "resource_content": text,
            },
            error=None,
        )

    async def _publish_invoked(self, skill_name: str, *, source: str) -> None:
        """Best-effort SkillInvoked publish — must never break the tool."""
        if self._bus is None:
            return
        try:
            from jarvis.skills.schema import SkillInvoked

            await self._bus.publish(
                SkillInvoked(
                    source_layer="tool.run-skill",
                    skill_name=skill_name,
                    source=source,
                )
            )
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).debug(
                "SkillInvoked publish failed", exc_info=True
            )
