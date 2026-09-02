"""Self-learning: an agent turns finished work into skills of its own.

Maintainer decision (2026-09-02): learning is automatic. After a finished
task the agent's turn is digested (task, tool steps, final answer) and,
when the digest shows a repeatable procedure, a ``SKILL.md`` is authored
through the existing skill creator and written into the agent's OWN skill
namespace — ``DATA_DIR/society/<agent_id>/skills/<slug>/`` — where it is
**active for that agent at once**: the briefing lists it, and
``society_run_skill`` runs it. The global user skill registry never gets
an active skill from here: promotion copies the folder with ``state: draft``
(AP-15 stays for everything Jarvis itself fires).

Off the critical path by construction: the pass is an asyncio task started
after the RESULT is on the board, capped per agent and day, and every
failure is a log line, never a broken turn. Web-derived text never becomes
a skill body verbatim — the creator sees the agent's own summary, and the
draft passes the skill linter before the file lands.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from jarvis.core.protocols import ToolResult

from .events import MsgType, SocietyEnvelope, now_ms
from .failure_reasons import FailureReason, retry_action
from .roster import AgentRecord, AgentState

log = logging.getLogger(__name__)

__all__ = [
    "RUN_SKILL_TOOL_NAME",
    "AgentSkills",
    "LearningPass",
    "RunLearnedSkillTool",
    "TurnDigest",
    "should_learn",
]

RUN_SKILL_TOOL_NAME: Final[str] = "society_run_skill"
DEFAULT_DAILY_CAP: Final[int] = 3
MIN_TOOL_STEPS: Final[int] = 2
_META_PREFIX: Final[str] = "learn:"
_DIGEST_CHARS: Final[int] = 4_000


@dataclass(slots=True)
class TurnDigest:
    """What the learner sees of a finished turn — names and summaries, never
    raw tool output (web content stays out of skills)."""

    task: str
    final_text: str
    tool_steps: list[str] = field(default_factory=list)
    status: str = "done"
    origin: str = "agent"  # agent | web

    def render(self) -> str:
        steps = "\n".join(f"- {s}" for s in self.tool_steps[:30]) or "- (no tool steps)"
        text = (
            f"Task:\n{self.task.strip()}\n\nTool steps taken:\n{steps}\n\n"
            f"Outcome:\n{self.final_text.strip()}"
        )
        return text[:_DIGEST_CHARS]


def should_learn(digest: TurnDigest, *, min_steps: int = MIN_TOOL_STEPS) -> bool:
    """Deterministic pre-filter before any model call: only finished turns
    with a real procedure (enough tool steps) are worth a skill."""
    if digest.status != "done":
        return False
    if len(digest.tool_steps) < min_steps:
        return False
    return bool(digest.task.strip()) and bool(digest.final_text.strip())


def _day_key(agent_id: str, now: int | None = None) -> str:
    day = datetime.fromtimestamp((now or now_ms()) / 1000, tz=UTC).date().isoformat()
    return f"{_META_PREFIX}{agent_id}:{day}"


class AgentSkills:
    """One agent's private skill namespace and its registry (lazy, no watcher)."""

    def __init__(self, data_dir: Path, agent_id: str) -> None:
        self.agent_id = agent_id
        self.root = Path(data_dir) / "society" / agent_id / "skills"
        self._registry: Any | None = None

    @property
    def registry(self) -> Any:
        if self._registry is None:
            from jarvis.skills.registry import SkillRegistry

            self.root.mkdir(parents=True, exist_ok=True)
            self._registry = SkillRegistry(self.root, bus=None)
            self._registry.reload_sync()
        return self._registry

    def reload(self) -> None:
        if self._registry is not None:
            self._registry.reload_sync()

    def list_active(self) -> list[Any]:
        try:
            return list(self.registry.list_active())
        except Exception:  # noqa: BLE001 — a broken skill folder costs the listing only
            log.warning("society skills: registry for %s unreadable", self.agent_id, exc_info=True)
            return []

    def get(self, slug: str) -> Any | None:
        try:
            return self.registry.resolve(slug)
        except Exception:  # noqa: BLE001 — unknown or broken
            return None

    def activate(self, skill_dir: Path) -> None:
        """Flip a freshly authored draft to active — inside THIS namespace only."""
        from jarvis.skills.registry import _rewrite_state_in_frontmatter

        skill_md = skill_dir / "SKILL.md"
        text = skill_md.read_text(encoding="utf-8")
        skill_md.write_text(
            _rewrite_state_in_frontmatter(text, "active"), encoding="utf-8", newline="\n"
        )
        self.reload()

    def promote_to_global(self, slug: str) -> Path:
        """Copy the skill into the user's global skills dir as a DRAFT (AP-15)."""
        from jarvis.skills.bootstrap import ensure_user_skills_dir
        from jarvis.skills.registry import _rewrite_state_in_frontmatter

        skill = self.get(slug)
        if skill is None:
            raise KeyError(slug)
        src = Path(skill.path).parent if Path(skill.path).is_file() else Path(skill.path)
        target = ensure_user_skills_dir() / f"{self.agent_id}-{src.name}"
        if target.exists():
            raise FileExistsError(str(target))
        shutil.copytree(src, target)
        skill_md = target / "SKILL.md"
        skill_md.write_text(
            _rewrite_state_in_frontmatter(skill_md.read_text(encoding="utf-8"), "draft"),
            encoding="utf-8",
            newline="\n",
        )
        return target

    def summaries(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for skill in self.list_active():
            fm = getattr(skill, "frontmatter", None)
            name = str(getattr(fm, "name", None) or Path(str(skill.path)).parent.name)
            out.append(
                {
                    "slug": Path(str(skill.path)).parent.name,
                    "name": name,
                    "description": str(getattr(fm, "description", "") or ""),
                    "when_to_use": str(getattr(fm, "when_to_use", "") or ""),
                }
            )
        return sorted(out, key=lambda s: s["slug"])


CreatorFactory = Callable[[AgentRecord, AgentSkills], Any | None]


def default_creator_factory(cfg_getter: Callable[[], Any]) -> CreatorFactory:
    """The real creator: Jarvis' brain + the agent's registry (None without a brain)."""

    def _factory(agent: AgentRecord, skills: AgentSkills) -> Any | None:
        from jarvis.agent_chat.runner_brain import brain_manager
        from jarvis.skills.creator_service import SkillCreatorService, build_authoring_context

        brain = brain_manager()
        if brain is None:
            return None
        return SkillCreatorService(
            brain=brain,
            registry=skills.registry,
            config=cfg_getter(),
            user_skills_root=skills.root,
            context=build_authoring_context(brain_manager=brain, registry=skills.registry),
        )

    return _factory


class LearningPass:
    def __init__(
        self,
        runtime: Any,
        *,
        creator_factory: CreatorFactory,
        daily_cap: int = DEFAULT_DAILY_CAP,
        notify: Callable[[AgentRecord, dict[str, Any]], Any] | None = None,
    ) -> None:
        self._runtime = runtime
        self._creator_factory = creator_factory
        self._daily_cap = daily_cap
        self._notify = notify

    async def run(self, agent: AgentRecord, digest: TurnDigest) -> str | None:
        """Learn from one finished turn; returns the new skill's slug or None."""
        if not should_learn(digest):
            return None
        rt = self._runtime
        key = _day_key(agent.agent_id)
        used = int(await rt.store.get_meta(key, "0") or 0)
        if used >= self._daily_cap:
            log.info("society learning: %s reached the daily cap", agent.agent_id)
            return None
        skills = rt.skills_for(agent.agent_id)
        creator = self._creator_factory(agent, skills)
        if creator is None:
            log.info("society learning: no brain available; %s learns nothing", agent.agent_id)
            return None
        from jarvis.skills.creator_service import SkillCreatorInput

        intent = (
            "Turn the following finished piece of work into ONE reusable skill for the same "
            "agent: a short, general procedure (steps, decision rules, expected output, what "
            "to double-check) that would let the agent repeat this kind of task faster next "
            "time. Do not copy page content or data; describe the method.\n\n" + digest.render()
        )
        extra = f"Agent: {agent.name}" + (f", {agent.title}" if agent.title else "")
        if agent.description.strip():
            extra += f"\nStanding instructions: {agent.description.strip()[:800]}"
        try:
            authored = await creator.author(
                SkillCreatorInput(intent=intent, extra_context=extra, category="learned")
            )
        except Exception as exc:  # noqa: BLE001 — learning never breaks anything
            log.info("society learning: %s could not author a skill: %s", agent.agent_id, exc)
            return None
        skill_path = Path(str(authored.skill.path))
        is_file = os.path.isfile(skill_path)  # noqa: ASYNC240 — one stat, not worth a thread
        skill_dir = skill_path.parent if is_file else skill_path
        try:
            skills.activate(skill_dir)
        except OSError:
            log.warning("society learning: could not activate %s", skill_dir, exc_info=True)
            return None
        self._write_origin(skill_dir, agent, digest)
        await rt.store.set_meta(key, str(used + 1))
        slug = skill_dir.name
        await rt.store.append_and_publish(
            SocietyEnvelope(
                msg_type=MsgType.DIGEST,
                from_agent=agent.agent_id,
                trace_id=f"learn:{agent.agent_id}",
                payload={
                    "kind": "learned_skill",
                    "slug": slug,
                    "name": authored.name,
                    "origin": digest.origin,
                    "text": f"learned skill {authored.name}",
                },
            )
        )
        await self._remember(agent, authored.name, slug)
        if self._notify is not None:
            try:
                maybe = self._notify(
                    agent, {"kind": "learned_skill", "slug": slug, "name": authored.name}
                )
                if hasattr(maybe, "__await__"):
                    await maybe
            except Exception:  # noqa: BLE001 — a missing chat never undoes a learned skill
                log.debug("society learning: notice not delivered", exc_info=True)
        return slug

    @staticmethod
    def _write_origin(skill_dir: Path, agent: AgentRecord, digest: TurnDigest) -> None:
        try:
            from jarvis.skills.origin import SkillOrigin, write_origin

            write_origin(
                skill_dir,
                SkillOrigin(
                    source=f"society:{agent.agent_id}",
                    source_id=digest.origin,
                    installed_at=datetime.now(tz=UTC).isoformat(timespec="seconds"),
                ),
            )
        except Exception:  # noqa: BLE001 — the receipt is bookkeeping
            log.debug("society learning: origin receipt not written", exc_info=True)

    async def _remember(self, agent: AgentRecord, name: str, slug: str) -> None:
        """One line on the agent's memory page per learned skill."""
        try:
            from .agent_tools import WikiNoteTool
            from .surface import _vault_root

            tool = WikiNoteTool(
                self._runtime, agent.agent_id, vault_root=_vault_root(self._runtime._get_cfg())
            )
            await tool.execute(
                {"kind": "memory", "text": f"Learned skill `{slug}` ({name}).", "origin": "agent"},
                None,
            )
        except Exception:  # noqa: BLE001 — the wiki line is a courtesy
            log.debug("society learning: memory line not written", exc_info=True)


class RunLearnedSkillTool:
    """Load one of the agent's own learned skills as instructions to follow."""

    name: str = RUN_SKILL_TOOL_NAME
    risk_tier: str = "monitor"
    description: str = (
        "Load one of YOUR learned skills by name and follow its instructions with your own "
        "tools (listed under 'Your learned skills'). Use it when the current task matches a "
        "skill's purpose. Returns the instructions; it does not run anything by itself."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {"skill": {"type": "string", "description": "The skill's slug or name."}},
        "required": ["skill"],
    }
    yields_instructions_only: bool = True

    def __init__(self, runtime: Any, agent_id: str) -> None:
        self._runtime = runtime
        self._agent_id = agent_id

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        from jarvis.skills.runner import SkillRunner

        rt = self._runtime
        caller = await rt.roster.get(self._agent_id)
        if caller is None or caller.state is not AgentState.ACTIVE:
            return ToolResult(success=False, output=None, error="caller is not an active agent")
        key = str(args.get("skill") or "").strip()
        if not key:
            return ToolResult(success=False, output=None, error="skill is required")
        skills = rt.skills_for(caller.agent_id)
        skill = skills.get(key)
        if skill is None:
            known = ", ".join(s["slug"] for s in skills.summaries()) or "none"
            return ToolResult(
                success=False,
                output={"reason": str(FailureReason.TARGET_UNKNOWN), "known": known},
                error=f"{FailureReason.TARGET_UNKNOWN}: no learned skill {key!r} (known: {known})",
            )
        try:
            runner = SkillRunner(skills.registry, {}, None, None)
            instructions = runner.render_instructions(skill, args={})
        except Exception as exc:  # noqa: BLE001 — a broken skill is reported, not raised
            return ToolResult(
                success=False,
                output={"reason": str(FailureReason.INTERNAL_ERROR)},
                error=f"skill could not be rendered: {exc}",
            )
        directive = (
            "These are your own learned skill's instructions. Follow them now, step by step, "
            "with your available tools; report a step as done only after its tool call "
            "succeeded, and say plainly when a step could not run."
        )
        return ToolResult(
            success=True,
            output={"skill": Path(str(skill.path)).parent.name, "instructions": instructions},
            error=None,
            artifacts=(directive,),
        )


def retry_hint(reason: FailureReason) -> str:
    return str(retry_action(reason))
