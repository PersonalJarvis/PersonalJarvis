"""Unit tests for the ``run-skill`` Brain-callable tool.

Instruction-skill model (2026-06-09 rebuild, AD-S1/S2/S5): the tool resolves
a skill by name, enforces DRAFT/DISABLED/block-tier rejection, and returns the
rendered skill body as instructions for the brain to follow — it never
macro-executes. Optional ``resource`` argument serves bundled files
(progressive disclosure L3). Tests use Fakes (no ``unittest.mock``) per
``CLAUDE.md`` testing-conventions.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.plugins.tool.run_skill import RunSkillTool
from jarvis.skills.schema import (
    Skill,
    SkillFrontmatter,
    SkillLifecycleState,
    SkillRiskPolicy,
)
from jarvis.skills.skill_context import SkillContext, set_skill_context

# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------


class _FakeRegistry:
    """Tiny registry whose ``get`` raises ``KeyError`` like the real one."""

    def __init__(self, skills: dict[str, Skill] | None = None) -> None:
        self._skills = skills or {}

    def get(self, name: str) -> Skill:
        if name not in self._skills:
            raise KeyError(f"Skill '{name}' not in registry")
        return self._skills[name]

    def list(self) -> list[Skill]:
        return list(self._skills.values())


@dataclass
class _RenderCall:
    skill: Skill
    args: dict[str, Any]


class _FakeRunner:
    """Records ``render_instructions`` calls, returns a scripted body."""

    def __init__(self, scripted_body: str = "# Demo\nDo the thing.") -> None:
        self.calls: list[_RenderCall] = []
        self.scripted_body = scripted_body

    def render_instructions(
        self, skill: Skill, *, args: dict[str, Any] | None = None
    ) -> str:
        self.calls.append(_RenderCall(skill=skill, args=dict(args or {})))
        return self.scripted_body


class _ExplodingRunner:
    """Runner that raises to verify the tool catches inner exceptions."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.calls: list[_RenderCall] = []

    def render_instructions(
        self, skill: Skill, *, args: dict[str, Any] | None = None
    ) -> str:
        self.calls.append(_RenderCall(skill=skill, args=dict(args or {})))
        raise self.exc


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.published.append(event)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _make_skill(
    name: str = "demo_skill",
    *,
    state: SkillLifecycleState = SkillLifecycleState.ACTIVE,
    default_tier: str = "monitor",
    frontmatter: bool = True,
    execution: str = "inline",
    path: Path | None = None,
    resources: dict[str, tuple[str, ...]] | None = None,
) -> Skill:
    fm: SkillFrontmatter | None
    if frontmatter:
        fm = SkillFrontmatter(
            schema_version="1",
            name=name,
            description="fake skill",
            risk_policy=SkillRiskPolicy(default_tier=default_tier),  # type: ignore[arg-type]
            execution=execution,  # type: ignore[arg-type]
        )
    else:
        fm = None
    kwargs: dict[str, Any] = {}
    if resources is not None:
        kwargs["resources"] = resources
    return Skill(
        path=path or (Path("nonexistent") / name / "SKILL.md"),
        frontmatter=fm,
        body="dummy",
        state=state,
        body_hash="deadbeef",
        error=None,
        **kwargs,
    )


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid4(),
        user_utterance="run the demo skill",
        config={},
        memory_read=None,
        approved_by="auto",
    )


def _wire(skill: Skill, runner: Any | None = None) -> Any:
    runner = runner or _FakeRunner()
    registry = _FakeRegistry({skill.name: skill})
    set_skill_context(SkillContext(registry=registry, runner=runner))
    return runner


@pytest.fixture(autouse=True)
def _reset_skill_context():
    """Reset the global SkillContext between tests."""
    set_skill_context(None)
    yield
    set_skill_context(None)


# ----------------------------------------------------------------------
# Validation gates (unchanged contract)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_skill_unknown_skill_returns_error() -> None:
    registry = _FakeRegistry()  # empty
    runner = _FakeRunner()
    set_skill_context(SkillContext(registry=registry, runner=runner))

    tool = RunSkillTool()
    result = await tool.execute({"skill_name": "missing"}, _ctx())

    assert isinstance(result, ToolResult)
    assert result.success is False
    assert result.error is not None
    assert "missing" in result.error
    assert runner.calls == []


@pytest.mark.asyncio
async def test_run_skill_rejects_draft_state() -> None:
    skill = _make_skill("draft_skill", state=SkillLifecycleState.DRAFT)
    runner = _wire(skill)

    tool = RunSkillTool()
    result = await tool.execute({"skill_name": "draft_skill"}, _ctx())

    assert result.success is False
    assert result.error is not None
    assert "DRAFT" in result.error
    assert runner.calls == [], "instructions must NOT render for DRAFT skills"


@pytest.mark.asyncio
async def test_run_skill_rejects_disabled_state() -> None:
    skill = _make_skill("off_skill", state=SkillLifecycleState.DISABLED)
    runner = _wire(skill)

    tool = RunSkillTool()
    result = await tool.execute({"skill_name": "off_skill"}, _ctx())

    assert result.success is False
    assert result.error is not None
    assert "DISABLED" in result.error
    assert runner.calls == [], "instructions must NOT render for DISABLED skills"


@pytest.mark.asyncio
async def test_run_skill_rejects_block_tier() -> None:
    skill = _make_skill("blocked_skill", default_tier="block")
    runner = _wire(skill)

    tool = RunSkillTool()
    result = await tool.execute({"skill_name": "blocked_skill"}, _ctx())

    assert result.success is False
    assert "block" in (result.error or "").lower()
    assert runner.calls == []


@pytest.mark.asyncio
async def test_run_skill_no_skill_context() -> None:
    set_skill_context(None)  # explicit
    tool = RunSkillTool()

    result = await tool.execute({"skill_name": "anything"}, _ctx())

    assert result.success is False
    assert "not initialized" in (result.error or "")


@pytest.mark.asyncio
async def test_run_skill_missing_argument() -> None:
    tool = RunSkillTool()
    result = await tool.execute({}, _ctx())
    assert result.success is False
    assert "skill_name" in (result.error or "")


# ----------------------------------------------------------------------
# Instruction-skill model (AD-S1)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_instructions_not_macro_result() -> None:
    skill = _make_skill("demo_skill")
    runner = _wire(skill, _FakeRunner(scripted_body="# Demo\nDo the briefing."))

    tool = RunSkillTool()
    result = await tool.execute(
        {"skill_name": "demo_skill", "args": {"foo": "bar"}}, _ctx()
    )

    assert result.success is True
    out = result.output
    assert out["skill_name"] == "demo_skill"
    assert out["execution"] == "inline"
    assert out["instructions"].startswith("# Demo")
    assert "Follow these skill instructions now" in out["directive"]
    assert runner.calls[0].args == {"foo": "bar"}


@pytest.mark.asyncio
async def test_mission_skill_returns_mission_directive() -> None:
    skill = _make_skill("heavy", execution="mission")
    _wire(skill)

    tool = RunSkillTool()
    result = await tool.execute({"skill_name": "heavy"}, _ctx())

    assert result.success is True
    assert result.output["execution"] == "mission"
    assert "spawn_worker" in result.output["directive"]


@pytest.mark.asyncio
async def test_render_failure_is_reported() -> None:
    skill = _make_skill("flaky_skill")
    _wire(skill, _ExplodingRunner(RuntimeError("boom")))

    tool = RunSkillTool()
    result = await tool.execute({"skill_name": "flaky_skill"}, _ctx())

    assert result.success is False
    assert "boom" in (result.error or "")


@pytest.mark.asyncio
async def test_skill_invoked_event_published() -> None:
    skill = _make_skill("demo_skill")
    bus = _FakeBus()
    runner = _FakeRunner()
    registry = _FakeRegistry({"demo_skill": skill})
    set_skill_context(SkillContext(registry=registry, runner=runner))

    tool = RunSkillTool(bus=bus)
    await tool.execute({"skill_name": "demo_skill"}, _ctx())

    names = [type(e).__name__ for e in bus.published]
    assert "SkillInvoked" in names
    ev = next(e for e in bus.published if type(e).__name__ == "SkillInvoked")
    assert ev.skill_name == "demo_skill"
    assert ev.source == "model"


# ----------------------------------------------------------------------
# Progressive disclosure L3: bundled resources (AD-S2)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resource_loading(tmp_path: Path) -> None:
    root = tmp_path / "demo_skill"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text("---\nname: x\n---\nbody", encoding="utf-8")
    (root / "references" / "guide.md").write_text("guide content", encoding="utf-8")
    skill = _make_skill(
        "demo_skill",
        path=root / "SKILL.md",
        resources={
            "references": ("references/guide.md",),
            "scripts": (),
            "assets": (),
            "agents": (),
        },
    )
    _wire(skill)

    tool = RunSkillTool()
    result = await tool.execute(
        {"skill_name": "demo_skill", "resource": "references/guide.md"}, _ctx()
    )

    assert result.success is True
    assert "guide content" in result.output["resource_content"]


@pytest.mark.asyncio
async def test_resource_path_traversal_rejected(tmp_path: Path) -> None:
    root = tmp_path / "demo_skill"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("---\nname: x\n---\nbody", encoding="utf-8")
    secret = tmp_path / "secrets.txt"
    secret.write_text("nope", encoding="utf-8")
    skill = _make_skill("demo_skill", path=root / "SKILL.md")
    _wire(skill)

    tool = RunSkillTool()
    result = await tool.execute(
        {"skill_name": "demo_skill", "resource": "../secrets.txt"}, _ctx()
    )

    assert result.success is False


@pytest.mark.asyncio
async def test_unregistered_resource_rejected(tmp_path: Path) -> None:
    root = tmp_path / "demo_skill"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("---\nname: x\n---\nbody", encoding="utf-8")
    (root / "rogue.md").write_text("rogue", encoding="utf-8")
    skill = _make_skill("demo_skill", path=root / "SKILL.md")
    _wire(skill)

    tool = RunSkillTool()
    result = await tool.execute(
        {"skill_name": "demo_skill", "resource": "rogue.md"}, _ctx()
    )

    assert result.success is False
    assert "resource" in (result.error or "").lower()


# ----------------------------------------------------------------------
# Speech-tolerant name resolution (live regression 2026-08-20)
# ----------------------------------------------------------------------


class _ResolvingRegistry(_FakeRegistry):
    """A registry with the real ``resolve`` + ``list_active`` surface.

    ``_FakeRegistry`` above predates ``resolve`` on purpose — it proves the
    tool still works against a registry that has only ``get`` (mock and
    headless boots). This one carries the real thing.
    """

    def resolve(self, name: str) -> Skill:
        from jarvis.skills.relevance import skill_name_tokens

        key = str(name or "").strip()
        if key in self._skills:
            return self._skills[key]
        wanted = skill_name_tokens(key)
        if wanted:
            for candidate in sorted(self._skills.values(), key=lambda s: s.name):
                if skill_name_tokens(str(candidate.name)) == wanted:
                    return candidate
        raise KeyError(f"Skill '{name}' not in registry")

    def list_active(self) -> list[Skill]:
        return list(self._skills.values())


@pytest.mark.asyncio
async def test_run_skill_resolves_a_display_name_to_the_installed_slug() -> None:
    """The live failure, verbatim: the realtime model sent "Morning Routine"
    while the registry key was "morning-routine", and the user was told the
    skill did not exist three turns running."""
    skill = _make_skill(name="morning-routine")
    registry = _ResolvingRegistry({"morning-routine": skill})
    runner = _FakeRunner()
    set_skill_context(SkillContext(registry=registry, runner=runner))  # type: ignore[arg-type]

    result = await RunSkillTool().execute({"skill_name": "Morning Routine"}, _ctx())

    assert result.success is True
    assert result.output["skill_name"] == "morning-routine"


@pytest.mark.asyncio
async def test_run_skill_reports_the_resolved_name_not_the_guess() -> None:
    """Everything downstream — the spoken answer, the SkillInvoked event —
    must name the skill that ran, never the string that led to it."""
    skill = _make_skill(name="morning-routine")
    registry = _ResolvingRegistry({"morning-routine": skill})
    set_skill_context(SkillContext(registry=registry, runner=_FakeRunner()))  # type: ignore[arg-type]

    result = await RunSkillTool().execute({"skill_name": "MORNING ROUTINE"}, _ctx())

    assert result.output["skill_name"] == "morning-routine"


@pytest.mark.asyncio
async def test_run_skill_unknown_name_lists_what_is_installed() -> None:
    """A bare dead end ends the turn; naming the alternatives lets the model
    correct itself before the user hears anything."""
    registry = _ResolvingRegistry({"morning-routine": _make_skill(name="morning-routine")})
    set_skill_context(SkillContext(registry=registry, runner=_FakeRunner()))  # type: ignore[arg-type]

    result = await RunSkillTool().execute({"skill_name": "brew coffee"}, _ctx())

    assert result.success is False
    assert "Unknown skill: brew coffee" in result.error
    assert "morning-routine" in result.error


@pytest.mark.asyncio
async def test_run_skill_still_works_on_a_registry_without_resolve() -> None:
    """Mock and headless boots ship a reduced registry; the tool must not
    require the newer method to function."""
    skill = _make_skill(name="demo_skill")
    set_skill_context(
        SkillContext(registry=_FakeRegistry({"demo_skill": skill}), runner=_FakeRunner())  # type: ignore[arg-type]
    )

    result = await RunSkillTool().execute({"skill_name": "demo_skill"}, _ctx())

    assert result.success is True


# ----------------------------------------------------------------------
# Music: the loaded skill follows the CONNECTED service
# ----------------------------------------------------------------------


def _music_skill(plugin_id: str) -> Skill:
    fm = SkillFrontmatter(
        schema_version="1",
        name=f"plugin-{plugin_id}",
        description=f"Play music on {plugin_id}.",
        plugin_id=plugin_id,
        risk_policy=SkillRiskPolicy(default_tier="monitor"),  # type: ignore[arg-type]
        execution="inline",  # type: ignore[arg-type]
    )
    return Skill(
        path=Path("nonexistent") / f"plugin-{plugin_id}" / "SKILL.md",
        frontmatter=fm,
        body=f"Use {plugin_id}.",
        state=SkillLifecycleState.ACTIVE,
        body_hash="deadbeef",
        error=None,
    )


def _music_registry() -> _FakeRegistry:
    return _FakeRegistry(
        {
            "plugin-spotify": _music_skill("spotify"),
            "plugin-youtube_music": _music_skill("youtube_music"),
        }
    )


@pytest.mark.asyncio
async def test_a_music_skill_load_follows_the_only_connected_service(monkeypatch) -> None:
    """Live 2026-08-22 18:16: the model asked for ``plugin-spotify`` on a box
    where only YouTube Music was connected, and held Spotify's how-to."""
    from jarvis.core import music_service as ms

    monkeypatch.setattr(ms, "connected_music_services", lambda: ("youtube_music",))
    monkeypatch.setattr(ms, "preferred_music_service", lambda: "auto")
    runner = _FakeRunner()
    set_skill_context(SkillContext(registry=_music_registry(), runner=runner))  # type: ignore[arg-type]

    result = await RunSkillTool().execute({"skill_name": "plugin-spotify"}, _ctx())

    assert result.success is True
    assert result.output["skill_name"] == "plugin-youtube_music"
    assert runner.calls[0].skill.name == "plugin-youtube_music"


@pytest.mark.asyncio
async def test_a_named_music_service_is_kept(monkeypatch) -> None:
    """The user said Spotify: that wins even when YouTube Music is connected too."""
    from jarvis.core import music_service as ms

    monkeypatch.setattr(
        ms, "connected_music_services", lambda: ("spotify", "youtube_music")
    )
    monkeypatch.setattr(ms, "preferred_music_service", lambda: "youtube_music")
    set_skill_context(SkillContext(registry=_music_registry(), runner=_FakeRunner()))  # type: ignore[arg-type]
    ctx = ExecutionContext(
        trace_id=uuid4(),
        user_utterance="Spiel Jazz auf Spotify.",  # i18n-allow: spoken-input fixture
        config={},
        memory_read=None,
    )

    result = await RunSkillTool().execute({"skill_name": "plugin-spotify"}, ctx)

    assert result.success is True
    assert result.output["skill_name"] == "plugin-spotify"


@pytest.mark.asyncio
async def test_a_music_skill_stays_when_nothing_is_connected(monkeypatch) -> None:
    """Nothing to route to: the skill the model named is loaded as before, and
    its own instructions tell the user the service is not connected."""
    from jarvis.core import music_service as ms

    monkeypatch.setattr(ms, "connected_music_services", lambda: ())
    monkeypatch.setattr(ms, "preferred_music_service", lambda: "auto")
    set_skill_context(SkillContext(registry=_music_registry(), runner=_FakeRunner()))  # type: ignore[arg-type]

    result = await RunSkillTool().execute({"skill_name": "plugin-spotify"}, _ctx())

    assert result.success is True
    assert result.output["skill_name"] == "plugin-spotify"
