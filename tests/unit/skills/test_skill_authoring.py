"""Tests for the skill-authoring pipeline (Phase 7.5).

Plan acceptance criteria §7.5:
- Spawn produces a valid SKILL.md in draft state
- state=draft is forced even when Sub-Jarvis wrongly writes `active`
- Validation retry works with concrete Pydantic feedback
- Name clash produces a suffix, no overwrite
- Draft skill doesn't trigger (negative test with TriggerMatcher)
- Audit entry contains an iterations counter
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml

from jarvis.core.self_mod import SelfModAudit
from jarvis.skills.authoring import (
    AuthoringFailure,
    AuthoringSuccess,
    SkillAuthoringRunner,
    SkillDraft,
    safe_lint_skill_body,
    write_draft,
)
from jarvis.skills.authoring.draft_writer import SlugError

# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def audit(tmp_path: Path) -> SelfModAudit:
    return SelfModAudit(path=tmp_path / "audit.log")


@pytest.fixture
def user_skills_root(tmp_path: Path) -> Path:
    root = tmp_path / "user_skills"
    root.mkdir()
    return root


def _draft(**overrides) -> SkillDraft:
    defaults: dict = dict(
        slug="spotify-auto-pause",
        name="Spotify Auto-Pause",
        description="Pauses Spotify when the user talks.",
        intent="user wants spotify auto-pause",
        triggers_yaml="[{type: voice, pattern: '^pause spotify'}]",
        requires_tools=["run-shell"],
        body_markdown="## Spotify Auto-Pause\n\nThis skill ...",
        state="draft",
    )
    defaults.update(overrides)
    return SkillDraft(**defaults)


def _good_response_json(**overrides) -> str:
    payload = _draft(**overrides).model_dump()
    return json.dumps(payload, ensure_ascii=False)


# ----------------------------------------------------------------------
# SkillDraft Schema (Plan-§AD-9 + Slug-Hardening)
# ----------------------------------------------------------------------


class TestSkillDraftSchema:
    def test_valid_draft_passes(self) -> None:
        draft = _draft()
        assert draft.slug == "spotify-auto-pause"

    def test_slug_traversal_rejected(self) -> None:
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError):
            SkillDraft(
                slug="../../etc/passwd",
                name="x",
                description="x",
                intent="x",
                body_markdown="x",
            )

    def test_slug_special_chars_rejected(self) -> None:
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError):
            SkillDraft(
                slug="some/slash",
                name="x",
                description="x",
                intent="x",
                body_markdown="x",
            )

    def test_slug_normalized_lowercase(self) -> None:
        draft = SkillDraft(
            slug="MIXED-Case",
            name="x",
            description="x",
            intent="x",
            body_markdown="x",
        )
        assert draft.slug == "mixed-case"


# ----------------------------------------------------------------------
# write_draft — state=draft Forcierung (Plan-§AD-8)
# ----------------------------------------------------------------------


class TestWriteDraft:
    def test_writes_skill_md_with_state_draft(
        self, user_skills_root: Path
    ) -> None:
        draft = _draft()
        result = write_draft(draft, user_skills_root=user_skills_root)
        assert result.draft_path.exists()
        text = result.draft_path.read_text(encoding="utf-8")
        # Parse frontmatter
        fm_text = text.split("---", 2)[1]
        fm = yaml.safe_load(fm_text)
        assert fm["state"] == "draft"
        assert fm["name"] == "Spotify Auto-Pause"

    def test_state_active_in_draft_is_forced_to_draft(
        self, user_skills_root: Path
    ) -> None:
        """Plan-§AD-8: Sub-Jarvis output state="active" is unconditionally
        overwritten to "draft" when writing.
        """
        draft = _draft(state="active")
        result = write_draft(draft, user_skills_root=user_skills_root)
        assert result.forced_state_override is True
        text = result.draft_path.read_text(encoding="utf-8")
        fm = yaml.safe_load(text.split("---", 2)[1])
        assert fm["state"] == "draft"

    def test_clash_creates_suffix(self, user_skills_root: Path) -> None:
        draft = _draft()
        first = write_draft(draft, user_skills_root=user_skills_root)
        second = write_draft(draft, user_skills_root=user_skills_root)
        assert first.slug == "spotify-auto-pause"
        assert second.slug == "spotify-auto-pause_2"
        # Both exist
        assert first.draft_path.exists()
        assert second.draft_path.exists()
        # The first one was NOT overwritten
        assert (user_skills_root / "spotify-auto-pause" / "SKILL.md").exists()


# ----------------------------------------------------------------------
# Path-Traversal-Defense
# ----------------------------------------------------------------------


class TestPathTraversalDefense:
    def test_resolved_target_blocks_traversal(
        self, user_skills_root: Path
    ) -> None:
        from jarvis.skills.authoring.draft_writer import _resolved_target

        with pytest.raises(SlugError):
            _resolved_target("../etc/passwd", root=user_skills_root)

    def test_traversal_via_dotdot_in_resolved(
        self, user_skills_root: Path
    ) -> None:
        from jarvis.skills.authoring.draft_writer import _resolved_target

        with pytest.raises(SlugError):
            _resolved_target("..\\..\\windows\\system32", root=user_skills_root)


# ----------------------------------------------------------------------
# safe_lint_skill_body (Plan-§7.5 Sicherheits-Lint)
# ----------------------------------------------------------------------


class TestSafeLint:
    def test_clean_body_no_findings(self) -> None:
        body = "## Title\n\n```python\nimport json\nimport re\n```"
        assert safe_lint_skill_body(body) == []

    def test_eval_blocked(self) -> None:
        body = "## Title\n\n```python\neval('1+1')\n```"
        assert "forbidden_call: eval" in safe_lint_skill_body(body)

    def test_exec_blocked(self) -> None:
        body = "## Title\n\n```python\nexec('print(1)')\n```"
        assert "forbidden_call: exec" in safe_lint_skill_body(body)

    def test_os_system_blocked(self) -> None:
        body = "```python\nimport os\nos.system('rm -rf /')\n```"
        findings = safe_lint_skill_body(body)
        assert any("forbidden_call: os.system" in f for f in findings)

    def test_subprocess_shell_true_blocked(self) -> None:
        body = (
            "```python\nimport subprocess\n"
            "subprocess.run('ls', shell=True)\n```"
        )
        findings = safe_lint_skill_body(body)
        assert any("subprocess_shell_true" in f for f in findings)

    def test_forbidden_import(self) -> None:
        body = "```python\nimport ctypes\n```"
        findings = safe_lint_skill_body(body)
        assert any("forbidden_import" in f for f in findings)

    def test_no_python_block_no_findings(self) -> None:
        body = "## Pure Markdown\n\nNo code here."
        assert safe_lint_skill_body(body) == []

    # Sub-Agent-Review-MAJOR-Hardening (Phase 7.5)

    def test_codefence_python3_tag_caught(self) -> None:
        body = "```python3\neval('boom')\n```"
        assert "forbidden_call: eval" in safe_lint_skill_body(body)

    def test_codefence_no_lang_python_caught(self) -> None:
        body = "```\nimport os\nos.system('whoami')\n```"
        findings = safe_lint_skill_body(body)
        assert any("os.system" in f for f in findings)

    def test_tilde_fence_caught(self) -> None:
        body = "~~~python\neval('boom')\n~~~"
        assert "forbidden_call: eval" in safe_lint_skill_body(body)

    def test_getattr_bypass_caught(self) -> None:
        body = (
            "```python\n"
            "fn = getattr(__builtins__, 'eval')\n"
            "fn('1+1')\n"
            "```"
        )
        findings = safe_lint_skill_body(body)
        assert any("forbidden_call: getattr" in f for f in findings)

    def test_subscript_builtins_bypass_caught(self) -> None:
        body = (
            "```python\n"
            "__builtins__['eval']('1+1')\n"
            "```"
        )
        findings = safe_lint_skill_body(body)
        assert any("forbidden_subscript_call" in f for f in findings)

    def test_globals_subscript_bypass_caught(self) -> None:
        body = (
            "```python\n"
            "globals()['eval']('boom')\n"
            "```"
        )
        findings = safe_lint_skill_body(body)
        assert any("forbidden_subscript_call" in f for f in findings)

    def test_os_exec_blocked(self) -> None:
        body = "```python\nimport os\nos.execvp('/bin/sh', ['sh'])\n```"
        findings = safe_lint_skill_body(body)
        assert any("os.execvp" in f for f in findings)

    def test_plain_text_codefence_ignored(self) -> None:
        """A codefence with a language tag that is NOT Python (e.g. `bash`,
        `json`) is NOT AST-parsed."""
        body = "```bash\necho 'eval is just a word here'\n```"
        assert safe_lint_skill_body(body) == []


# ----------------------------------------------------------------------
# SkillAuthoringRunner — Sub-Jarvis-Mock + Validation-Loop
# ----------------------------------------------------------------------


class TestRunnerHappyPath:
    def test_authoring_success(
        self, user_skills_root: Path, audit: SelfModAudit
    ) -> None:
        async def fake_spawn(prompt: str) -> str:
            return _good_response_json()

        runner = SkillAuthoringRunner(
            spawn_callback=fake_spawn,
            audit=audit,
            user_skills_root=user_skills_root,
        )
        result = asyncio.run(runner.author("Pause Spotify when I talk"))
        assert isinstance(result, AuthoringSuccess)
        assert result.iterations == 1
        assert result.draft_path.exists()
        assert result.forced_state_override is False

    def test_audit_contains_iterations_counter(
        self, user_skills_root: Path, audit: SelfModAudit
    ) -> None:
        async def fake_spawn(prompt: str) -> str:
            return _good_response_json()

        runner = SkillAuthoringRunner(
            spawn_callback=fake_spawn,
            audit=audit,
            user_skills_root=user_skills_root,
        )
        asyncio.run(runner.author("intent text"))
        entries = [
            json.loads(line)
            for line in audit.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        success = [e for e in entries if e["ok"] is True]
        assert len(success) == 1
        assert success[0]["iterations"] == 1
        assert success[0]["type"] == "skill_authored"


class TestRunnerForcedStateOverride:
    def test_subjarvis_active_yields_forced_override_audit(
        self, user_skills_root: Path, audit: SelfModAudit
    ) -> None:
        """Plan-AC §7.5: state=draft is forced even when Sub-Jarvis returns 'active'."""
        async def fake_spawn(prompt: str) -> str:
            return _good_response_json(state="active")

        runner = SkillAuthoringRunner(
            spawn_callback=fake_spawn,
            audit=audit,
            user_skills_root=user_skills_root,
        )
        result = asyncio.run(runner.author("intent text"))
        assert isinstance(result, AuthoringSuccess)
        assert result.forced_state_override is True
        # The written file has state: draft
        text = result.draft_path.read_text(encoding="utf-8")
        fm = yaml.safe_load(text.split("---", 2)[1])
        assert fm["state"] == "draft"
        # Audit entry documents the override
        entries = [
            json.loads(line)
            for line in audit.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        success = [e for e in entries if e["ok"] is True]
        assert success[0]["forced_state_override"] is True


class TestRunnerParseFailure:
    def test_invalid_json_audits_parse_failure(
        self, user_skills_root: Path, audit: SelfModAudit
    ) -> None:
        async def fake_spawn(prompt: str) -> str:
            return "this is not json at all"

        runner = SkillAuthoringRunner(
            spawn_callback=fake_spawn,
            audit=audit,
            user_skills_root=user_skills_root,
        )
        result = asyncio.run(runner.author("intent text"))
        assert isinstance(result, AuthoringFailure)
        assert result.error_kind == "parse_failed"
        # No draft in the skills root
        drafts = list(user_skills_root.rglob("SKILL.md"))
        assert drafts == []
        # Audit "author_failed_parse"
        entries = [
            json.loads(line)
            for line in audit.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert any(e["error"] == "author_failed_parse" for e in entries)

    def test_validation_loop_retries_up_to_3_times(
        self, user_skills_root: Path, audit: SelfModAudit
    ) -> None:
        attempts: list[str] = []

        async def fake_spawn(prompt: str) -> str:
            attempts.append(prompt)
            # First 2 attempts: invalid; 3rd attempt: valid
            if len(attempts) < 3:
                return "garbage"
            return _good_response_json()

        runner = SkillAuthoringRunner(
            spawn_callback=fake_spawn,
            audit=audit,
            user_skills_root=user_skills_root,
        )
        result = asyncio.run(runner.author("intent text"))
        assert isinstance(result, AuthoringSuccess)
        assert result.iterations == 3
        assert len(attempts) == 3


class TestRunnerUnsafeBody:
    def test_unsafe_body_blocks_write(
        self, user_skills_root: Path, audit: SelfModAudit
    ) -> None:
        unsafe_body = (
            "## Spotify Eval\n\n"
            "```python\n"
            "eval('print(\"hi\")')\n"
            "```\n"
        )

        async def fake_spawn(prompt: str) -> str:
            return _good_response_json(body_markdown=unsafe_body)

        runner = SkillAuthoringRunner(
            spawn_callback=fake_spawn,
            audit=audit,
            user_skills_root=user_skills_root,
        )
        result = asyncio.run(runner.author("intent text"))
        assert isinstance(result, AuthoringFailure)
        assert result.error_kind == "unsafe"
        # No draft written
        assert list(user_skills_root.rglob("SKILL.md")) == []


# ----------------------------------------------------------------------
# Brain-Tool-Integration
# ----------------------------------------------------------------------


class TestSpawnSkillAuthorTool:
    def test_tool_schema_strict_mode(self) -> None:
        from jarvis.brain.tools import SpawnSkillAuthorTool

        schema = SpawnSkillAuthorTool.schema
        assert schema["strict"] is True
        assert schema["additionalProperties"] is False
        # Plan-§AD-9: all properties in `required`
        properties = set(schema["properties"].keys())
        required = set(schema["required"])
        assert properties == required
        # Plan: ≥2 input_examples
        assert len(schema["input_examples"]) >= 2

    def test_tool_returns_authoring_success(
        self, user_skills_root: Path, audit: SelfModAudit
    ) -> None:
        from uuid import uuid4

        from jarvis.brain.tools import SpawnSkillAuthorTool
        from jarvis.core.protocols import ExecutionContext

        async def fake_spawn(prompt: str) -> str:
            return _good_response_json()

        runner = SkillAuthoringRunner(
            spawn_callback=fake_spawn,
            audit=audit,
            user_skills_root=user_skills_root,
        )
        tool = SpawnSkillAuthorTool(runner=runner)
        ctx = ExecutionContext(
            trace_id=uuid4(),
            user_utterance="erstell einen Spotify-Skill",
            config={},
            memory_read=None,
            approved_by="auto",
        )
        result = asyncio.run(
            tool.execute(
                {
                    "intent": "Pause Spotify when I talk",
                    "suggested_name": "spotify-auto-pause",
                    "trigger_hint": "voice 'pause spotify'",
                },
                ctx,
            )
        )
        assert result.success is True
        assert result.output["slug"] == "spotify-auto-pause"

    def test_tool_rejects_empty_intent(self) -> None:
        from uuid import uuid4

        from jarvis.brain.tools import SpawnSkillAuthorTool
        from jarvis.core.protocols import ExecutionContext

        async def fake_spawn(prompt: str) -> str:
            return _good_response_json()

        runner = SkillAuthoringRunner(
            spawn_callback=fake_spawn,
            audit=SelfModAudit(),
            user_skills_root=None,
        )
        tool = SpawnSkillAuthorTool(runner=runner)
        ctx = ExecutionContext(
            trace_id=uuid4(),
            user_utterance="",
            config={},
            memory_read=None,
            approved_by="auto",
        )
        result = asyncio.run(
            tool.execute(
                {"intent": "", "suggested_name": "", "trigger_hint": ""},
                ctx,
            )
        )
        assert result.success is False
        assert "invalid_input" in result.error
