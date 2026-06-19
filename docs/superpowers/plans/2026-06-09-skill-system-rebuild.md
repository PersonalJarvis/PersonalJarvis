# Skill System Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the skill system on the instruction-skill model (Anthropic Agent Skills standard) so skills actually fire: model-decided invocation, skill-aware routing guard, brain-routed direct triggers, inline/mission execution split, honest results.

**Architecture:** Spec: `docs/superpowers/specs/2026-06-09-skill-system-rebuild-design.md` (AD-S1..S8). Skill bodies become instructions the brain loads via `run-skill` and follows with its own tools; a deterministic guard makes matching skills win over force-spawn; the voice direct-trigger path routes through the brain instead of the macro runner.

**Tech Stack:** Python 3.11, Pydantic v2, pytest (asyncio_mode=auto), existing `jarvis/skills/` package.

**Conventions:** All new strings/comments English (CI language gate). TDD per task. Run `powershell -ExecutionPolicy Bypass -File scripts/preflight.ps1` once before starting (done: GREEN 2026-06-09).

---

### Task 1: Schema extensions (`when_to_use`, `execution`)

**Files:**
- Modify: `jarvis/skills/schema.py` (SkillFrontmatter, ~line 76-117)
- Test: `tests/unit/skills/test_schema_v2_fields.py` (new)

- [ ] **Step 1: Write failing tests**

```python
"""New optional frontmatter fields: when_to_use + execution (AD-S5/S7)."""
from jarvis.skills.schema import SkillFrontmatter


def _minimal(**kw):
    return SkillFrontmatter.model_validate({"schema_version": "1", "name": "x", **kw})


def test_when_to_use_defaults_to_none():
    assert _minimal().when_to_use is None


def test_when_to_use_roundtrip():
    fm = _minimal(when_to_use="Use when the user asks for a morning briefing.")
    assert fm.when_to_use.startswith("Use when")


def test_execution_defaults_to_inline():
    assert _minimal().execution == "inline"


def test_execution_mission_accepted():
    assert _minimal(execution="mission").execution == "mission"


def test_execution_invalid_rejected():
    import pytest
    with pytest.raises(Exception):
        _minimal(execution="background")
```

- [ ] **Step 2: Run** `pytest tests/unit/skills/test_schema_v2_fields.py -v` → FAIL (unknown field / attribute error)

- [ ] **Step 3: Implement** — add to `SkillFrontmatter`:

```python
    when_to_use: str | None = Field(
        default=None,
        description=(
            "Additional trigger guidance appended to the description in the "
            "AVAILABLE SKILLS listing (Anthropic Agent Skills convention)."
        ),
    )
    execution: Literal["inline", "mission"] = Field(
        default="inline",
        description=(
            "inline: the brain follows the skill instructions in the current "
            "turn. mission: the rendered body is dispatched as a background "
            "worker mission brief (AD-S5)."
        ),
    )
```

- [ ] **Step 4: Run** the new test file + `pytest tests/unit/skills/ -q` → PASS
- [ ] **Step 5: Commit** `feat(skills): when_to_use + execution frontmatter fields`

---

### Task 2: `SkillInvoked` event + runner instruction rendering + honesty fix

**Files:**
- Modify: `jarvis/skills/schema.py` (events block ~line 203-285), `jarvis/skills/runner.py`
- Test: `tests/unit/skills/test_runner_instructions.py` (new), extend `tests/unit/skills/` runner tests

- [ ] **Step 1: Failing tests**

```python
import pytest
from jarvis.skills.schema import SkillInvoked


def test_skill_invoked_event_frozen():
    ev = SkillInvoked(source_layer="brain.manager", skill_name="x", source="model")
    with pytest.raises(Exception):
        ev.skill_name = "y"  # type: ignore[misc]


# render_instructions: renders Jinja body without executing TOOL: lines
async def test_render_instructions_returns_rendered_body(make_skill, runner):
    skill = make_skill(body="Hello {{ config.city }}\nTOOL: remember {\"x\": 1}")
    text = runner.render_instructions(skill, args={})
    assert "Hello" in text and "TOOL:" in text  # body verbatim, not executed


# Honesty: legacy macro run with unresolvable tools must NOT report success
async def test_macro_run_with_unresolvable_tools_fails(make_skill, runner_empty_registry):
    skill = make_skill(body="TOOL: gmail-mcp/list_unread {}")
    result = await runner_empty_registry.run(skill, args={})
    assert result.success is False
    assert "gmail-mcp/list_unread" in (result.error or "")
```

(Reuse/define `make_skill` fixture in the test module following the pattern of the existing runner tests in `tests/unit/skills/` — parse a SKILL.md written to `tmp_path`.)

- [ ] **Step 2: Run** → FAIL (no `SkillInvoked`, no `render_instructions`, legacy run returns success)
- [ ] **Step 3: Implement**
  - `schema.py`: add frozen `SkillInvoked(Event)` with `skill_name: str`, `source: str` (one of `model|trigger|hotkey|cron|chat`; plain `str` field — single consumer, no wire crossing yet).
  - `runner.py`: add public `def render_instructions(self, skill, *, args: dict | None = None) -> str` that wraps the existing private Jinja `render()` (same context: `config`, time vars, `args`).
  - `runner.py` honesty fix: in the `TOOL:` loop, replace the silent `continue` on unresolved tool with collecting `skipped: list[str]`; after the loop, if `skipped` and not steps executed successfully overall → `success=False`, `error=f"unresolvable tools: {', '.join(skipped)}"`. A body with zero `TOOL:` lines stays `success=True` (pure-instruction bodies are legal).
- [ ] **Step 4: Run** new tests + `pytest tests/unit/skills/ -q` → PASS
- [ ] **Step 5: Commit** `feat(skills): SkillInvoked event, render_instructions, macro honesty fix`

---

### Task 3: `run-skill` becomes the instruction loader (AD-S1/S2/S5)

**Files:**
- Modify: `jarvis/plugins/tool/run_skill.py`
- Test: `tests/unit/brain/test_run_skill_tool.py` (extend)

- [ ] **Step 1: Failing tests** (extend the existing test module's fixtures — it already fakes a SkillContext):

```python
async def test_returns_instructions_not_macro_result(tool, ctx_with_skill):
    res = await tool.execute({"skill_name": "demo"}, ctx=None)
    assert res.success is True
    out = res.output
    assert out["instructions"].startswith("# ")          # rendered body
    assert out["execution"] == "inline"
    assert "Follow these skill instructions now" in out["directive"]


async def test_mission_skill_returns_mission_directive(tool, ctx_with_mission_skill):
    res = await tool.execute({"skill_name": "heavy"}, ctx=None)
    assert res.output["execution"] == "mission"
    assert "spawn_worker" in res.output["directive"]


async def test_resource_loading(tool, ctx_with_skill_with_resource):
    res = await tool.execute(
        {"skill_name": "demo", "resource": "references/guide.md"}, ctx=None)
    assert res.success and "guide content" in res.output["resource_content"]


async def test_resource_path_traversal_rejected(tool, ctx_with_skill):
    res = await tool.execute(
        {"skill_name": "demo", "resource": "../../secrets.txt"}, ctx=None)
    assert res.success is False


async def test_skill_invoked_event_published(tool, ctx_with_skill, fake_bus):
    await tool.execute({"skill_name": "demo"}, ctx=None)
    assert any(type(e).__name__ == "SkillInvoked" for e in fake_bus.published)
```

- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** — keep Steps 1-5 (validation, context, resolve, AP-15 draft/disabled, block tier) byte-compatible. Replace Steps 6-7:

```python
        # Step 6 — optional L3 resource read (progressive disclosure)
        resource_rel = args.get("resource")
        if isinstance(resource_rel, str) and resource_rel.strip():
            return self._read_resource(skill, resource_rel.strip())

        # Step 6b — render instructions (AD-S1: instruction-skill model)
        skill_args = args.get("args") or {}
        if not isinstance(skill_args, dict):
            skill_args = {}
        try:
            instructions = skill_ctx.runner.render_instructions(skill, args=skill_args)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output=None,
                              error=f"{type(exc).__name__}: {exc}")

        execution = "inline"
        if skill.frontmatter is not None:
            execution = skill.frontmatter.execution
        if execution == "mission":
            directive = (
                "This skill runs as a background mission. Call the spawn_worker "
                "tool NOW with the instructions below as the task text, then "
                "give the user a short optimistic acknowledgement."
            )
        else:
            directive = (
                "These are the skill's instructions. Follow these skill "
                "instructions now, step by step, using your available tools. "
                "Then answer the user with the result — never read the raw "
                "instructions aloud."
            )
        await self._publish_invoked(skill_name, source="model")
        resources = {k: list(v) for k, v in skill.resources.items() if v}
        return ToolResult(
            success=True,
            output={
                "skill_name": skill_name,
                "execution": execution,
                "directive": directive,
                "instructions": instructions,
                "resources": resources,  # loadable via the `resource` argument
            },
            error=None,
        )
```

  Plus `_read_resource()` (resolve against `skill.root`, `Path.resolve()`, require `is_relative_to(skill.root.resolve())` AND membership in `skill.resources` lists; read UTF-8, cap at 64 KB) and `_publish_invoked()` (best-effort `self._bus.publish(SkillInvoked(...))`, swallow errors; bus arrives via the existing `bus=` constructor kwarg — factory already passes it where available). Update the tool `description` + `schema` (add `resource` property) to describe the instruction-loader semantics. Update the module docstring (D9 note stays: instructions execute through the router tool loop, `run-skill` output is data, recursion guard unchanged).

- [ ] **Step 4: Run** `pytest tests/unit/brain/test_run_skill_tool.py tests/unit/skills/test_runner_d9_block.py -v` → PASS
- [ ] **Step 5: Commit** `feat(skills): run-skill returns instructions (instruction-skill model, L3 resources)`

---

### Task 4: Prompt listing upgrade (AD-S2 L1)

**Files:**
- Modify: `jarvis/skills/prompt_injection.py`
- Test: `tests/unit/skills/test_prompt_injection.py` (extend)

- [ ] **Step 1: Failing tests**

```python
def test_when_to_use_appended(registry_with):
    reg = registry_with(description="Does X.", when_to_use="Use when Y.")
    out = render_available_skills_section(reg)
    assert "Does X. Use when Y." in out


def test_per_entry_char_cap(registry_with):
    reg = registry_with(description="A" * 3000)
    out = render_available_skills_section(reg)
    line = next(l for l in out.splitlines() if l.startswith("- `"))
    assert len(line) <= 1600  # 1536 + bullet/name overhead


def test_framing_mentions_instruction_loading(registry_with):
    out = render_available_skills_section(registry_with())
    assert "run-skill" in out and "instructions" in out
```

- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** — per bullet: `text = description; if fm.when_to_use: text += " " + when_to_use`; truncate `text` to 1536 chars with `…`. Replace the German intro/outro with English framing (CI language gate):

```python
    intro = (
        "The user has these skills installed. When a request matches a "
        "skill's description, call the `run-skill` tool with its name — the "
        "tool returns the skill's instructions for you to follow:\n"
    )
    outro = (
        "\n\nIf several skills could match, pick the most specific one. "
        "Draft/disabled skills are rejected by the tool automatically."
    )
```

  Keep `max_skills` cap + overflow tail (tail bullet text → English: `f"- … and {overflow} more"`).
- [ ] **Step 4: Run** `pytest tests/unit/skills/test_prompt_injection.py tests/integration/test_skill_listing_in_prompt.py -v` → PASS (fix integration assertions if they pin the old German strings)
- [ ] **Step 5: Commit** `feat(skills): listing renders when_to_use, per-entry cap, instruction framing`

---

### Task 5: Skill-aware routing guard in BrainManager (AD-S3) — the RC1 fix

**Files:**
- Modify: `jarvis/brain/manager.py` (`_should_force_spawn` ~1890, `generate()` ~2780-3010, smalltalk override ~1786)
- Test: `tests/unit/brain/test_skill_routing_guard.py` (new), `tests/unit/brain/test_routing.py` (append cases)

- [ ] **Step 1: Failing tests** (follow the fixture style of `tests/unit/brain/test_routing.py` — it builds a BrainManager with fake tools/config):

```python
# 1. Utterance matching an active skill must not force-spawn
def test_skill_match_blocks_force_spawn(manager_with_skill_ctx):
    m = manager_with_skill_ctx(trigger=r"morgenroutine|morning routine")
    assert m._should_force_spawn("starte die Morgenroutine") is False

# 2. Hard negatives keep spawning (no skill match)
def test_non_skill_action_still_spawns(manager_with_skill_ctx):
    m = manager_with_skill_ctx(trigger=r"morgenroutine")
    assert m._should_force_spawn("mach einen deep dive ins Repo via OpenClaw") is True

# 3. Matched turn guarantees run-skill in the tool set even on smalltalk
def test_smalltalk_override_keeps_run_skill_on_skill_turn(manager_with_skill_ctx):
    m = manager_with_skill_ctx(trigger=r"guten morgen")
    m._skill_turn_match = m._match_skill_for_turn("guten morgen")
    tools = m._smalltalk_tool_override()
    assert "run-skill" in tools

# 4. Steering hint lands in turn context
def test_turn_context_contains_steering_hint(manager_with_skill_ctx):
    m = manager_with_skill_ctx(trigger=r"guten morgen")
    m._skill_turn_match = m._match_skill_for_turn("guten morgen")
    assert "morning-routine" in m._render_skill_turn_hint()
```

- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** in `manager.py`:

```python
    def _match_skill_for_turn(self, user_text: str, lang: str = "auto"):
        """Deterministic skill-match probe (AD-S3). Returns the matched Skill or None.

        Uses the TriggerMatcher (incl. its tolerant filler-stripping pass) over
        the live SkillContext registry. Never raises — routing must not break
        when the skill subsystem is absent (headless/mock boots).
        """
        try:
            from jarvis.skills.skill_context import try_get_skill_context
            from jarvis.skills.trigger_matcher import TriggerMatcher

            ctx = try_get_skill_context()
            if ctx is None:
                return None
            res = TriggerMatcher(ctx.registry).match_voice_with_match(
                user_text, lang=lang
            )
            return res[0] if res is not None else None
        except Exception:  # noqa: BLE001
            return None
```

  - In `_should_force_spawn`, after the `is_open_app_intent` guard (~line 1990): `if self._skill_turn_match is not None or self._match_skill_for_turn(t) is not None: return False` — with a `log.info("force-spawn skipped: utterance matches skill %s", ...)`.
  - In `generate()` right before the `unsupported = self._check_unsupported_intent(...)` gate (~line 2771): `self._skill_turn_match = self._match_skill_for_turn(user_text)`; when set, also skip `_check_unsupported_intent` (a skill IS the capability) and append `self._render_skill_turn_hint()` to `turn_context` (built ~line 2907). Reset `self._skill_turn_match = None` in the `finally` block of `generate()` (same place `_wiki_context_suffix` resets).
  - `_render_skill_turn_hint()` returns: `f'[Skill match] The user\'s request matches the installed skill `{name}` — call the run-skill tool with skill_name="{name}" now unless that is clearly wrong.'`
  - `_smalltalk_tool_override()`: when `self._skill_turn_match is not None`, include `"run-skill"` in the allowed names.
- [ ] **Step 4: Run** `pytest tests/unit/brain/test_skill_routing_guard.py tests/unit/brain/test_routing.py -v` → PASS (existing 26-case suite must stay green)
- [ ] **Step 5: Commit** `feat(brain): skill-aware routing guard — skills win over force-spawn (AD-S3)`

---

### Task 6: Direct triggers route through the brain (AD-S4) + mission dispatch (AD-S5) — the RC3 fix

**Files:**
- Modify: `jarvis/speech/pipeline.py` (`_try_skill_direct_trigger` ~1664-1715), `jarvis/ui/desktop_app.py` (chat hook ~699-731), `jarvis/brain/manager.py` (forced-skill injection + mission dispatch)
- Test: `tests/unit/speech/test_pipeline_skill_hook.py` (rewrite), `tests/unit/brain/test_skill_forced_turn.py` (new)

- [ ] **Step 1: Failing tests**

```python
# pipeline: trigger match no longer macro-runs; it primes the brain turn
async def test_trigger_match_primes_brain_and_returns_false(pipeline_with_skill):
    p, brain = pipeline_with_skill(trigger=r"guten morgen")
    handled = await p._try_skill_direct_trigger("guten morgen", "de")
    assert handled is False                      # brain path continues
    assert brain.pending_forced_skill == "morning-routine"

# manager: forced skill injects rendered instructions into turn context
async def test_forced_skill_instructions_in_turn_context(manager_with_skill_ctx):
    m = manager_with_skill_ctx(body="# Morning\nDo the briefing.")
    m.note_skill_trigger("morning-routine", content="", source="trigger")
    ctx = m._consume_forced_skill_context()
    assert "Do the briefing." in ctx and "Follow these skill instructions" in ctx

# manager: mission-mode skill dispatches spawn instead of inline injection
async def test_mission_skill_dispatches_worker(manager_with_mission_skill):
    m, spawn_calls = manager_with_mission_skill()
    m.note_skill_trigger("heavy-skill", content="", source="trigger")
    reply = await m.generate("starte den heavy skill", ...)
    assert spawn_calls and "heavy-skill" in spawn_calls[0]
```

- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement**
  - `manager.py`: `note_skill_trigger(skill_name: str, *, content: str = "", source: str = "trigger") -> None` stores `self._pending_forced_skill = (skill_name, content, source)`. `_consume_forced_skill_context() -> str | None`: pops the pending tuple, resolves the skill via `try_get_skill_context()`, renders via `runner.render_instructions(skill, args={"content": content, "_trigger": source})`, publishes `SkillInvoked(source=source)`, returns `f"[Skill instructions for `{name}` — the user's request triggered this skill]\n{body}\n\nFollow these skill instructions now using your tools; answer with the result, never read them aloud."`. On render failure → log + return `None` (turn proceeds normally).
  - In `generate()`: after `turn_context = self._build_turn_context()` (~line 2907) — `forced = self._consume_forced_skill_context()`; if the pending skill has `execution == "mission"`, instead call `await self._force_spawn_worker(mission_text, trace_id=...)` where `mission_text = rendered body` (add an optional `task_override: str | None = None` parameter to `_force_spawn_worker` that replaces the task text sent to spawn_worker while keeping ACK behavior); on dispatch failure fall back to inline injection (AD-OE6: no silent drop). For inline: `turn_context = f"{turn_context}\n\n{forced}" if turn_context else forced`.
  - `pipeline.py` `_try_skill_direct_trigger`: keep context/matcher/match + `_emit_skill_direct`; replace the runner-run+TTS block with `self._brain.note_skill_trigger(matched.name, content=content, source="trigger")` (guard `hasattr`), then `return False`. Update the docstring (returns False on match now — the brain turn carries the skill).
  - `desktop_app.py` chat hook (~699-731): same replacement, `source="chat"`.
- [ ] **Step 4: Run** `pytest tests/unit/speech/test_pipeline_skill_hook.py tests/unit/brain/test_skill_forced_turn.py tests/integration/test_skill_trigger_e2e.py -v` → PASS (update the e2e integration test to the new contract: trigger → brain turn with instructions, not macro+TTS)
- [ ] **Step 5: Commit** `feat(skills): direct triggers route through the brain; mission skills dispatch workers (AD-S4/S5)`

---

### Task 7: Boot-race fix + omit warning (AD-S6) — the RC2 fix

**Files:**
- Modify: `jarvis/brain/factory.py` (inside `build_default_brain`, near the capability seed ~1254), `jarvis/brain/manager.py` (`_build_system_prompt` skills block ~1268-1280), `jarvis/ui/desktop_app.py` (~1459-1521)
- Test: `tests/unit/brain/test_skill_context_boot.py` (new)

- [ ] **Step 1: Failing tests**

```python
def test_factory_sets_skill_context(monkeypatch, tmp_path):
    from jarvis.skills.skill_context import try_get_skill_context, set_skill_context
    set_skill_context(None)
    build_default_brain(...minimal fake config/fixtures as in existing factory tests...)
    assert try_get_skill_context() is not None

def test_prompt_build_warns_once_when_ctx_missing(caplog, manager_no_skill_ctx):
    manager_no_skill_ctx._build_system_prompt()
    manager_no_skill_ctx._build_system_prompt()
    warns = [r for r in caplog.records if "skills section omitted" in r.message]
    assert len(warns) == 1
```

- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement**
  - `factory.py`: after the capability seed block — if `try_get_skill_context() is None`, build `SkillRegistry(root=ensure_user_skills_dir(), bus=bus, state_prefs_loader=prefs.load_state_overrides)` + `reload_sync()` + `SkillRunner(registry=..., tool_registry={}, bus=bus)` + `set_skill_context(...)`, wrapped in `try/except` with a WARNING. (Empty tool_registry is fine — the instruction model does not execute TOOL: lines; the desktop app later re-sets the context with the server registry + populated runner, which stays authoritative.)
  - `desktop_app.py`: keep the existing block (it re-sets the context with the shared server registry — now an idempotent upgrade, update the comment).
  - `manager.py` skills block: in the `if _skill_ctx is None`/exception path, log `log.warning("skills section omitted: skill context not initialized")` guarded by `self._skills_omit_warned` (instance bool, set once).
- [ ] **Step 4: Run** new tests + `pytest tests/integration/test_skill_listing_in_prompt.py -q` → PASS
- [ ] **Step 5: Commit** `fix(skills): skill context set at brain build time; warn once when listing omitted (AD-S6)`

---

### Task 8: Bootstrap v3 refresh (AD-S8)

**Files:**
- Modify: `jarvis/skills/bootstrap.py`
- Test: `tests/unit/skills/test_bootstrap_refresh.py` (new)

- [ ] **Step 1: Failing tests**

```python
def test_unedited_builtin_gets_refreshed(tmp_user_dir_with_v2_copy):
    # user copy SHA matches the known v2 hash → overwritten with current builtin
def test_edited_builtin_left_alone(tmp_user_dir_with_edited_copy):
    # user copy SHA unknown → untouched, logged
def test_manifest_written(tmp_user_dir):
    # .shipped-hashes.json exists and maps every builtin to the SKILL.md sha256
```

- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** — bump `BOOTSTRAP_VERSION` to `"3"`. Compute the v2 hash map ONCE during implementation (`python - <<'EOF' ... hashlib.sha256 of each current builtin SKILL.md BEFORE Task 9 rewrites them ... EOF`) and embed as `_V2_SHIPPED_HASHES: dict[str, str]`. Refresh rule per builtin: read user copy SKILL.md hash; if hash ∈ {v2 hash, manifest hash} → `shutil.copy2` the current builtin SKILL.md (+ sync bundle dirs); else log `info("builtin %s user-edited — not refreshed", name)`. Always (re)write `.shipped-hashes.json` with the now-shipped hashes. Keep the existing gap-fill behavior for missing skills.
- [ ] **Step 4: Run** `pytest tests/unit/skills/test_bootstrap_refresh.py tests/unit/skills/ -q` → PASS
- [ ] **Step 5: Commit** `feat(skills): bootstrap v3 — hash-guarded builtin refresh + shipped-hashes manifest`

> ORDER NOTE: capture the v2 hashes (Step 3 inline script) BEFORE Task 9 rewrites the builtin files.

---

### Task 9: Builtin content migration (AD-S7)

**Files:**
- Modify: all 18 `jarvis/skills/builtin/*/SKILL.md`
- Test: `tests/unit/skills/test_builtin_skills.py` (extend with the lint suite)

- [ ] **Step 1: Failing lint tests**

```python
import re
from pathlib import Path
from jarvis.skills.loader import parse_skill

BUILTIN_ROOT = Path("jarvis/skills/builtin")
SKILLS = sorted(p for p in BUILTIN_ROOT.iterdir() if (p / "SKILL.md").exists())


@pytest.mark.parametrize("root", SKILLS, ids=lambda p: p.name)
def test_builtin_meets_instruction_standard(root):
    sk = parse_skill(root / "SKILL.md")
    fm = sk.frontmatter
    assert fm is not None, sk.error
    assert len(fm.description) <= 1024
    combined = fm.description + " " + (fm.when_to_use or "")
    assert "use when" in combined.lower()          # pushy trigger clause
    assert len(sk.body.splitlines()) <= 500
    assert "-mcp/" not in sk.body                   # no fictional MCP tool names
    assert not re.search(r"^\s*TOOL:", sk.body, re.M)  # instruction model, no macros
```

- [ ] **Step 2: Run** → FAIL for every legacy builtin
- [ ] **Step 3: Rewrite the builtins.** Template (all-English):

```markdown
---
schema_version: "1"
name: morning-routine
version: "2.0.0"
description: >-
  Delivers the user's spoken morning briefing: today's calendar, unread
  email summary, weather, and open tasks. Use when the user asks for a
  morning briefing, says good morning and wants their day overview, or
  asks "what's on today".
when_to_use: >-
  Use for "starte die Morgenroutine", "guten Morgen, wie sieht mein Tag
  aus", "morning briefing", "what's my day looking like".
category: productivity
triggers:
  - type: voice
    pattern: "(morgenroutine|morgen[-\\s]?briefing|morning routine|morning briefing)"
    language: [de, en]
risk_policy:
  default_tier: monitor
execution: inline
---

# Morning Routine

Deliver a short spoken morning briefing. Work through these steps with the
tools you have; skip a step gracefully (one short sentence) when its
integration is not connected.

1. Calendar: if a calendar tool or connected plugin is available, fetch
   today's events; otherwise say you cannot see the calendar yet.
2. Email: if the Gmail plugin is connected, summarize unread mail counts
   and the 2-3 most important senders/subjects. Never read full bodies.
3. Weather: if a web/search tool is available, get today's weather for the
   user's city; otherwise skip silently.
4. Compose ONE flowing, friendly briefing (3-5 sentences max, no lists,
   no markdown) and answer with it.
```

  Per-skill notes: `morning-routine`, `deep-work-mode`, `control-api`, `jarvis-doc-author`, `skill-creator` → full instruction bodies referencing only real router tools (`navigate`, `screenshot`, plugin tools by their real namespaced names, `wiki-ingest`, `set_config_value`, …). `memory-save` keeps `state: disabled` + gets the format update. The 12 `plugin-*` skills: keep `plugin_id`/`intent_verbs`/`intent_objects`/voice patterns verbatim (capability pairing contract — DO NOT touch, see `tests/unit/skills/test_skill_context_paired_caps.py`); body becomes 5-15 lines: "Use the connected <X> plugin's tools (namespaced `<id>/...`) to fulfill the request; if the plugin is not connected, tell the user to connect it in Settings → Plugins." Unanchored trigger patterns: relax remaining `^...$` anchors to substring patterns while keeping them specific (the R10 anti-hijack rule: compound terms like `fokusmodus`, never bare `fokus`).
- [ ] **Step 4: Run** `pytest tests/unit/skills/test_builtin_skills.py tests/unit/skills/test_skill_context_paired_caps.py -v` → PASS
- [ ] **Step 5: Commit** `feat(skills): migrate all builtin skills to the instruction-skill format`

---

### Task 10: Full verification sweep

- [ ] **Step 1:** `pytest tests/unit/skills/ tests/unit/brain/ tests/unit/speech/test_pipeline_skill_hook.py tests/integration/test_skill_listing_in_prompt.py tests/integration/test_skill_trigger_e2e.py -q` → all green; fix regressions.
- [ ] **Step 2:** `pytest tests/ -q -m "not slow and not openclaw_live and not skip_ci"` (or the closest passing baseline — record pre-existing failures first via `git stash` discipline if needed).
- [ ] **Step 3:** `ruff check jarvis/ && ruff format --check jarvis/`
- [ ] **Step 4:** `pip install -e . --no-deps` (entry-point hygiene) + `python -c "import jarvis; print(jarvis.__file__)"`
- [ ] **Step 5:** Commit any fixes; write the live verification checklist (spec §10) into the final user summary.
