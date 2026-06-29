# Plugin↔Skill Pairing & Reachability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every *connected* marketplace plugin reliably reachable by voice/chat, and establish a binding convention that every plugin ships a paired, AI-authored skill that is the single source of truth for its intent vocabulary.

**Architecture:** A `SKILL.md` becomes the canonical source per plugin. It gains three optional frontmatter fields — `plugin_id`, `intent_verbs`, `intent_objects`. A new deterministic generator turns those fields into a `Capability` (registered at boot for built-ins and live on plugin-connect for marketplace plugins). Because `resolve_intent() != None` simultaneously silences the UNSUPPORTED refusal AND the force-spawn weiche (`capabilities.py:165`, `manager.py:1896`, `local_action_gate.py:411`), a paired skill is all it takes to make a connected plugin reachable. The refusal noun-list (`local_action_gate.py:299`) is left intact; we rely on the capability resolving the intent instead of widening the regex. A CI parity gate forbids a catalog plugin from existing without a paired skill.

**Tech Stack:** Python 3.11, Pydantic v2 (frozen models, `extra="forbid"`), pytest (`asyncio_mode=auto`), the existing `CapabilityRegistry` singleton, the `SkillRegistry` + `discover_skills` loader, and the `PluginToolRegistry` connect/disconnect lifecycle.

---

## Background — what the two deep-dives established (read before coding)

**The reachability chain (Agent 1, `routing-pathfinder`).** An utterance flows through `BrainManager.generate()` (`manager.py:2384`). The FIRST blocking gate is the local-action fast path (`manager.py:2469` → `local_action_gate.match_local_action`). It returns `UNSUPPORTED` (`local_action_gate.py:411-416`) when ALL hold: `has_action_intent` is true, `resolve_intent() is None`, not desktop-control, and `requires_external_integration()` is true. The force-spawn weiche `_is_generic_subagent_work` (`manager.py:1885`) also early-returns on `requires_external_integration` (`manager.py:1896`). **Lever:** registering a `Capability` whose verbs+objects match the utterance makes `resolve_intent()` return non-None, which silences BOTH paths at once. Proven precedent: the contacts capabilities (`capabilities_seed.py:367-415`).

**Plugin inventory (Agent 1).** 13 catalog plugins. Only `gmail` (native tool, noun in refusal regex) and `telegram` (noun in refusal regex AND structurally has no tool — channel adapter only) are refused by the gate today. `vercel` is broken differently (`rest_wrapper` transport unsupported by `plugin_mcp.py:72` → zero tools). The other 10 (`github`, `stripe`, `supabase`, `notion`, `slack`, `linear`, `cloudflare`, `discord`, `asana`, `google_drive`) only work *by accident*: their domain noun simply isn't in `_EXTERNAL_INTEGRATION_NOUN_RE` (`local_action_gate.py:299`), and MCP adapters give them a weak auto-capability (`adapter.py:91-100`). They are not deliberately secured.

**The MCP auto-capability (this session).** Every `MCPToolAdapter` registers `Capability(id=f"mcp.{name}", source="mcp", verbs=_verbs_from_description(desc), objects=_objects_from_tool_name(name), ...)` (`adapter.py:91-100`). Verbs/objects are *heuristic guesses*. A paired skill's explicit `intent_verbs`/`intent_objects` replace the guess with curated vocabulary.

**Skill execution contract (Agent 2, `skill-pairing-architect`).** In production the `SkillRunner` is built WITHOUT a `tool_registry` (D9 recursion guard) — so `TOOL:` lines in a skill body silently no-op (`runner.py` resolves them to None). **Therefore a plugin-paired skill must NOT contain executing `TOOL:` lines.** Its body is GUIDANCE PROSE returned to the brain via Path B (`render_available_skills_section` at `manager.py:1168-1179` injects name+description; `run-skill` returns the rendered body, and the brain then issues the plugin tool-call itself). This path works today with no wiring change.

**The hard-negative trap (Agent 1, Risk 1).** "implementier eine Email-Validation" must STAY generic Jarvis-Agent work, never become a Gmail call. `\bemail\b` matches "Email-Validation", so `intent_objects` must use inbox-specific nouns (`postfach`, `inbox`, `gmail`) and MUST NOT contain bare `email`/`e-mail`. `intent_verbs` must exclude coding verbs (`implementier`/`baue`/`schreib`). Guarded by `tests/integration/test_capability_coupling_e2e.py` (hard-negatives at ~46-87). The four non-Gmail hard-negatives (Termin/WhatsApp/Pizza/X-posting) have no backing tool and MUST remain `UNSUPPORTED`.

---

## File Structure

**Create:**
- `jarvis/skills/plugin_coupling.py` — the deterministic generator: `capability_from_skill(skill) -> Capability | None` and `register_paired_capabilities(registry, skill_registry) -> int`.
- `tests/unit/skills/test_plugin_coupling.py` — unit tests for the generator + hard-negative guards.
- `tests/integration/test_plugin_reachability_e2e.py` — end-to-end: a paired Gmail skill makes "lies meine letzte Mail" / "schick eine Mail an X" resolve instead of UNSUPPORTED.
- `tests/unit/marketplace/test_plugin_skill_parity.py` — the CI anti-drift gate: every catalog plugin has a paired skill.
- `jarvis/skills/builtin/plugin-gmail/SKILL.md` ... one paired skill per plugin (Phase 1, authored by subagents).

**Modify:**
- `jarvis/skills/schema.py:76` — add `plugin_id`, `intent_verbs`, `intent_objects` fields to `SkillFrontmatter`.
- `jarvis/marketplace/plugin_registry.py:95-129` — register the paired capability on `_connect_plugin`, deregister on `_disconnect_plugin`.
- `jarvis/core/capabilities.py:150` — add `deregister(cap_id)` (needed for disconnect).
- `jarvis/brain/factory.py` (boot seeding site) — call `register_paired_capabilities` after `seed_registry`.
- `tests/integration/test_capability_coupling_e2e.py` — migrate the Gmail hard-negative to a hard-positive; keep the other four negative.

---

## Phase 0 — Reachability scaffold (the rails). Build this FIRST, before any subagent runs.

### Task 1: Extend `SkillFrontmatter` with pairing + intent fields

**Files:**
- Modify: `jarvis/skills/schema.py:76-99`
- Test: `tests/unit/skills/test_schema_pairing_fields.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/skills/test_schema_pairing_fields.py
from jarvis.skills.schema import SkillFrontmatter


def test_pairing_fields_default_empty():
    fm = SkillFrontmatter(name="x")
    assert fm.plugin_id is None
    assert fm.intent_verbs == []
    assert fm.intent_objects == []


def test_pairing_fields_roundtrip():
    fm = SkillFrontmatter(
        name="plugin-gmail",
        plugin_id="gmail",
        intent_verbs=["lies", "schick", "antworte"],
        intent_objects=["postfach", "inbox", "gmail"],
    )
    assert fm.plugin_id == "gmail"
    assert "postfach" in fm.intent_objects
    # extra="forbid" must still reject typos
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        SkillFrontmatter(name="x", pluginid="typo")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3.11 -m pytest tests/unit/skills/test_schema_pairing_fields.py -v`
Expected: FAIL — `SkillFrontmatter` has no field `plugin_id` (ValidationError on the roundtrip test because `extra="forbid"`).

- [ ] **Step 3: Add the fields**

In `jarvis/skills/schema.py`, inside `class SkillFrontmatter` (after the `state` field at line 99), add:

```python
    # Plugin↔Skill pairing (2026-06-07). When set, this skill is the canonical
    # source for a marketplace plugin's intent vocabulary; the deterministic
    # generator in jarvis/skills/plugin_coupling.py turns intent_verbs +
    # intent_objects into a CapabilityRegistry entry so the connected plugin is
    # reachable (resolve_intent != None silences the UNSUPPORTED refusal AND the
    # force-spawn weiche). plugin_id=None marks a standalone "skill without
    # plugin" that still carries an intent capability.
    # HARD CONSTRAINT (test_capability_coupling_e2e): intent_objects must use
    # inbox/domain-SPECIFIC nouns (postfach/inbox) and MUST NOT contain bare
    # "email"/"e-mail" (it would match "Email-Validation" coding tasks);
    # intent_verbs must exclude coding verbs (implementier/baue/schreib-code).
    plugin_id: str | None = None
    intent_verbs: list[str] = Field(default_factory=list)
    intent_objects: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3.11 -m pytest tests/unit/skills/test_schema_pairing_fields.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add jarvis/skills/schema.py tests/unit/skills/test_schema_pairing_fields.py
git commit -m "feat(skills): add plugin_id + intent_verbs/objects to SkillFrontmatter"
```

---

### Task 2: Add `deregister` to the CapabilityRegistry

**Files:**
- Modify: `jarvis/core/capabilities.py:150-154`
- Test: `tests/unit/core/test_capabilities_deregister.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/core/test_capabilities_deregister.py
from jarvis.core.capabilities import Capability, CapabilityRegistry


def _cap(cid: str) -> Capability:
    return Capability(
        id=cid, source="mcp", verbs=("lies",), objects=("postfach",),
        description="x", risk_tier="ask", requires_evidence=True,
    )


def test_deregister_removes_capability():
    reg = CapabilityRegistry()
    reg.register(_cap("plugin.gmail"))
    assert reg.resolve_intent("lies mein postfach") is not None
    reg.deregister("plugin.gmail")
    assert reg.resolve_intent("lies mein postfach") is None


def test_deregister_unknown_is_noop():
    reg = CapabilityRegistry()
    reg.deregister("does.not.exist")  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3.11 -m pytest tests/unit/core/test_capabilities_deregister.py -v`
Expected: FAIL — `CapabilityRegistry` has no attribute `deregister`.

- [ ] **Step 3: Add the method**

In `jarvis/core/capabilities.py`, after `register` (line 154), add:

```python
    def deregister(self, cap_id: str) -> None:
        """Remove a capability by id. Unknown id is a silent no-op.

        Needed for plugin-disconnect: a paired plugin capability must be
        withdrawn when the user disconnects the plugin, so resolve_intent
        stops resolving (and the honest refusal / force-spawn returns)."""
        with self._lock:
            self._caps.pop(cap_id, None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3.11 -m pytest tests/unit/core/test_capabilities_deregister.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add jarvis/core/capabilities.py tests/unit/core/test_capabilities_deregister.py
git commit -m "feat(capabilities): add deregister() for plugin-disconnect teardown"
```

---

### Task 3: The deterministic generator — skill → capability

**Files:**
- Create: `jarvis/skills/plugin_coupling.py`
- Test: `tests/unit/skills/test_plugin_coupling.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/skills/test_plugin_coupling.py
from pathlib import Path

from jarvis.skills.schema import Skill, SkillFrontmatter, SkillLifecycleState
from jarvis.skills.plugin_coupling import capability_from_skill, PAIRED_CAP_PREFIX


def _skill(**fm) -> Skill:
    front = SkillFrontmatter(**fm)
    return Skill(
        path=Path("x/SKILL.md"), frontmatter=front, body="guidance",
        state=SkillLifecycleState.ACTIVE,
    )


def test_capability_from_paired_skill():
    sk = _skill(
        name="plugin-gmail", plugin_id="gmail",
        description="Read and send mail from the connected Gmail inbox.",
        intent_verbs=["lies", "schick", "antworte"],
        intent_objects=["postfach", "inbox", "gmail"],
        risk_policy={"default_tier": "ask"},
    )
    cap = capability_from_skill(sk)
    assert cap is not None
    assert cap.id == f"{PAIRED_CAP_PREFIX}gmail"
    assert cap.source == "skill"
    assert "lies" in cap.verbs and "postfach" in cap.objects
    assert cap.risk_tier == "ask"


def test_no_capability_without_intent_vocab():
    # A paired skill with no verbs+objects cannot resolve anything → no cap.
    sk = _skill(name="plugin-empty", plugin_id="empty")
    assert capability_from_skill(sk) is None


def test_standalone_skill_with_intent_gets_capability():
    # "skill without plugin": plugin_id None but intent vocab present.
    sk = _skill(
        name="morning-routine", description="Run the morning routine.",
        intent_verbs=["starte"], intent_objects=["morgenroutine", "routine"],
    )
    cap = capability_from_skill(sk)
    assert cap is not None
    assert cap.id == f"{PAIRED_CAP_PREFIX}morning-routine"


def test_draft_skill_yields_no_capability():
    sk = _skill(name="plugin-gmail", plugin_id="gmail",
                intent_verbs=["lies"], intent_objects=["postfach"])
    object.__setattr__(sk, "state", SkillLifecycleState.DRAFT)
    assert capability_from_skill(sk) is None


def test_gmail_objects_do_not_match_email_validation():
    """HARD NEGATIVE: a curated Gmail skill must NOT resolve a coding task."""
    from jarvis.core.capabilities import CapabilityRegistry
    sk = _skill(
        name="plugin-gmail", plugin_id="gmail",
        description="Gmail inbox.",
        intent_verbs=["lies", "schick", "antworte", "zeig"],
        intent_objects=["postfach", "inbox", "gmail", "mails", "nachrichten"],
        risk_policy={"default_tier": "ask"},
    )
    reg = CapabilityRegistry()
    reg.register(capability_from_skill(sk))
    # coding task that merely NAMES email → must NOT resolve to gmail
    assert reg.resolve_intent("implementier eine Email-Validation") is None
    # real inbox request → must resolve
    assert reg.resolve_intent("lies meine letzte Mail aus dem Postfach") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3.11 -m pytest tests/unit/skills/test_plugin_coupling.py -v`
Expected: FAIL — module `jarvis.skills.plugin_coupling` does not exist.

- [ ] **Step 3: Write the generator**

```python
# jarvis/skills/plugin_coupling.py
"""Deterministic generator: a paired SKILL.md → a CapabilityRegistry entry.

The SKILL.md is the single source of truth for a plugin's (or standalone
skill's) intent vocabulary. This module turns its `intent_verbs` +
`intent_objects` frontmatter into a `Capability`, so `resolve_intent()` matches
the user's utterance and the connected plugin becomes reachable — which
silences BOTH the UNSUPPORTED refusal (local_action_gate.py:411) AND the
force-spawn weiche (manager.py:1896) in one move.

No LLM, no IO — pure transform (safe to call on the registry at boot and on the
plugin-connect/disconnect lifecycle). The intent vocabulary itself is authored
by a subagent in the SKILL.md; the wiring here is mechanical.
"""
from __future__ import annotations

import logging

from jarvis.core.capabilities import Capability, CapabilityRegistry
from jarvis.skills.schema import Skill, SkillLifecycleState

log = logging.getLogger(__name__)

#: All paired capabilities share this id prefix so register_paired_capabilities
#: can withdraw the whole set on reload without touching seeded/MCP caps.
PAIRED_CAP_PREFIX = "skill.paired."

# Only ACTIVE / VALIDATED skills contribute a capability (DRAFT/DISABLED must
# not silently grant reachability — mirrors SkillRegistry.list_active).
_LIVE_STATES = (SkillLifecycleState.ACTIVE, SkillLifecycleState.VALIDATED)


def capability_from_skill(skill: Skill) -> Capability | None:
    """Return a Capability for a paired/intent-carrying skill, or None.

    None when: no frontmatter, not a live state, or no intent vocabulary
    (a skill with neither verbs nor objects cannot resolve anything).
    """
    fm = skill.frontmatter
    if fm is None or skill.state not in _LIVE_STATES:
        return None
    verbs = tuple(v.strip() for v in fm.intent_verbs if v and v.strip())
    objects = tuple(o.strip() for o in fm.intent_objects if o and o.strip())
    if not verbs or not objects:
        # resolve_intent needs at least one verb; an object-less cap can never
        # win specificity. Require both so the capability is meaningful.
        return None
    # Identity: prefer the plugin_id (one cap per plugin); fall back to the
    # skill name for standalone "skill without plugin".
    ident = (fm.plugin_id or fm.name).strip()
    return Capability(
        id=f"{PAIRED_CAP_PREFIX}{ident}",
        source="skill",
        verbs=verbs,
        objects=objects,
        description=fm.description or f"Paired skill capability for {ident}.",
        risk_tier=fm.risk_policy.default_tier,
        requires_evidence=True,
    )


def register_paired_capabilities(
    registry: CapabilityRegistry, skills: list[Skill]
) -> int:
    """Register a capability for every live paired/intent skill. Returns count.

    Idempotent: re-registration replaces the previous entry (capabilities.py).
    Caller is responsible for ordering (call after seed_registry so an explicit
    paired cap can override a weak MCP auto-cap for the same domain)."""
    n = 0
    for skill in skills:
        cap = capability_from_skill(skill)
        if cap is not None:
            registry.register(cap)
            n += 1
    log.info("plugin_coupling: registered %d paired capabilities", n)
    return n
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3.11 -m pytest tests/unit/skills/test_plugin_coupling.py -v`
Expected: PASS (5 tests). The hard-negative test proves "implementier eine Email-Validation" stays unresolved.

- [ ] **Step 5: Commit**

```bash
git add jarvis/skills/plugin_coupling.py tests/unit/skills/test_plugin_coupling.py
git commit -m "feat(skills): deterministic paired-skill → capability generator"
```

---

### Task 4: Seed paired capabilities at boot

**Files:**
- Modify: `jarvis/brain/factory.py` (the boot site that calls `seed_registry`)
- Test: `tests/unit/brain/test_paired_capability_boot.py`

- [ ] **Step 1: Locate the seed site**

Run: `py -3.11 -c "import jarvis.brain.factory"` then `grep -n "seed_registry" jarvis/brain/factory.py` (use the Grep tool). Confirm where `seed_registry(get_registry())` is called at brain build.

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/brain/test_paired_capability_boot.py
from pathlib import Path

from jarvis.core.capabilities import CapabilityRegistry
from jarvis.skills.schema import Skill, SkillFrontmatter, SkillLifecycleState
from jarvis.skills.plugin_coupling import register_paired_capabilities


def test_boot_registers_paired_gmail_capability():
    reg = CapabilityRegistry()
    gmail = Skill(
        path=Path("plugin-gmail/SKILL.md"),
        frontmatter=SkillFrontmatter(
            name="plugin-gmail", plugin_id="gmail",
            description="Gmail inbox.",
            intent_verbs=["lies", "schick", "antworte", "zeig"],
            intent_objects=["postfach", "inbox", "gmail", "mails"],
            risk_policy={"default_tier": "ask"},
        ),
        body="g", state=SkillLifecycleState.ACTIVE,
    )
    n = register_paired_capabilities(reg, [gmail])
    assert n == 1
    assert reg.resolve_intent("schick eine Mail an Sam aus meinem Postfach") is not None
```

- [ ] **Step 3: Run test to verify it fails / passes**

Run: `py -3.11 -m pytest tests/unit/brain/test_paired_capability_boot.py -v`
Expected: PASS already (it tests `register_paired_capabilities` directly). This test is the contract; Step 4 wires it into boot.

- [ ] **Step 4: Wire into boot**

At the `seed_registry(get_registry())` call site in `jarvis/brain/factory.py`, immediately AFTER it add:

```python
    # Plugin↔Skill pairing (2026-06-07): after the static seed, register a
    # capability for every live paired skill so connected plugins resolve.
    # Placed after seed_registry so an explicit paired cap overrides the weak
    # MCP auto-cap for the same domain. Defensive: a missing skill registry
    # must not block boot (cloud-first graceful degradation).
    try:
        from jarvis.skills.plugin_coupling import register_paired_capabilities
        from jarvis.skills.skill_context import try_get_skill_context

        _ctx = try_get_skill_context()
        if _ctx is not None:
            register_paired_capabilities(get_registry(), _ctx.registry.list())
    except Exception as exc:  # noqa: BLE001
        log.debug("paired-capability seed skipped: %s", exc)
```

> VERIFIED API (2026-06-07, lead): the process-wide accessor is
> `jarvis.skills.skill_context.try_get_skill_context()` → returns a
> `SkillContext | None` with a `.registry` attribute (a `SkillRegistry`).
> `SkillRegistry.list()` (`registry.py:104`) returns ALL skills;
> `.list_active()` (`registry.py:107`) returns ACTIVE/VALIDATED only. Use
> `.list()` — `capability_from_skill` already filters DRAFT/DISABLED. Do NOT use
> `get_skill_registry`/`all_skills` (they do not exist). `registry.py` is
> currently dirty in a parallel session, so re-confirm these names still hold at
> implement time, but do not edit `registry.py`.

- [ ] **Step 5: Run the brain test suite + commit**

Run: `py -3.11 -m pytest tests/unit/brain/test_paired_capability_boot.py tests/unit/brain/test_routing.py -v`
Expected: PASS.

```bash
git add jarvis/brain/factory.py tests/unit/brain/test_paired_capability_boot.py
git commit -m "feat(brain): register paired-skill capabilities at boot"
```

---

### Task 5: Register/deregister paired capability on plugin connect/disconnect

**Files:**
- Modify: `jarvis/marketplace/plugin_registry.py:95-139`
- Test: `tests/unit/marketplace/test_plugin_capability_lifecycle.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/marketplace/test_plugin_capability_lifecycle.py
from jarvis.core.capabilities import CapabilityRegistry
from jarvis.marketplace.plugin_registry import _register_plugin_capability, _deregister_plugin_capability
from jarvis.skills.plugin_coupling import PAIRED_CAP_PREFIX


def test_connect_registers_and_disconnect_removes(monkeypatch, tmp_path):
    reg = CapabilityRegistry()
    # Fake a paired skill discoverable by plugin_id
    from jarvis.skills.schema import Skill, SkillFrontmatter, SkillLifecycleState
    from pathlib import Path
    gmail = Skill(
        path=Path("plugin-gmail/SKILL.md"),
        frontmatter=SkillFrontmatter(
            name="plugin-gmail", plugin_id="gmail", description="Gmail.",
            intent_verbs=["lies", "schick"], intent_objects=["postfach", "inbox"],
            risk_policy={"default_tier": "ask"},
        ),
        body="g", state=SkillLifecycleState.ACTIVE,
    )
    _register_plugin_capability(reg, "gmail", [gmail])
    assert reg.resolve_intent("lies mein postfach") is not None
    _deregister_plugin_capability(reg, "gmail")
    assert reg.resolve_intent("lies mein postfach") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3.11 -m pytest tests/unit/marketplace/test_plugin_capability_lifecycle.py -v`
Expected: FAIL — helpers `_register_plugin_capability` / `_deregister_plugin_capability` do not exist.

- [ ] **Step 3: Add the helpers + wire them into the lifecycle**

In `jarvis/marketplace/plugin_registry.py`, add module-level helpers (after the imports):

```python
def _register_plugin_capability(cap_registry, plugin_id, skills) -> None:
    """Register the paired-skill capability for a freshly connected plugin."""
    from jarvis.skills.plugin_coupling import capability_from_skill

    for sk in skills:
        fm = getattr(sk, "frontmatter", None)
        if fm is not None and getattr(fm, "plugin_id", None) == plugin_id:
            cap = capability_from_skill(sk)
            if cap is not None:
                cap_registry.register(cap)
            return


def _deregister_plugin_capability(cap_registry, plugin_id) -> None:
    """Withdraw the paired capability when a plugin disconnects."""
    from jarvis.skills.plugin_coupling import PAIRED_CAP_PREFIX

    cap_registry.deregister(f"{PAIRED_CAP_PREFIX}{plugin_id}")
```

Then in `_connect_plugin` (after the tools are registered, ~`plugin_registry.py:129`):

```python
        try:
            from jarvis.core.capabilities import get_registry as _get_cap_registry
            from jarvis.skills.skill_context import try_get_skill_context

            _ctx = try_get_skill_context()
            if _ctx is not None:
                _register_plugin_capability(
                    _get_cap_registry(), plugin.id, _ctx.registry.list()
                )
        except Exception as exc:  # noqa: BLE001 — capability is best-effort
            log.debug("paired cap register failed for %s: %s", plugin.id, exc)
```

And in `_disconnect_plugin` (after the client stop, ~`plugin_registry.py:139`):

```python
        try:
            from jarvis.core.capabilities import get_registry as _get_cap_registry

            _deregister_plugin_capability(_get_cap_registry(), plugin_id)
        except Exception as exc:  # noqa: BLE001
            log.debug("paired cap deregister failed for %s: %s", plugin_id, exc)
```

> VERIFIED API (2026-06-07, lead): same as Task 4 — `try_get_skill_context()` →
> `.registry.list()`. The connect/disconnect hooks are best-effort: at the very
> first boot bootstrap the skill context may not be set yet (None) — that is
> fine, the Task-4 boot seed covers startup; the live connect path (refresh_plugin)
> runs after the context is set.

- [ ] **Step 4: Run test + the plugin registry suite to verify**

Run: `py -3.11 -m pytest tests/unit/marketplace/test_plugin_capability_lifecycle.py tests/unit/marketplace/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/marketplace/plugin_registry.py tests/unit/marketplace/test_plugin_capability_lifecycle.py
git commit -m "feat(marketplace): (de)register paired capability on plugin connect/disconnect"
```

---

### Task 5.5: `resolve_intent` precision for paired-skill capabilities (DISCOVERED during e2e prototyping)

**Why:** Empirical test (lead, 2026-06-07) showed the original plan's assumption was too optimistic. A paired Gmail cap with generic dispatch verbs (`sende`/`schick`) hijacked a DIFFERENT domain's hard-negative ("Sende eine WhatsApp" wrongly resolved to gmail via verb-only match), and lost ties to earlier-registered seed caps ("lies meine Mail" → contact-lookup, "check mein Postfach" → dispatch-with-review). Two narrow rules in `resolve_intent`, applied ONLY to `source="skill"` caps (seed tool/harness/local caps unchanged), fix all cases. Prototype validated all 9 utterances (5 hard-negatives stay None, gmail requests resolve to gmail, "Lies die Datei" stays harness).

**Files:**
- Modify: `jarvis/core/capabilities.py` (`resolve_intent`, ~line 188-206)
- Test: `tests/unit/core/test_resolve_intent_skill_precision.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/core/test_resolve_intent_skill_precision.py
from pathlib import Path

from jarvis.core.capabilities import CapabilityRegistry
from jarvis.core.capabilities_seed import seed_registry
from jarvis.skills.schema import Skill, SkillFrontmatter, SkillLifecycleState
from jarvis.skills.plugin_coupling import register_paired_capabilities


def _reg_with_gmail() -> CapabilityRegistry:
    reg = CapabilityRegistry()
    seed_registry(reg)
    gmail = Skill(
        path=Path("plugin-gmail/SKILL.md"),
        frontmatter=SkillFrontmatter(
            name="plugin-gmail", plugin_id="gmail", description="Gmail.",
            intent_verbs=["lies", "lese", "schick", "sende", "antworte", "zeig", "check"],  # i18n-allow
            intent_objects=["postfach", "inbox", "gmail", "mail", "email", "mails", "nachrichten", "posteingang"],  # i18n-allow
            risk_policy={"default_tier": "ask"},
        ),
        body="g", state=SkillLifecycleState.ACTIVE,
    )
    register_paired_capabilities(reg, [gmail])
    return reg


def test_gmail_request_with_domain_noun_resolves_to_gmail():
    reg = _reg_with_gmail()
    for utt in [
        "Schick eine Email an sam@example.com mit dem Betreff Hallo",  # i18n-allow
        "lies meine letzte Mail aus dem Postfach",  # i18n-allow
        "check mein Postfach",  # i18n-allow
    ]:
        cap = reg.resolve_intent(utt)
        assert cap is not None and cap.id == "skill.paired.gmail", f"{utt!r} -> {cap}"


def test_generic_verb_without_gmail_noun_does_not_hijack():
    """A skill cap must NOT win on a generic verb alone — other domains' actions
    (WhatsApp/Pizza) and coding tasks must not resolve to gmail."""
    reg = _reg_with_gmail()
    for utt in [
        "Sende eine WhatsApp an Mama",  # i18n-allow
        "Bestelle eine Pizza",  # i18n-allow
        "Poste auf X dass ich heute frei habe",  # i18n-allow
        "Trag einen Termin morgen 10 Uhr ein",  # i18n-allow
    ]:
        cap = reg.resolve_intent(utt)
        assert cap is None or cap.id != "skill.paired.gmail", f"{utt!r} -> {cap}"


def test_seed_harness_caps_unchanged_by_skill_rules():
    """The skill-only rules must not regress generic seed-cap matching."""
    reg = _reg_with_gmail()
    cap = reg.resolve_intent("Lies die Datei foo.txt")
    assert cap is not None and cap.source == "harness"
```

- [ ] **Step 2: Run it, verify it FAILS** (the WhatsApp hijack + tie-loss assertions fail under current logic):
`py -3.11 -m pytest tests/unit/core/test_resolve_intent_skill_precision.py -v`

- [ ] **Step 3: Modify `resolve_intent`.** In `jarvis/core/capabilities.py`, in the per-cap loop (after `obj_hit` is computed, before `score = 2 if obj_hit else 1`), insert the skill-only rules:

```python
            # Plugin/paired-skill capabilities are DOMAIN-SPECIFIC: they must
            # match a domain object (noun), not just a generic dispatch verb.
            # Without this, gmail's generic "sende"/"schick" would hijack a
            # different domain's request ("Sende eine WhatsApp"). Seed
            # tool/harness/local caps keep their verb-only match (unchanged).
            if cap.source == "skill" and not obj_hit:
                continue
            score = 2 if obj_hit else 1
            # A domain-specific paired-skill match (verb + its own domain noun)
            # is the most specific signal — it beats a generic seed cap that
            # merely shares the verb/object on a tie (e.g. "check mein Postfach"
            # must reach gmail, not dispatch-with-review).
            if cap.source == "skill" and obj_hit:
                score = 3
```

(Leave the existing `if score > best_score:` block as-is below this.)

- [ ] **Step 4: Run it, verify it PASSES + no regression on the existing coupling suite:**
`py -3.11 -m pytest tests/unit/core/test_resolve_intent_skill_precision.py tests/integration/test_capability_coupling_e2e.py tests/unit/core/ -k "capab or resolve or coupling" -v`
Expected: PASS, including all pre-existing hard-negative/hard-positive cases (skill rules don't touch seed caps).

- [ ] **Step 5: ruff + language gate, then FRESH commit:**
`py -3.11 -m ruff check jarvis/core/capabilities.py tests/unit/core/test_resolve_intent_skill_precision.py`
```
git add jarvis/core/capabilities.py tests/unit/core/test_resolve_intent_skill_precision.py
git commit -m "feat(capabilities): paired-skill caps require a domain object + win ties vs generic caps"
```
`py -3.11 scripts/ci/check_no_new_german.py HEAD~1` → expect "gate OK".

---

### Task 6: End-to-end reachability + migrate the Gmail hard-negative

**Files:**
- Create: `tests/integration/test_plugin_reachability_e2e.py`
- Modify: `tests/integration/test_capability_coupling_e2e.py` (Gmail negative → positive)

- [ ] **Step 1: Write the e2e reachability test**

```python
# tests/integration/test_plugin_reachability_e2e.py
"""A paired Gmail capability makes inbox requests resolve, not refuse."""
from jarvis.core.capabilities import CapabilityRegistry
from jarvis.brain.local_action_gate import match_local_action, LocalActionMode
from jarvis.skills.schema import Skill, SkillFrontmatter, SkillLifecycleState
from jarvis.skills.plugin_coupling import register_paired_capabilities
from pathlib import Path


def _gmail_skill() -> Skill:
    return Skill(
        path=Path("plugin-gmail/SKILL.md"),
        frontmatter=SkillFrontmatter(
            name="plugin-gmail", plugin_id="gmail",
            description="Read and send mail from the connected Gmail inbox.",
            intent_verbs=["lies", "lese", "schick", "sende", "antworte", "zeig", "check"],  # i18n-allow
            intent_objects=["postfach", "inbox", "gmail", "mails", "nachrichten", "posteingang"],  # i18n-allow
            risk_policy={"default_tier": "ask"},
        ),
        body="Use gmail/* tools to read and send mail.",
        state=SkillLifecycleState.ACTIVE,
    )


def test_gmail_request_resolves_when_paired():
    reg = CapabilityRegistry()
    register_paired_capabilities(reg, [_gmail_skill()])
    # The gate must NOT return UNSUPPORTED — resolve_intent is non-None now.
    plan = match_local_action("schick eine Mail an Sam aus meinem Postfach", _registry=reg)  # i18n-allow
    assert plan is None or plan.mode != LocalActionMode.UNSUPPORTED


def test_email_validation_still_refused_or_passed_through():
    """Coding task that merely names email stays non-Gmail."""
    reg = CapabilityRegistry()
    register_paired_capabilities(reg, [_gmail_skill()])
    assert reg.resolve_intent("implementier eine Email-Validation") is None  # i18n-allow
```

> CI LANGUAGE GATE: the German voice-utterance string literals and German intent-vocab lists above are intentional test data (they prove Jarvis's German speech path resolves). Each such line carries `# i18n-allow`. After writing, verify with `py -3.11 scripts/ci/check_no_new_german.py HEAD~1` → must print "gate OK".

- [ ] **Step 2: Run it to verify it passes** (the scaffold from Tasks 1-5 makes it green)

Run: `py -3.11 -m pytest tests/integration/test_plugin_reachability_e2e.py -v`
Expected: PASS. If `test_gmail_request_resolves_when_paired` fails, the gate's noun-list still wins over an empty registry — re-read `match_local_action` `_registry` handling (`local_action_gate.py:389-397`) and pass the seeded registry explicitly (the test already does).

- [ ] **Step 3: Migrate the existing Gmail hard-negative**

In `tests/integration/test_capability_coupling_e2e.py`, find the hard-negative entry for "Schick eine Email an …" (~line 47). With a paired Gmail capability registered, that utterance is now CORRECTLY reachable, so it must move from the "must be UNSUPPORTED" list to a "must resolve" assertion. Read the test, then: (a) remove the Gmail line from the hard-negative list, (b) add a positive assertion that with the Gmail paired skill seeded, the same utterance resolves. Leave the other four hard-negatives (Termin/WhatsApp/Pizza/X-posting) untouched — they have no backing tool and MUST stay UNSUPPORTED.

- [ ] **Step 4: Run the full capability + gate suite**

Run: `py -3.11 -m pytest tests/integration/test_capability_coupling_e2e.py tests/integration/test_plugin_reachability_e2e.py tests/unit/brain/test_routing.py -v`
Expected: PASS, four non-Gmail hard-negatives still UNSUPPORTED.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_plugin_reachability_e2e.py tests/integration/test_capability_coupling_e2e.py
git commit -m "test(reachability): paired Gmail resolves; 4 non-tool hard-negatives stay refused"
```

---

## Phase 1 — Author 13 paired skills (parallel subagents)

**This phase runs ONLY after Phase 0 is green.** Each subagent authors ONE `jarvis/skills/builtin/plugin-<id>/SKILL.md` against the schema from Task 1. Dispatch all 13 in parallel (Sonnet model). Each subagent is read-mostly + writes exactly one SKILL.md.

**Plugin list (13):** `github`, `vercel`, `supabase`, `notion`, `slack`, `linear`, `stripe`, `cloudflare`, `discord`, `telegram`, `asana`, `google_drive`, `gmail`.

**Per-subagent brief (template — fill `<id>` + the plugin's catalog entry):**

> Author `jarvis/skills/builtin/plugin-<id>/SKILL.md` — the canonical paired skill for the `<id>` marketplace plugin. READ FIRST: `jarvis/marketplace/seed_catalog.json` (the `<id>` entry), `jarvis/marketplace/usage_cards/<id>.md` if present, `jarvis/skills/schema.py` (frontmatter contract), and `jarvis/skills/builtin/<any existing>/SKILL.md` for format. Then write the SKILL.md with this frontmatter:
> - `name: plugin-<id>`, `plugin_id: <id>`, `description:` ONE English sentence the brain sees in AVAILABLE SKILLS.
> - `intent_verbs:` curated DE+EN action verbs for THIS plugin's real use (e.g. for stripe: `zeig, lies, erstatt, refund, charge`). **THIS IS THE PRIMARY HARD-NEGATIVE GUARD: EXCLUDE coding verbs** (`implementier`, `baue`, `schreib`, `entwickel`, `refactor`, `debug`) — they belong to generic Jarvis-Agent work. (Per Task 5.5, a paired-skill cap only matches when BOTH a verb AND a domain object hit; so excluding coding verbs is what keeps "implementier eine Email-Validation" off the gmail cap, even though `email` IS an allowed object — see next line.)
> - `intent_objects:` domain-SPECIFIC nouns — the words a user says when they mean THIS product's data. The plugin cap requires an object hit (Task 5.5), so these must be rich enough to cover real phrasings: for gmail use `postfach, inbox, gmail, posteingang, mail, email, mails, nachrichten`. Bare `email`/`mail` IS allowed and needed (so "Schick eine Email" matches) — the coding-task guard lives in the verb list, NOT here. AVOID nouns that belong to a DIFFERENT domain (don't put `nachricht` alone on gmail if it would steal WhatsApp/SMS requests — prefer `mail`/`postfach`).
> - `triggers:` ONE `voice` trigger with a permissive `pattern` regex matching natural phrasing.
> - `requires_tools:` the plugin's namespaced tools (e.g. `stripe/list_customers`) for documentation — these are advisory.
> - `risk_policy.default_tier:` match the tool's tier (gmail=ask, read-only lookups=safe).
> - Body: GUIDANCE PROSE only. **Do NOT write any `TOOL: ...` execution lines** — the production SkillRunner has no tool_registry and they silently no-op. The body tells the brain HOW to use the plugin's tools; the brain issues the calls (Path B). Read-first, summarize results, confirm before destructive actions.
> - **CI LANGUAGE GATE (binding):** `scripts/ci/check_no_new_german.py` scans `.md` files and flags newly-added German lines. The `description:` and the entire Markdown body MUST be plain English (no marker — the gate correctly enforces this). The German intent vocabulary is the EXCEPTION: append `  # i18n-allow` (a YAML comment) to EVERY frontmatter line carrying German tokens — typically the `intent_verbs:`, `intent_objects:`, and any German `triggers[].pattern:` line. Keep each of those as a single-line inline list so one marker covers it, e.g. `intent_objects: [postfach, inbox, gmail, mails, nachrichten]  # i18n-allow`. After writing, VERIFY with `py -3.11 scripts/ci/check_no_new_german.py HEAD~1` → must print "gate OK"; if it lists any line, add `# i18n-allow` there (data line) or translate it (prose line).
> Return: the path written + the chosen intent_verbs/intent_objects, the gate result, and a one-line note confirming no bare-`email`-class hijack noun was used.

**After all 13 return:** review each SKILL.md (two-stage: a `code-reviewer` pass for the hard-negative constraint + a parity check that `plugin_id` matches a catalog id). Then run:

Run: `py -3.11 -m pytest tests/unit/skills/ tests/integration/test_plugin_reachability_e2e.py tests/integration/test_capability_coupling_e2e.py -v`

Commit each reviewed skill (or batch by reviewer approval).

---

## Phase 2 — Anti-drift CI gate (the convention, enforced)

### Task 7: Parity test — every catalog plugin has a paired skill

**Files:**
- Create: `tests/unit/marketplace/test_plugin_skill_parity.py`

- [ ] **Step 1: Write the parity test**

```python
# tests/unit/marketplace/test_plugin_skill_parity.py
"""BINDING CONVENTION: every marketplace plugin ships a paired skill.

This is the anti-drift gate. A new plugin added to the catalog WITHOUT a
plugin-<id>/SKILL.md (or with mismatched plugin_id) fails CI — which is exactly
what stops the Gmail-class regression from recurring.
"""
from pathlib import Path

from jarvis.marketplace.catalog_data import load_catalog
from jarvis.skills.loader import discover_skills

_BUILTIN = Path("jarvis/skills/builtin")

# Plugins exempt from the convention with a written reason (structural, not yet
# tool-backed). Keep this list SHORT and justified; removing an entry is the fix.
_EXEMPT = {
    "vercel": "rest_wrapper transport unsupported by plugin_mcp.py:72 — no tools yet",
    "telegram": "channel adapter only, no MCP/native tool to drive",
}


def test_every_plugin_has_paired_skill():
    catalog = load_catalog()
    skills = discover_skills(_BUILTIN)
    paired = {
        s.frontmatter.plugin_id
        for s in skills
        if s.frontmatter is not None and s.frontmatter.plugin_id
    }
    missing = [
        p.id for p in catalog.plugins
        if p.id not in paired and p.id not in _EXEMPT
    ]
    assert not missing, (
        f"plugins without a paired skill: {missing}. "
        f"Add jarvis/skills/builtin/plugin-<id>/SKILL.md with plugin_id=<id>, "
        f"or add a justified entry to _EXEMPT."
    )


_CODING_VERBS = frozenset({
    "implementier", "implementiere", "baue", "bau", "schreib", "schreibe",
    "entwickel", "entwickle", "refactor", "debug", "code", "programmier",
})


def test_paired_skills_exclude_coding_verbs():
    """The Gmail-class hijack guard (corrected, Task 5.5): a paired skill cap
    only matches when a verb AND a domain object both hit, so the hard-negative
    guard lives in the VERB list — no paired skill may carry a coding verb, or
    'implementier eine Email-Validation' (a coding task that names the domain)
    would resolve to the plugin instead of staying generic Jarvis-Agent work."""
    skills = discover_skills(_BUILTIN)
    offenders = []
    for s in skills:
        if s.frontmatter is None or not s.frontmatter.plugin_id:
            continue
        verbs = {v.lower().strip() for v in s.frontmatter.intent_verbs}
        bad = verbs & _CODING_VERBS
        if bad:
            offenders.append((s.frontmatter.plugin_id, sorted(bad)))
    assert not offenders, (
        f"paired skills carrying coding verbs (would hijack coding tasks that "
        f"merely name the domain): {offenders}. Remove coding verbs from "
        f"intent_verbs — they belong to generic Jarvis-Agent work."
    )
```

- [ ] **Step 2: Run it** (fails until all 13 skills from Phase 1 exist)

Run: `py -3.11 -m pytest tests/unit/marketplace/test_plugin_skill_parity.py -v`
Expected: after Phase 1 → PASS. Before Phase 1 → lists the missing plugins (this is the gate doing its job).

- [ ] **Step 3: Wire into CI** — add the file to the existing pytest job (it is collected by default under `tests/`). No workflow change needed unless a dedicated marker is wanted.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/marketplace/test_plugin_skill_parity.py
git commit -m "test(ci): anti-drift parity gate — every plugin ships a paired skill"
```

---

## Phase 3 — Structural plugin bugs (separate, do NOT block Phases 0-2)

These are surfaced by the deep-dive but are independent fixes. File them; fix after the reachability rails land.

- **`vercel` (`plugin_mcp.py:72`):** `transport=rest_wrapper` returns None → zero tools, no log warning. Either implement a rest_wrapper transport in `plugin_to_mcp_server_spec` or change the catalog entry to a supported transport. Until fixed, keep `vercel` in the parity `_EXEMPT` list.
- **`telegram`:** catalog entry has neither `mcp_server` nor `native_tool` (channel adapter only). Decide: add a native tool, or mark it non-actionable in the catalog. Until then keep it in `_EXEMPT`.

---

## Risks / Hard-Negatives (must not regress)

1. **"implementier eine Email-Validation" → must stay generic Jarvis-Agent work.** Guarded by `test_plugin_coupling.py::test_gmail_objects_do_not_match_email_validation`, `test_resolve_intent_skill_precision.py`, and `test_plugin_skill_parity.py::test_paired_skills_exclude_coding_verbs`. **Corrected mechanism (Task 5.5):** the guard is VERB-based — paired-skill `intent_verbs` must EXCLUDE coding verbs (`implementier`/`baue`/`schreib`/`entwickel`/`refactor`/`debug`). The object `email`/`mail` IS allowed (and required for "Schick eine Email" to match), because a paired-skill cap only matches when a verb AND an object both hit; with no coding verb in the list, "implementier eine Email-Validation" never gets a verb hit on the gmail cap.
2. **The four non-tool hard-negatives (Termin/WhatsApp/Pizza/X-posting) must stay UNSUPPORTED.** They have no backing plugin/tool, so no paired skill exists → no capability → `resolve_intent` stays None → refusal holds. Guarded by the untouched entries in `test_capability_coupling_e2e.py`.
3. **Skill bodies must not contain executing `TOOL:` lines.** Production SkillRunner has no tool_registry (D9 guard) → they no-op silently. Enforced by the Phase 1 reviewer brief; consider a follow-up lint in `test_plugin_skill_parity.py` scanning bodies for `^TOOL:`.
4. **Disconnect teardown.** A disconnected plugin must lose its capability (Task 5 `_deregister`), else a disconnected Gmail still resolves and the brain calls a tool that returns "not connected". Guarded by `test_plugin_capability_lifecycle.py`.
5. **Boot-order.** `register_paired_capabilities` runs AFTER `seed_registry` so an explicit paired cap overrides the weak MCP auto-cap. Wrong order = the heuristic guess wins.
6. **Windows test runner.** Use `py -3.11` (the Jarvis interpreter), not the pytest venv — per project memory, the venv Python ≠ Jarvis Python.
7. **CI language-policy gate (`scripts/ci/check_no_new_german.py`).** Scans newly-added lines in `.py`/`.md`/`.json`/`.yaml`/etc. German intent vocabulary (verbs/objects/voice-trigger patterns) and German test utterances are INTENTIONAL — Jarvis is German-primary — but the gate cannot tell. Mark each such DATA line with an inline `  # i18n-allow`; keep `description`/body/prose ENGLISH (no marker). Verify any commit with `py -3.11 scripts/ci/check_no_new_german.py HEAD~1` before considering a task done. This applies to Task 6's German test utterances and EVERY Phase-1 SKILL.md.

---

## Self-Review checklist (run before execution)

- **Spec coverage:** reachability fix (Tasks 3-6 ✓), "every new plugin pairs a skill" (Task 1 schema ✓ + Phase 1 authoring ✓ + Task 7 enforcement ✓), "skill without plugin" (Task 3 `test_standalone_skill_with_intent_gets_capability` ✓), "AI-authored skills" (Phase 1 subagents ✓), "all 13 plugins" (Phase 1 ✓).
- **No placeholders:** every code step has real code; the two NOTE-TO-IMPLEMENTER items are explicit "read the file, confirm the accessor name" — not invented APIs.
- **Type consistency:** `PAIRED_CAP_PREFIX`, `capability_from_skill`, `register_paired_capabilities`, `_register_plugin_capability`, `_deregister_plugin_capability` used identically across Tasks 3/4/5/6/7. `Capability(...)` fields match `capabilities.py:101-127`. `SkillFrontmatter` new fields match Task 1.
