# CLI First-Class Capabilities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connected CLIs become automatically discoverable capabilities (capability registry + system-prompt section), and questions about external-data domains (calendar, email, tasks, repos, deployments) are never answered from the model's head — either a tool is mandated or Jarvis refuses honestly.

**Architecture:** Per the approved spec `docs/superpowers/specs/2026-06-10-cli-first-class-capabilities-design.md` (AD-CLI1..AD-CLI10). CLIs stay plain router tools; we add metadata (a `capabilities` block on `CliSpec`), a provider that mirrors connect/disconnect into the `CapabilityRegistry` (new `source="cli"`), a "CONNECTED CLIS" system-prompt section, and a deterministic pre-brain **evidence gate** (regex only, no LLM, AP-9/AP-11). One source of truth: the CLI catalog.

**Tech Stack:** Python 3.11, frozen dataclasses + Pydantic (existing `jarvis/clis/spec.py` pattern), pytest (`asyncio_mode=auto`), no new dependencies, no entry-point changes (no `pip install -e .` needed).

**Language policy reminder:** every artifact is English. The only German strings are the spoken refusal templates — each such line MUST carry an inline `# i18n-allow` marker (CI language gate) and the strings must be TTS-safe (no tool slugs, no markdown).

---

## Task 0: Preflight

- [ ] **Step 1: Verify the worktree is the live one**

Run: `pwsh scripts/preflight.ps1`
Expected: exit code 0. If non-zero, fix the reported issue before any edit (BUG-006/014 four-layer restore trap).

- [ ] **Step 2: Baseline test run for the touched areas**

Run: `pytest tests/unit/clis/ tests/unit/brain/test_routing.py -q`
Expected: all pass (note the count — it must not shrink later).

---

## Task 1: `CliCapabilityDecl` on the spec model

**Files:**
- Modify: `jarvis/clis/spec.py`
- Test: `tests/unit/clis/test_spec.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/clis/test_spec.py`)

```python
def _base_model_kwargs() -> dict:
    return dict(
        name="demo",
        display_name="Demo CLI",
        description="Demo CLI for tests.",
        binary_name="demo",
        check_command=["demo", "--version"],
        version_parse_regex=r"(\S+)",
        install={"manual_url": "https://example.com"},
        auth={"type": "none"},
    )


def test_capabilities_block_roundtrip():
    from jarvis.clis.spec import CliSpec, CliSpecModel

    model = CliSpecModel(
        **_base_model_kwargs(),
        capabilities=[{
            "domains": ["repos"],
            "verbs": ["zeig", "list", "show"],
            "objects": ["pull request", "issue"],
            "description": "GitHub repos, PRs and issues.",
        }],
    )
    spec = CliSpec.from_model(model)
    assert spec.capabilities[0].domains == ("repos",)
    assert spec.capabilities[0].verbs == ("zeig", "list", "show")
    assert spec.capabilities[0].objects == ("pull request", "issue")
    assert spec.capabilities[0].description == "GitHub repos, PRs and issues."


def test_capabilities_default_empty():
    from jarvis.clis.spec import CliSpec, CliSpecModel

    spec = CliSpec.from_model(CliSpecModel(**_base_model_kwargs()))
    assert spec.capabilities == ()


def test_capabilities_reject_empty_lists():
    import pytest
    from pydantic import ValidationError
    from jarvis.clis.spec import CliSpecModel

    with pytest.raises(ValidationError):
        CliSpecModel(
            **_base_model_kwargs(),
            capabilities=[{
                "domains": [], "verbs": ["x"], "objects": ["y"],
                "description": "broken",
            }],
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/clis/test_spec.py -q`
Expected: FAIL — `ValidationError`/`TypeError` ("capabilities" unknown field) or `AttributeError`.

- [ ] **Step 3: Implement in `jarvis/clis/spec.py`**

Insert the dataclass after `RiskConfig` (line 83):

```python
@dataclass(frozen=True, slots=True)
class CliCapabilityDecl:
    """Declares what a CLI can do, in capability-registry vocabulary.

    Mirrors the paired-skill pairing fields (intent verbs + domain nouns) so
    ``resolve_intent`` works without changes. ``domains`` ties the CLI to the
    evidence-gate domains (see jarvis/clis/capability_provider.py DOMAIN_VOCAB).
    """

    domains: tuple[str, ...]
    verbs: tuple[str, ...]
    objects: tuple[str, ...]
    description: str
```

Add the field to `CliSpec` (after `source`, line 100):

```python
    capabilities: tuple[CliCapabilityDecl, ...] = ()
```

Extend `CliSpec.from_model` (append inside the `cls(...)` call, after `source=...`):

```python
            capabilities=tuple(
                CliCapabilityDecl(
                    domains=tuple(c.domains),
                    verbs=tuple(c.verbs),
                    objects=tuple(c.objects),
                    description=c.description,
                )
                for c in model.capabilities
            ),
```

Add the Pydantic model after `RiskConfigModel` (line 193):

```python
class CliCapabilityDeclModel(BaseModel):
    domains: list[str] = Field(min_length=1)
    verbs: list[str] = Field(min_length=1)
    objects: list[str] = Field(min_length=1)
    description: str = Field(min_length=1, max_length=200)
```

Add to `CliSpecModel` (after `source`, line 210):

```python
    capabilities: list[CliCapabilityDeclModel] = Field(default_factory=list, max_length=5)
```

Extend `__all__` with `"CliCapabilityDecl", "CliCapabilityDeclModel"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/clis/test_spec.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/clis/spec.py tests/unit/clis/test_spec.py
git commit -m "feat(clis): capabilities declaration block on CliSpec (AD-CLI9)"
```

---

## Task 2: `source="cli"` in the CapabilityRegistry

**Files:**
- Modify: `jarvis/core/capabilities.py:121` (Literal) and `:215` (object-required rule)
- Test: `tests/unit/core/test_capabilities_cli_source.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""CLI-source capabilities: registrable, object-required matching (AD-CLI2)."""
from jarvis.core.capabilities import Capability, CapabilityRegistry


def _cli_cap() -> Capability:
    return Capability(
        id="cli.gh",
        source="cli",
        verbs=("zeig", "list", "show"),
        objects=("pull request", "issue", "repo"),
        description="GitHub repos, PRs and issues via gh.",
        risk_tier="monitor",
        requires_evidence=True,
    )


def test_cli_source_registers_and_resolves_with_object():
    reg = CapabilityRegistry()
    reg.register(_cli_cap())
    cap = reg.resolve_intent("zeig mir die offenen Issues")
    assert cap is not None and cap.id == "cli.gh"


def test_cli_source_requires_object_match():
    # A bare generic verb must NOT resolve to a CLI capability — same
    # domain-specific rule as paired skills (prevents verb hijacking).
    reg = CapabilityRegistry()
    reg.register(_cli_cap())
    assert reg.resolve_intent("zeig mal her") is None


def test_paired_skill_beats_cli_on_tie():
    reg = CapabilityRegistry()
    reg.register(_cli_cap())
    reg.register(Capability(
        id="skill.paired.github",
        source="skill",
        verbs=("zeig",),
        objects=("issue",),
        description="Paired GitHub plugin skill.",
        risk_tier="ask",
        requires_evidence=True,
    ))
    cap = reg.resolve_intent("zeig mir die Issues")
    assert cap is not None and cap.id == "skill.paired.github"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/core/test_capabilities_cli_source.py -q`
Expected: FAIL — Pydantic-free dataclass accepts any string, BUT `test_cli_source_requires_object_match` fails (verb-only currently scores 1 and resolves).

- [ ] **Step 3: Implement in `jarvis/core/capabilities.py`**

Line 121, extend the Literal:

```python
    source: Literal["router_tool", "mcp", "harness", "local_action", "skill", "cli"]
```

Line 215 (`resolve_intent` body), extend the object-required rule and add a comment line:

```python
            # Plugin/paired-skill AND CLI capabilities are DOMAIN-SPECIFIC: they
            # must match a domain object (noun), not just a generic dispatch
            # verb. CLI verbs ("zeig", "list") are deliberately generic — a
            # verb-only hit would hijack unrelated requests (AD-CLI2/AD-CLI6).
            if cap.source in ("skill", "cli") and not obj_hit:
                continue
```

(The score logic is unchanged: `cli` verb+object scores 2, paired skill verb+object scores 3 → skill wins ties, satisfying AD-CLI6 inside `resolve_intent`.)

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/core/test_capabilities_cli_source.py tests/unit/core/ -q`
Expected: PASS, and no regressions in the existing `tests/unit/core/` suite (especially `test_resolve_intent_skill_precision.py`).

- [ ] **Step 5: Commit**

```bash
git add jarvis/core/capabilities.py tests/unit/core/test_capabilities_cli_source.py
git commit -m "feat(capabilities): cli source with object-required matching (AD-CLI2)"
```

---

## Task 3: Capability provider module

**Files:**
- Create: `jarvis/clis/capability_provider.py`
- Test: `tests/unit/clis/test_capability_provider.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
"""Capability provider: CliSpec.capabilities -> CapabilityRegistry (AD-CLI1..3)."""
from dataclasses import dataclass, field

from jarvis.clis.capability_provider import (
    DOMAIN_VOCAB,
    capability_for_spec,
    connected_domain_tool_map,
    refusal_hint,
    sync_registry,
)
from jarvis.clis.spec import (
    AuthConfig, CliCapabilityDecl, CliSpec, CliStatus, InstallMethods, RiskConfig,
)
from jarvis.core.capabilities import CapabilityRegistry


def _spec(name: str, domains: tuple[str, ...] = ("repos",)) -> CliSpec:
    return CliSpec(
        name=name,
        display_name=name.upper(),
        description=f"{name} CLI.",
        homepage="https://example.com",
        binary_name=name,
        check_command=(name, "--version"),
        version_parse_regex=r"(\S+)",
        install=InstallMethods(manual_url="https://example.com"),
        auth=AuthConfig(type="none"),
        risk=RiskConfig(default_tier="monitor"),
        capabilities=(
            CliCapabilityDecl(
                domains=domains,
                verbs=("zeig", "list", "show"),
                objects=("pull request", "issue"),
                description=f"{name} test capability.",
            ),
        ),
    )


@dataclass
class _FakeTool:
    name: str


@dataclass
class _FakeCliRegistry:
    specs: dict
    active: list
    status: dict = field(default_factory=dict)

    def catalog(self):
        class _Cat:
            def __init__(self, specs): self._specs = specs
            def all(self): return self._specs
        return _Cat(self.specs)

    def active_tools(self):
        return self.active

    def all_status(self):
        return self.status


def test_capability_for_spec_maps_fields():
    cap = capability_for_spec(_spec("gh"))
    assert cap is not None
    assert cap.id == "cli.gh"
    assert cap.source == "cli"
    assert cap.verbs == ("zeig", "list", "show")
    assert cap.requires_evidence is True
    assert cap.risk_tier == "monitor"


def test_capability_for_spec_none_without_block():
    from dataclasses import replace
    assert capability_for_spec(replace(_spec("gh"), capabilities=())) is None


def test_sync_registers_usable_and_deregisters_unusable():
    cap_reg = CapabilityRegistry()
    spec = _spec("gh")
    fake = _FakeCliRegistry({"gh": spec}, active=[_FakeTool("cli_gh")])
    sync_registry(fake, cap_reg)
    assert any(c.id == "cli.gh" for c in cap_reg.all())

    fake.active = []  # disconnected
    sync_registry(fake, cap_reg)
    assert not any(c.id == "cli.gh" for c in cap_reg.all())


def test_connected_domain_tool_map():
    spec = _spec("gh", domains=("repos",))
    fake = _FakeCliRegistry({"gh": spec}, active=[_FakeTool("cli_gh")])
    assert connected_domain_tool_map(fake) == {"repos": "cli_gh"}
    fake.active = []
    assert connected_domain_tool_map(fake) == {}


def test_refusal_hint_installed_not_connected():
    spec = _spec("gam", domains=("calendar",))
    fake = _FakeCliRegistry(
        {"gam": spec}, active=[],
        status={"gam": CliStatus(installed=True, auth_status="not_connected")},
    )
    hint_de = refusal_hint("calendar", fake, "de")
    assert "GAM" in hint_de and "installiert" in hint_de
    hint_en = refusal_hint("calendar", fake, "en")
    assert "GAM" in hint_en and "installed" in hint_en


def test_refusal_hint_empty_for_unknown_domain():
    fake = _FakeCliRegistry({}, active=[])
    assert refusal_hint("calendar", fake, "de") == ""


def test_domain_vocab_contains_evidence_domains():
    assert {"calendar", "email", "tasks", "repos", "deployments"} <= DOMAIN_VOCAB
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/clis/test_capability_provider.py -q`
Expected: FAIL — `ModuleNotFoundError: jarvis.clis.capability_provider`.

- [ ] **Step 3: Create `jarvis/clis/capability_provider.py`**

```python
"""Bridge connected CLIs into the CapabilityRegistry.

Design: docs/superpowers/specs/2026-06-10-cli-first-class-capabilities-design.md
(AD-CLI1..AD-CLI3). One Capability per CLI (``cli.<name>``), registered only
while the CLI is usable (installed + authenticated). All functions are
defensive: an infrastructure failure degrades to a no-op/empty result and
must never propagate into the caller (registry lifecycle or voice path).
"""
from __future__ import annotations

import logging
from typing import Any

from jarvis.clis.spec import CliSpec
from jarvis.clis.tool import TOOL_NAME_PREFIX
from jarvis.core.capabilities import Capability

log = logging.getLogger(__name__)

# Documented domain vocabulary for CliCapabilityDecl.domains. The evidence
# gate only consumes the configured subset (calendar/email/tasks/repos/
# deployments); the rest exists so catalog curation stays typo-guarded
# (parity test in tests/unit/clis/test_seed_catalog_capabilities.py).
DOMAIN_VOCAB: frozenset[str] = frozenset({
    "calendar", "email", "tasks", "repos", "deployments",
    "cloud", "containers", "kubernetes", "database", "payments",
    "messaging", "storage", "workspace",
})

CAP_ID_PREFIX = "cli."


def capability_for_spec(spec: CliSpec) -> Capability | None:
    """Merge a spec's capability declarations into one Capability, or None."""
    if not spec.capabilities:
        return None
    verbs: list[str] = []
    objects: list[str] = []
    descriptions: list[str] = []
    for decl in spec.capabilities:
        verbs.extend(v for v in decl.verbs if v not in verbs)
        objects.extend(o for o in decl.objects if o not in objects)
        if decl.description not in descriptions:
            descriptions.append(decl.description)
    return Capability(
        id=f"{CAP_ID_PREFIX}{spec.name}",
        source="cli",
        verbs=tuple(verbs),
        objects=tuple(objects),
        description=" ".join(descriptions),
        risk_tier=spec.risk.default_tier,
        requires_evidence=True,
    )


def sync_registry(cli_registry: Any, capability_registry: Any) -> None:
    """Mirror the usable-CLI set into the CapabilityRegistry. Idempotent.

    Derives everything from the catalog + active tool set — no module state,
    so repeated calls (bootstrap, every refresh_status) converge.
    """
    try:
        active = {t.name for t in cli_registry.active_tools()}
        for spec in cli_registry.catalog().all().values():
            cap = capability_for_spec(spec)
            if cap is None:
                continue
            if f"{TOOL_NAME_PREFIX}{spec.name}" in active:
                capability_registry.register(cap)
            else:
                capability_registry.deregister(cap.id)
    except Exception:  # noqa: BLE001 — sync must never break the lifecycle
        log.debug("cli capability sync failed", exc_info=True)


def connected_domain_tool_map(cli_registry: Any) -> dict[str, str]:
    """Map evidence domain -> cli tool name for usable CLIs only.

    First registered CLI per domain wins (catalog order is deterministic).
    """
    out: dict[str, str] = {}
    try:
        active = {t.name for t in cli_registry.active_tools()}
        for spec in cli_registry.catalog().all().values():
            tool_name = f"{TOOL_NAME_PREFIX}{spec.name}"
            if tool_name not in active:
                continue
            for decl in spec.capabilities:
                for domain in decl.domains:
                    out.setdefault(domain, tool_name)
    except Exception:  # noqa: BLE001
        log.debug("cli domain map failed", exc_info=True)
    return out


def refusal_hint(domain: str, cli_registry: Any, lang: str) -> str:
    """One TTS-safe sentence pointing at the closest catalog CLI for *domain*.

    Used by the evidence gate's honest refusal (AD-CLI7): "installed but not
    connected" beats "available in the catalog". Returns "" when the catalog
    has no CLI for the domain.
    """
    try:
        status_map = cli_registry.all_status()
        for spec in cli_registry.catalog().all().values():
            if not any(domain in decl.domains for decl in spec.capabilities):
                continue
            st = status_map.get(spec.name)
            if st is not None and st.installed:
                if lang == "de":
                    return (
                        f" Die {spec.display_name} ist installiert, aber noch nicht"  # i18n-allow: spoken German voice reply
                        " verbunden — sag Bescheid, dann richten wir das ein."  # i18n-allow: spoken German voice reply
                    )
                return (
                    f" The {spec.display_name} is installed but not connected yet"
                    " — say the word and we'll set it up."
                )
            if lang == "de":
                return (
                    f" Im CLI-Katalog gibt es dafür die {spec.display_name} —"  # i18n-allow: spoken German voice reply
                    " ich kann sie mit dir einrichten."  # i18n-allow: spoken German voice reply
                )
            return (
                f" The CLI catalog has {spec.display_name} for that —"
                " I can set it up with you."
            )
    except Exception:  # noqa: BLE001
        log.debug("refusal hint failed", exc_info=True)
    return ""


__all__ = [
    "DOMAIN_VOCAB", "CAP_ID_PREFIX",
    "capability_for_spec", "sync_registry",
    "connected_domain_tool_map", "refusal_hint",
]
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/clis/test_capability_provider.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/clis/capability_provider.py tests/unit/clis/test_capability_provider.py
git commit -m "feat(clis): capability provider bridges connected CLIs into the registry (AD-CLI1..3)"
```

---

## Task 4: Lifecycle wiring in `CliToolRegistry`

**Files:**
- Modify: `jarvis/clis/registry.py` (bootstrap + refresh_status)
- Test: `tests/unit/clis/test_registry.py` (append)

- [ ] **Step 1: Write the failing test** (append to `tests/unit/clis/test_registry.py`; first READ that file and reuse its existing fake catalog/prober helpers for constructing the registry — the assertions below are the contract)

```python
async def test_refresh_status_syncs_capability_registry(monkeypatch):
    """Connect transition registers cli.<name>; disconnect deregisters it."""
    from jarvis.core.capabilities import CapabilityRegistry
    import jarvis.core.capabilities as cap_mod

    spy = CapabilityRegistry()
    monkeypatch.setattr(cap_mod, "get_registry", lambda: spy)

    # Build a CliToolRegistry exactly like the existing tests in this file do
    # (fake catalog with ONE spec + fake prober), but give the spec a
    # capabilities block:
    #   capabilities=(CliCapabilityDecl(domains=("repos",), verbs=("zeig",),
    #                 objects=("issue",), description="Test cap."),)
    # Prober first reports installed+connected, then not_connected.
    registry = _make_registry_with_connected_spec()  # reuse/adapt local helper

    await registry.bootstrap()
    assert any(c.id.startswith("cli.") for c in spy.all())

    _set_prober_disconnected(registry)  # adapt to the local fake prober
    await registry.refresh_status(_spec_name(registry))
    assert not any(c.id.startswith("cli.") for c in spy.all())
```

(Replace the three helper stubs with the concrete fakes already defined in `tests/unit/clis/test_registry.py` — same construction, plus the `capabilities` tuple on the spec. Do not invent a new fake style.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/clis/test_registry.py -q`
Expected: the new test FAILS (no capability ever registered); pre-existing tests still pass.

- [ ] **Step 3: Implement in `jarvis/clis/registry.py`**

Add a private method (after `_is_usable`, line 215):

```python
    def _sync_capabilities(self) -> None:
        """Mirror the usable-CLI set into the global CapabilityRegistry.

        Defensive: a capabilities-module failure must never break the CLI
        lifecycle (bootstrap/refresh), only disable intent resolution.
        """
        try:
            from jarvis.clis.capability_provider import sync_registry
            from jarvis.core.capabilities import get_registry

            sync_registry(self, get_registry())
        except Exception:  # noqa: BLE001
            log.debug("cli capability sync skipped", exc_info=True)
```

Call it in `bootstrap()` directly after `self._bootstrapped = True` (line 51):

```python
        self._bootstrapped = True
        self._sync_capabilities()
```

Call it in `refresh_status()` inside the existing `if tool_set_changed:` block (line 152), before the publish:

```python
        if tool_set_changed:
            self._sync_capabilities()
            await self._publish_brain_tools_changed(cli_name, tool_name in self._tools)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/clis/test_registry.py -q`
Expected: PASS (all, including pre-existing).

- [ ] **Step 5: Commit**

```bash
git add jarvis/clis/registry.py tests/unit/clis/test_registry.py
git commit -m "feat(clis): registry lifecycle mirrors usable CLIs into CapabilityRegistry (AD-CLI3)"
```

---

## Task 5: "CONNECTED CLIS" system-prompt section

**Files:**
- Create: `jarvis/clis/prompt_section.py`
- Modify: `jarvis/brain/manager.py` (`_build_system_prompt`, insert after the skills-section block ending at line 1291)
- Test: `tests/unit/clis/test_prompt_section.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
"""CONNECTED CLIS prompt section renderer (AD-CLI design §5.3)."""
from tests.unit.clis.test_capability_provider import _FakeCliRegistry, _FakeTool, _spec


def test_renders_connected_cli_with_description_and_examples():
    from jarvis.clis.prompt_section import render_connected_clis_section

    fake = _FakeCliRegistry({"gh": _spec("gh")}, active=[_FakeTool("cli_gh")])
    section = render_connected_clis_section(fake)
    assert "CONNECTED CLIS" in section
    assert "cli_gh" in section
    assert "gh test capability." in section  # decl description preferred
    assert "Answer ONLY from the tool result" in section


def test_empty_when_nothing_connected():
    from jarvis.clis.prompt_section import render_connected_clis_section

    fake = _FakeCliRegistry({"gh": _spec("gh")}, active=[])
    assert render_connected_clis_section(fake) == ""


def test_defensive_on_broken_registry():
    from jarvis.clis.prompt_section import render_connected_clis_section

    class _Broken:
        def active_tools(self):
            raise RuntimeError("boom")

    assert render_connected_clis_section(_Broken()) == ""
```

(Import note: if `_FakeCliRegistry`/`_spec` are private to the other test module, move them to a small shared helper `tests/unit/clis/_fakes.py` and import from there in both test files — do that refactor in this step.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/clis/test_prompt_section.py -q`
Expected: FAIL — `ModuleNotFoundError: jarvis.clis.prompt_section`.

- [ ] **Step 3: Create `jarvis/clis/prompt_section.py`**

```python
"""Render the CONNECTED CLIS system-prompt section.

Design §5.3: only connected/usable CLIs appear; section is "" when none are.
Mirrors render_available_skills_section (jarvis/skills/prompt_injection.py):
static per connect/disconnect, cheap to render, defensive against any
registry fault (the system prompt build must never crash, AP-18 spirit).
"""
from __future__ import annotations

import logging
from typing import Any

from jarvis.clis.tool import TOOL_NAME_PREFIX

log = logging.getLogger(__name__)

_HEADER = (
    "CONNECTED CLIS\n"
    "You have direct command-line tools for these connected services. Prefer "
    "them for matching requests instead of refusing or spawning a worker:\n"
)
_FOOTER = (
    "\nAnswer ONLY from the tool result — never invent external data. Prefer "
    "machine-readable output flags (--json, --format json) when the CLI "
    "supports them."
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
                summary = " ".join(
                    dict.fromkeys(d.description for d in spec.capabilities)
                )
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
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/clis/test_prompt_section.py -q`
Expected: PASS.

- [ ] **Step 5: Wire into `_build_system_prompt`** (`jarvis/brain/manager.py`, insert directly after the skills-section `except` block that ends at line 1291, before `if self._system_prompt_extra:`)

```python
        # CLI first-class capabilities (design 2026-06-10, §5.3): list the
        # connected CLIs so the brain can pick them for matching requests.
        # Mirrors the skills section above. Rendered from the shared registry
        # published by the UI server; absent registry → section omitted.
        try:
            from jarvis.clis.prompt_section import render_connected_clis_section
            from jarvis.clis.shared import get_active_registry

            _cli_reg = get_active_registry()
            if _cli_reg is not None:
                _cli_section = render_connected_clis_section(_cli_reg)
                if _cli_section:
                    parts.append(_cli_section)
        except Exception:  # noqa: BLE001
            log.debug("connected-CLIs section omitted", exc_info=True)
```

- [ ] **Step 6: Regression run**

Run: `pytest tests/unit/brain/ -q -x --timeout=120`
Expected: same pass count as baseline (the new block is additive + defensive).

- [ ] **Step 7: Commit**

```bash
git add jarvis/clis/prompt_section.py tests/unit/clis/test_prompt_section.py tests/unit/clis/_fakes.py tests/unit/clis/test_capability_provider.py jarvis/brain/manager.py
git commit -m "feat(brain): CONNECTED CLIS system-prompt section from shared registry (design 5.3)"
```

---

## Task 6: `EvidenceDomainsConfig`

**Files:**
- Modify: `jarvis/core/config.py` (new model before `BrainConfig` line 510; new field on `BrainConfig` after `routing` line 545)
- Test: `tests/unit/core/test_evidence_domains_config.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""[brain.evidence_domains] config model defaults + override (AD-CLI5)."""
from jarvis.core.config import BrainConfig, EvidenceDomainsConfig


def test_defaults_ship_five_domains_enabled():
    cfg = EvidenceDomainsConfig()
    assert cfg.enabled is True
    assert set(cfg.domains) == {"calendar", "email", "tasks", "repos", "deployments"}
    assert "kalender" in cfg.domains["calendar"]
    assert "inbox" in cfg.domains["email"]


def test_brain_config_carries_evidence_domains():
    cfg = BrainConfig()
    assert cfg.evidence_domains.enabled is True


def test_toml_override_shape():
    cfg = BrainConfig.model_validate({
        "evidence_domains": {
            "enabled": False,
            "domains": {"calendar": ["kalender"]},
        }
    })
    assert cfg.evidence_domains.enabled is False
    assert cfg.evidence_domains.domains == {"calendar": ["kalender"]}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/core/test_evidence_domains_config.py -q`
Expected: FAIL — `ImportError: EvidenceDomainsConfig`.

- [ ] **Step 3: Implement in `jarvis/core/config.py`** (insert before `class BrainConfig`, line 510)

```python
class EvidenceDomainsConfig(BaseModel):
    """Evidence-required domains (CLI first-class capabilities, 2026-06-10).

    Questions in these domains are never answered from the model's head:
    either a capability covers the domain (the gate injects a mandatory-tool
    directive) or the gate returns a deterministic honest refusal. Keyword
    lists are DE+EN, lowercase; matching is word-boundary, umlaut-normalised
    (jarvis/brain/evidence_gate.py). TOML shape:

        [brain.evidence_domains]
        enabled = true
        [brain.evidence_domains.domains]
        calendar = ["kalender", "termin", ...]
    """

    enabled: bool = True
    domains: dict[str, list[str]] = Field(default_factory=lambda: {
        "calendar": [
            "kalender", "termin", "termine", "steht heute", "steht morgen",
            "steht diese woche", "calendar", "appointment", "appointments",
        ],
        "email": [
            "mail", "mails", "e-mail", "e-mails", "email", "emails",
            "posteingang", "postfach", "inbox", "ungelesene",
        ],
        "tasks": [
            "aufgaben", "todo", "todos", "to-do", "task", "tasks",
        ],
        "repos": [
            "pull request", "pull requests", "pull-request", "pr", "prs",
            "issue", "issues", "repo", "repos", "repository",
        ],
        "deployments": [
            "deployment", "deployments", "deploy-status",
            "build-status", "build status",
        ],
    })
```

Add the field to `BrainConfig` after `routing` (line 545):

```python
    # CLI first-class capabilities: evidence-required external-data domains.
    evidence_domains: EvidenceDomainsConfig = Field(
        default_factory=EvidenceDomainsConfig,
    )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/core/test_evidence_domains_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/core/config.py tests/unit/core/test_evidence_domains_config.py
git commit -m "feat(config): [brain.evidence_domains] model with shipped defaults (AD-CLI5)"
```

---

## Task 7: Evidence gate (pure function)

**Files:**
- Create: `jarvis/brain/evidence_gate.py`
- Test: `tests/unit/brain/test_evidence_gate.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
"""Evidence gate verdicts + hard negatives (AD-CLI4..AD-CLI8)."""
from jarvis.brain.evidence_gate import EvidenceVerdict, check_evidence_domain
from jarvis.core.capabilities import Capability, CapabilityRegistry

DOMAINS = {
    "calendar": ["kalender", "termin", "termine", "steht heute", "calendar"],
    "email": ["mail", "mails", "inbox", "postfach"],
    "repos": ["pull request", "pr", "prs", "issue", "issues"],
}


def _gate(text, *, registry=None, tool_map=None, hint_fn=None, enabled=True):
    return check_evidence_domain(
        text,
        enabled=enabled,
        domains=DOMAINS,
        capability_registry=registry if registry is not None else CapabilityRegistry(),
        domain_tool_map=tool_map or {},
        refusal_hint_fn=hint_fn,
    )


# --- verdict: require_tool -------------------------------------------------

def test_calendar_question_with_cli_requires_tool():
    v = _gate("Was steht heute noch an?", tool_map={"calendar": "cli_gam"})
    assert v.kind == "require_tool"
    assert v.tool_name == "cli_gam"
    assert "cli_gam" in v.directive and "NEVER invent" in v.directive


def test_umlaut_form_matches():
    v = _gate("Welche Termine habe ich morgen?", tool_map={"calendar": "cli_gam"})
    assert v.kind == "require_tool"


# --- verdict: honest_refusal -------------------------------------------------

def test_calendar_question_without_anything_refuses_honestly():
    v = _gate("Was steht heute noch an?")
    assert v.kind == "honest_refusal"
    assert "Kalenderzugriff" in v.refusal_text


def test_refusal_appends_hint():
    v = _gate(
        "Was steht heute noch an?",
        hint_fn=lambda domain, lang: " HINT",
    )
    assert v.refusal_text.endswith("HINT")


def test_english_refusal_for_english_text():
    v = _gate("Do I have any appointments on my calendar today?")
    assert v.kind == "honest_refusal"
    assert "calendar access" in v.refusal_text


# --- verdict: pass (preference order, AD-CLI6) -------------------------------

def test_non_cli_capability_wins_and_passes():
    reg = CapabilityRegistry()
    reg.register(Capability(
        id="skill.paired.gmail", source="skill",
        verbs=("lies",), objects=("mail", "inbox", "postfach"),
        description="Paired Gmail skill.", risk_tier="ask",
        requires_evidence=True,
    ))
    v = _gate("Hab ich neue Mails?", registry=reg, tool_map={"email": "cli_gam"})
    assert v.kind == "pass"


# --- hard negatives -----------------------------------------------------------

def test_smalltalk_passes():
    assert _gate("Danke dir, das war's").kind == "pass"
    assert _gate("Wie geht es dir heute?").kind == "pass"


def test_domain_word_in_passing_passes():
    # statement, not a lookup — must not trigger
    assert _gate("Ich habe dir das vorhin per Mail geschickt").kind == "pass"


def test_definition_question_passes():
    assert _gate("Was ist ein Pull Request?").kind == "pass"  # i18n-allow: German test utterance under test
    assert _gate("What is an issue tracker?").kind == "pass"


def test_send_action_passes_to_existing_gates():
    # imperative "schick eine Mail" is the unsupported-intent gate's turf
    assert _gate("Schick eine Mail an Christoph").kind == "pass"


def test_disabled_flag_bypasses():
    assert _gate("Was steht heute noch an?", enabled=False).kind == "pass"


def test_empty_and_garbage_pass():
    assert _gate("").kind == "pass"
    assert _gate("   ").kind == "pass"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/brain/test_evidence_gate.py -q`
Expected: FAIL — `ModuleNotFoundError: jarvis.brain.evidence_gate`.

- [ ] **Step 3: Create `jarvis/brain/evidence_gate.py`**

```python
"""Evidence gate — deterministic honesty guard for external-data domains.

Design: docs/superpowers/specs/2026-06-10-cli-first-class-capabilities-design.md
(AD-CLI4..AD-CLI8). Pure regex + in-memory registry lookups — NO LLM call,
NO disk/network IO (AP-9/AP-11). Called once per turn from
BrainManager.generate(); every failure path degrades to PASS.

Verdicts:
  pass            — turn proceeds unchanged (default for 99% of turns).
  require_tool    — a connected CLI covers the matched domain: the manager
                    injects ``directive`` into this turn's system prompt.
  honest_refusal  — nothing covers the domain: the manager speaks
                    ``refusal_text`` deterministically (no LLM involved).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, Sequence

from jarvis.core.capabilities import _normalize

# A domain keyword alone must not trigger (hard negative: "Ich habe dir das
# per Mail geschickt" mentions mail in passing). The utterance must also look
# like a question/lookup or a read-imperative on the domain.
_LOOKUP_SHAPE_RE = re.compile(
    r"\b(was|wann|welche|welcher|welches|wie viele|wieviele|gibt es|gibts|"
    r"hab ich|habe ich|steht|stehen|ansteht|anstehen|zeig|zeige|check|checke|"
    r"pruef|pruefe|liste|list|lies|lese|fasse|what|when|which|how many|"
    r"do i have|any|anything|is there|are there|show|summarize|read)\b"
)

# Definitional/explanatory questions are general knowledge, not a data lookup
# ("Was ist ein Pull Request?") — never force a tool call for them.  # i18n-allow: quotes German input example
_DEFINITION_RE = re.compile(
    r"\b(was ist ein|was ist eine|was sind|was bedeutet|wofuer steht|"  # i18n-allow: German input vocabulary
    r"what is a|what is an|what are|what does|explain|erklaer)\b"
)


@dataclass(frozen=True)
class EvidenceVerdict:
    kind: Literal["pass", "require_tool", "honest_refusal"]
    domain: str = ""
    tool_name: str = ""
    directive: str = ""
    refusal_text: str = ""


_PASS = EvidenceVerdict(kind="pass")

_REFUSAL_DE: dict[str, str] = {
    "calendar": "Ich habe aktuell keinen Kalenderzugriff.",  # i18n-allow: spoken German voice reply
    "email": "Ich habe aktuell keinen Zugriff auf dein Postfach.",  # i18n-allow: spoken German voice reply
    "tasks": "Ich habe aktuell keinen Zugriff auf deine Aufgaben.",  # i18n-allow: spoken German voice reply
    "repos": "Ich habe aktuell keinen Zugriff auf deine Repositories.",  # i18n-allow: spoken German voice reply
    "deployments": "Ich habe aktuell keinen Zugriff auf deine Deployments.",  # i18n-allow: spoken German voice reply
}
_REFUSAL_DE_FALLBACK = "Dafuer habe ich aktuell keinen Datenzugriff."  # i18n-allow: spoken German voice reply

_REFUSAL_EN: dict[str, str] = {
    "calendar": "I have no calendar access right now.",
    "email": "I have no access to your inbox right now.",
    "tasks": "I have no access to your tasks right now.",
    "repos": "I have no access to your repositories right now.",
    "deployments": "I have no access to your deployments right now.",
}
_REFUSAL_EN_FALLBACK = "I have no data access for that right now."


def _detect_lang(text: str) -> str:
    if re.search(r"[äöüÄÖÜß]", text):  # i18n-allow: German diacritic detection
        return "de"
    if re.search(
        r"\b(was|wie|welche|welcher|steht|stehen|heute|morgen|hab|habe|"
        r"meine|meinem|bitte|gibt)\b",
        text,
        re.I,
    ):
        return "de"
    return "en"


def check_evidence_domain(
    text: str,
    *,
    enabled: bool,
    domains: Mapping[str, Sequence[str]],
    capability_registry: Any,
    domain_tool_map: Mapping[str, str],
    refusal_hint_fn: Callable[[str, str], str] | None = None,
) -> EvidenceVerdict:
    """Classify one utterance against the evidence-required domains."""
    if not enabled:
        return _PASS
    t = (text or "").strip()
    if not t:
        return _PASS
    normalised = _normalize(t)
    if _DEFINITION_RE.search(normalised):
        return _PASS
    if not _LOOKUP_SHAPE_RE.search(normalised):
        return _PASS

    matched_domain = ""
    for domain, keywords in domains.items():
        if any(
            re.search(r"\b" + re.escape(_normalize(kw)) + r"\b", normalised)
            for kw in keywords
        ):
            matched_domain = domain
            break
    if not matched_domain:
        return _PASS

    # AD-CLI6 preference: any non-CLI capability covering the domain wins —
    # the existing machinery (paired skill, router tool, MCP) owns the turn.
    domain_keywords = [_normalize(k) for k in domains[matched_domain]]
    try:
        caps = capability_registry.all() if capability_registry is not None else ()
    except Exception:  # noqa: BLE001 — registry fault degrades to PASS
        return _PASS
    for cap in caps:
        if getattr(cap, "source", "") == "cli":
            continue
        objs = {_normalize(o) for o in getattr(cap, "objects", ())}
        if matched_domain in objs or objs.intersection(domain_keywords):
            return _PASS

    tool_name = domain_tool_map.get(matched_domain, "")
    if tool_name:
        directive = (
            f"MANDATORY THIS TURN: the user is asking about {matched_domain} "
            f"data. You MUST call the `{tool_name}` tool (read-only command, "
            f"prefer a --json/--format json output flag) BEFORE answering, "
            f"and answer ONLY from its result. If the call fails, say that it "
            f"failed and why — NEVER invent {matched_domain} data."
        )
        return EvidenceVerdict(
            kind="require_tool",
            domain=matched_domain,
            tool_name=tool_name,
            directive=directive,
        )

    lang = _detect_lang(t)
    base = (
        _REFUSAL_DE.get(matched_domain, _REFUSAL_DE_FALLBACK)
        if lang == "de"
        else _REFUSAL_EN.get(matched_domain, _REFUSAL_EN_FALLBACK)
    )
    hint = ""
    if refusal_hint_fn is not None:
        try:
            hint = refusal_hint_fn(matched_domain, lang) or ""
        except Exception:  # noqa: BLE001
            hint = ""
    return EvidenceVerdict(
        kind="honest_refusal",
        domain=matched_domain,
        refusal_text=base + hint,
    )


__all__ = ["EvidenceVerdict", "check_evidence_domain"]
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/brain/test_evidence_gate.py -q`
Expected: PASS (all, including every hard negative).

- [ ] **Step 5: Commit**

```bash
git add jarvis/brain/evidence_gate.py tests/unit/brain/test_evidence_gate.py
git commit -m "feat(brain): evidence gate — deterministic honesty guard for external-data domains (AD-CLI4..8)"
```

---

## Task 8: Wire the gate into `BrainManager.generate()`

**Files:**
- Modify: `jarvis/brain/manager.py`:
  - class attributes near `_skill_turn_match` (line 1803)
  - per-turn reset right after `self._skill_turn_match = self._match_skill_for_turn(user_text)` (line 2987)
  - gate call after the force-spawn block (after line 3081, before the budget gate at 3083)
  - directive append in `_build_system_prompt` (after the CONNECTED CLIS block from Task 5)
  - `_smalltalk_tool_override` (line 2020)
- Test: `tests/unit/brain/test_evidence_gate_wiring.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
"""Manager wiring for the evidence gate: override + defensive degradation."""
from types import SimpleNamespace

from jarvis.brain.manager import BrainManager


def _bare_manager() -> BrainManager:
    m = BrainManager.__new__(BrainManager)
    m._tools = {"screenshot": object(), "cli_gam": object(), "spawn-worker": object()}
    return m


def test_smalltalk_override_keeps_required_evidence_tool():
    m = _bare_manager()
    m._evidence_required_tool = "cli_gam"
    visible = m._smalltalk_tool_override()
    assert "cli_gam" in visible
    assert "spawn-worker" not in visible


def test_smalltalk_override_unchanged_without_required_tool():
    m = _bare_manager()
    m._evidence_required_tool = ""
    visible = m._smalltalk_tool_override()
    assert "cli_gam" not in visible


def test_run_evidence_gate_degrades_to_pass_on_missing_config():
    m = _bare_manager()
    m._config = SimpleNamespace(brain=SimpleNamespace())  # no evidence_domains
    verdict = m._run_evidence_gate("Was steht heute noch an?")
    assert verdict.kind == "pass"


def test_run_evidence_gate_refuses_without_any_integration(monkeypatch):
    import jarvis.clis.shared as shared

    m = _bare_manager()
    m._config = SimpleNamespace(
        brain=SimpleNamespace(
            evidence_domains=SimpleNamespace(
                enabled=True,
                domains={"calendar": ["kalender", "steht heute"]},
            )
        )
    )
    monkeypatch.setattr(shared, "get_active_registry", lambda: None)
    # Fresh, empty capability registry so no other source covers the domain:
    import jarvis.core.capabilities as cap_mod
    monkeypatch.setattr(cap_mod, "get_registry", lambda: cap_mod.CapabilityRegistry())
    verdict = m._run_evidence_gate("Was steht heute noch an?")
    assert verdict.kind == "honest_refusal"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/brain/test_evidence_gate_wiring.py -q`
Expected: FAIL — `AttributeError: _evidence_required_tool` / `_run_evidence_gate`.

- [ ] **Step 3: Implement in `jarvis/brain/manager.py`**

(a) Class attributes — add directly under the `_skills_omit_warned: bool = False` line (1812):

```python
    # Evidence gate (CLI first-class capabilities, 2026-06-10): per-turn
    # mandatory-tool directive + the tool that must stay visible even on a
    # smalltalk-classified turn ("was steht heute an" matches the smalltalk
    # allowlist forms). Reset at the start of every generate() turn.
    _evidence_directive: str = ""
    _evidence_required_tool: str = ""
```

(b) Per-turn reset — insert immediately after line 2987 (`self._skill_turn_match = ...`):

```python
        # Evidence-gate state is strictly per-turn — a stale directive must
        # never leak into a later prompt build (e.g. a skill turn that
        # early-returns before the gate runs).
        self._evidence_directive = ""
        self._evidence_required_tool = ""
```

(c) Gate call — insert after the force-spawn block (after the `return forced_spawn` block ending at line 3081, before the budget gate comment at 3083):

```python
        # Evidence gate (AD-CLI4..AD-CLI8): questions about external-data
        # domains (calendar/email/tasks/repos/deployments) are never answered
        # from the model's head. Either a connected CLI covers the domain
        # (mandatory-tool directive for this turn) or the answer is a
        # deterministic honest refusal. Pure regex + registry lookup, no LLM
        # (AP-11). Skill turns already returned above; non-CLI capabilities
        # (paired skills, router tools, MCP) make the gate stand down (PASS).
        verdict = self._run_evidence_gate(user_text)
        if verdict.kind == "honest_refusal":
            await self._record_response_side_effects(
                user_text=user_text,
                response_text=verdict.refusal_text,
                use_history=use_history,
                trace_id=turn_trace_id,
            )
            return verdict.refusal_text
        if verdict.kind == "require_tool":
            log.info(
                "Evidence gate: domain=%s requires tool %s this turn",
                verdict.domain, verdict.tool_name,
            )
            self._evidence_directive = verdict.directive
            self._evidence_required_tool = verdict.tool_name
```

(d) Gate helper — add as a method near `_check_unsupported_intent` (after line 1740):

```python
    def _run_evidence_gate(self, user_text: str) -> "EvidenceVerdict":
        """Defensive wrapper around check_evidence_domain.

        Any infrastructure fault (missing config field, no shared CLI
        registry, capabilities module error) degrades to PASS — the gate adds
        behaviour, it must never block the voice path.
        """
        from jarvis.brain.evidence_gate import EvidenceVerdict, check_evidence_domain

        try:
            cfg = self._config.brain.evidence_domains
            if not cfg.enabled:
                return EvidenceVerdict(kind="pass")
            from jarvis.clis.capability_provider import (
                connected_domain_tool_map,
                refusal_hint,
            )
            from jarvis.clis.shared import get_active_registry
            from jarvis.core.capabilities import get_registry

            cli_reg = get_active_registry()
            domain_map = (
                connected_domain_tool_map(cli_reg) if cli_reg is not None else {}
            )

            def _hint(domain: str, lang: str) -> str:
                if cli_reg is None:
                    return ""
                return refusal_hint(domain, cli_reg, lang)

            return check_evidence_domain(
                user_text,
                enabled=cfg.enabled,
                domains=cfg.domains,
                capability_registry=get_registry(),
                domain_tool_map=domain_map,
                refusal_hint_fn=_hint,
            )
        except Exception:  # noqa: BLE001
            log.debug("evidence gate degraded to PASS", exc_info=True)
            return EvidenceVerdict(kind="pass")
```

(e) Directive into the system prompt — in `_build_system_prompt`, directly after the CONNECTED CLIS block added in Task 5:

```python
        # Evidence gate directive (per-turn): forces a tool call before any
        # answer about an external-data domain. Empty on normal turns.
        if self._evidence_directive:
            parts.append(self._evidence_directive)
```

(f) Smalltalk override — in `_smalltalk_tool_override` (line 2030), extend:

```python
        allowed = self._SMALLTALK_SAFE_TOOLS
        if self._skill_turn_match is not None:
            allowed = allowed | {"run-skill"}
        if self._evidence_required_tool:
            # "was steht heute an" can classify as smalltalk; the mandated
            # evidence tool must stay visible or the directive is unfulfillable.
            allowed = allowed | {self._evidence_required_tool}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/brain/test_evidence_gate_wiring.py tests/unit/brain/test_evidence_gate.py -q`
Expected: PASS.

- [ ] **Step 5: Brain regression sweep**

Run: `pytest tests/unit/brain/ -q --timeout=180`
Expected: pass count ≥ baseline from Task 0; zero new failures (the gate returns PASS wherever the new config/infrastructure is absent, so existing routing tests are unaffected).

- [ ] **Step 6: Commit**

```bash
git add jarvis/brain/manager.py tests/unit/brain/test_evidence_gate_wiring.py
git commit -m "feat(brain): wire evidence gate into generate() with per-turn directive (AD-CLI8)"
```

---

## Task 9: Seed-catalog curation (capabilities + read-only whitelists + GAM entry)

**Files:**
- Modify: `jarvis/clis/catalog/seed_catalog.json`
- Test: `tests/unit/clis/test_seed_catalog_capabilities.py` (create)

- [ ] **Step 1: Write the failing parity test**

```python
"""Parity guard: curated capabilities blocks stay valid (anti-drift, AD-CLI9)."""
from jarvis.clis.capability_provider import DOMAIN_VOCAB
from jarvis.clis.catalog import CliCatalog

CURATED = {
    "gam", "gh", "glab", "gcloud", "az", "aws", "wrangler", "vercel",
    "netlify", "heroku", "railway", "flyctl", "render", "supabase",
    "firebase", "pscale", "neonctl", "stripe", "twilio", "docker", "kubectl",
}


def _seed_specs():
    return CliCatalog().all()


def test_curated_entries_declare_capabilities():
    specs = _seed_specs()
    for name in CURATED:
        assert name in specs, f"{name} missing from seed catalog"
        spec = specs[name]
        assert spec.capabilities, f"{name} must declare a capabilities block"
        for decl in spec.capabilities:
            assert decl.domains, f"{name}: empty domains"
            unknown = set(decl.domains) - DOMAIN_VOCAB
            assert not unknown, f"{name}: unknown domains {unknown}"
            assert decl.verbs, f"{name}: empty verbs"
            assert decl.objects, f"{name}: empty objects"
            assert decl.description, f"{name}: empty description"


def test_curated_entries_have_read_only_whitelist():
    specs = _seed_specs()
    for name in CURATED:
        assert specs[name].risk.whitelist_patterns, (
            f"{name} needs read-only whitelist patterns (safe-tier inline calls)"
        )


def test_evidence_domains_have_at_least_one_cli():
    specs = _seed_specs()
    covered = {
        d for s in specs.values() for decl in s.capabilities for d in decl.domains
    }
    assert {"calendar", "email", "repos", "deployments"} <= covered
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/clis/test_seed_catalog_capabilities.py -q`
Expected: FAIL — no entry has `capabilities`, `gam` missing.

- [ ] **Step 3: Add the GAM seed entry** (append to the entry array in `seed_catalog.json`)

```json
{
  "name": "gam",
  "display_name": "GAM (Google Workspace)",
  "description": "Google Workspace CLI: Calendar events, Gmail messages, Drive, Users.",
  "homepage": "https://github.com/GAM-team/GAM",
  "binary_name": "gam",
  "check_command": ["gam", "version"],
  "version_parse_regex": "GAM (\\S+)",
  "install": {
    "winget_id": null,
    "scoop_package": null,
    "npm_package": null,
    "pip_package": null,
    "cargo_package": null,
    "script_url": null,
    "manual_url": "https://github.com/GAM-team/GAM/wiki",
    "recommended": null
  },
  "auth": {
    "type": "oauth_cli",
    "login_command": ["gam", "oauth", "create"],
    "logout_command": ["gam", "oauth", "delete"],
    "status_command": ["gam", "oauth", "info"],
    "status_parse": "text_contains_email",
    "secret_keys": [],
    "env_vars": []
  },
  "risk": {
    "default_tier": "monitor",
    "blacklist_patterns": [
      "gam * delete *",
      "gam * remove *",
      "gam update user * password *"
    ],
    "whitelist_patterns": [
      "gam calendar * print *",
      "gam calendar * show *",
      "gam user * print *",
      "gam user * show *",
      "gam oauth info*",
      "gam version*"
    ]
  },
  "tool_schema_examples": [
    "gam calendar <primary-email> printevents after today before tomorrow",
    "gam user <primary-email> print messages query \"is:unread\" maxmessages 10"
  ],
  "icon": "google",
  "category": "workspace",
  "capabilities": [
    {
      "domains": ["calendar"],
      "verbs": ["zeig", "zeige", "lies", "check", "list", "show", "read"],
      "objects": ["kalender", "termin", "termine", "calendar", "appointment", "appointments"],
      "description": "Google Workspace Calendar: list and read events."
    },
    {
      "domains": ["email"],
      "verbs": ["zeig", "zeige", "lies", "check", "list", "show", "read"],
      "objects": ["mail", "mails", "email", "gmail", "posteingang", "inbox", "postfach"],
      "description": "Google Workspace Gmail: list and read messages."
    }
  ]
}
```

- [ ] **Step 4: Add `capabilities` + whitelist/blacklist additions to the existing 20 entries**

Full blocks for the seven most important entries (insert `capabilities` as a sibling of `category`; MERGE the listed patterns into the existing `risk` arrays, do not replace existing entries):

`gh`:

```json
"risk": {
  "default_tier": "monitor",
  "blacklist_patterns": ["gh repo delete *", "gh release delete *", "gh secret set *", "gh secret delete *"],
  "whitelist_patterns": ["gh pr list*", "gh pr view*", "gh pr status*", "gh issue list*", "gh issue view*", "gh repo list*", "gh repo view*", "gh run list*", "gh run view*", "gh release list*", "gh auth status*", "gh --version*"]
},
"capabilities": [{
  "domains": ["repos"],
  "verbs": ["zeig", "zeige", "lies", "check", "list", "show", "read", "erstell", "erstelle", "create", "merge"],
  "objects": ["pull request", "pull requests", "pr", "prs", "issue", "issues", "repo", "repos", "repository", "github", "release", "releases", "workflow", "actions"],
  "description": "GitHub: list/read/create repos, pull requests, issues, releases, workflow runs."
}]
```

`gcloud`:

```json
"whitelist_patterns_add": ["gcloud * list*", "gcloud * describe*", "gcloud config list*", "gcloud auth list*", "gcloud --version*"],
"blacklist_patterns_add": ["gcloud * delete *"],
"capabilities": [{
  "domains": ["cloud", "deployments"],
  "verbs": ["zeig", "zeige", "list", "show", "check", "describe", "deploy"],
  "objects": ["gcp", "google cloud", "gcloud", "compute", "cloud run", "instanz", "instanzen", "instances", "bucket", "buckets"],
  "description": "Google Cloud: list/describe projects, compute instances, Cloud Run services, storage."
}]
```

`vercel`:

```json
"whitelist_patterns_add": ["vercel ls*", "vercel list*", "vercel inspect*", "vercel whoami*", "vercel project ls*", "vercel logs*"],
"blacklist_patterns_add": ["vercel remove *", "vercel rm *", "vercel domains rm *", "vercel env rm *"],
"capabilities": [{
  "domains": ["deployments"],
  "verbs": ["zeig", "zeige", "list", "show", "check", "deploy", "deploye"],
  "objects": ["deployment", "deployments", "vercel", "build", "builds", "preview", "projekt", "projekte", "project", "projects"],
  "description": "Vercel: list deployments and projects, inspect builds, read logs, deploy."
}]
```

`supabase`:

```json
"whitelist_patterns_add": ["supabase projects list*", "supabase migration list*", "supabase status*", "supabase --version*"],
"blacklist_patterns_add": ["supabase db reset*", "supabase projects delete*"],
"capabilities": [{
  "domains": ["database"],
  "verbs": ["zeig", "zeige", "list", "show", "check"],
  "objects": ["supabase", "datenbank", "database", "tabelle", "tabellen", "tables", "migration", "migrations"],
  "description": "Supabase: list projects, migrations, database status."
}]
```

`docker`:

```json
"whitelist_patterns_add": ["docker ps*", "docker images*", "docker logs*", "docker inspect*", "docker version*", "docker info*"],
"blacklist_patterns_add": ["docker rm *", "docker rmi *", "docker system prune*", "docker volume rm *"],
"capabilities": [{
  "domains": ["containers"],
  "verbs": ["zeig", "zeige", "list", "show", "check", "starte", "start", "stoppe", "stop"],
  "objects": ["docker", "container", "containers", "image", "images", "volume", "volumes"],
  "description": "Docker: list/inspect containers, images, volumes; read logs."
}]
```

`kubectl`:

```json
"whitelist_patterns_add": ["kubectl get *", "kubectl describe *", "kubectl logs *", "kubectl version*"],
"blacklist_patterns_add": ["kubectl delete *", "kubectl drain *"],
"capabilities": [{
  "domains": ["kubernetes"],
  "verbs": ["zeig", "zeige", "list", "show", "check", "describe"],
  "objects": ["kubernetes", "k8s", "pod", "pods", "cluster", "namespace", "namespaces", "node", "nodes"],
  "description": "Kubernetes: get/describe pods, deployments, services; read logs."
}]
```

`stripe`:

```json
"whitelist_patterns_add": ["stripe customers list*", "stripe charges list*", "stripe invoices list*", "stripe balance retrieve*", "stripe products list*"],
"blacklist_patterns_add": ["stripe * delete*", "stripe refunds create*"],
"capabilities": [{
  "domains": ["payments"],
  "verbs": ["zeig", "zeige", "list", "show", "check"],
  "objects": ["stripe", "zahlung", "zahlungen", "payment", "payments", "kunde", "kunden", "customer", "customers", "invoice", "invoices", "umsatz"],
  "description": "Stripe: list customers, charges, invoices, products; read balance."
}]
```

Remaining 13 entries — same JSON shape, exact values (one `capabilities` decl each; whitelist = the listed read-only patterns merged in; blacklist = listed destructive patterns merged in):

| CLI | domains | objects | verbs | description | whitelist (merge) | blacklist (merge) |
|---|---|---|---|---|---|---|
| `aws` | `["cloud"]` | `["aws", "s3", "ec2", "lambda", "bucket", "buckets"]` | `["zeig", "zeige", "list", "show", "check", "describe"]` | `AWS: list/describe S3, EC2, Lambda resources.` | `aws * list*`, `aws * describe*`, `aws s3 ls*`, `aws sts get-caller-identity*` | `aws * delete*`, `aws * terminate-instances*` |
| `az` | `["cloud"]` | `["azure", "az", "resource group", "vm", "vms"]` | `["zeig", "zeige", "list", "show", "check"]` | `Azure: list/show resource groups, VMs, services.` | `az * list*`, `az * show*`, `az account show*` | `az * delete*` |
| `wrangler` | `["deployments", "cloud"]` | `["cloudflare", "wrangler", "worker", "workers", "pages"]` | `["zeig", "zeige", "list", "show", "check", "deploy"]` | `Cloudflare: list Workers and Pages projects, tail logs, deploy.` | `wrangler whoami*`, `wrangler deployments list*`, `wrangler pages project list*` | `wrangler delete*`, `wrangler kv:key delete*` |
| `netlify` | `["deployments"]` | `["netlify", "deployment", "deployments", "site", "sites"]` | `["zeig", "zeige", "list", "show", "check", "deploy"]` | `Netlify: list sites and deploys, read status.` | `netlify status*`, `netlify sites:list*`, `netlify api listSiteDeploys*` | `netlify sites:delete*` |
| `heroku` | `["deployments"]` | `["heroku", "app", "apps", "dyno", "dynos"]` | `["zeig", "zeige", "list", "show", "check"]` | `Heroku: list apps, dynos, releases; read logs.` | `heroku apps*`, `heroku ps*`, `heroku releases*`, `heroku logs*` | `heroku apps:destroy*`, `heroku addons:destroy*` |
| `railway` | `["deployments"]` | `["railway", "deployment", "deployments", "service", "services"]` | `["zeig", "zeige", "list", "show", "check", "deploy"]` | `Railway: list projects/services, read status and logs.` | `railway status*`, `railway list*`, `railway logs*` | `railway delete*` |
| `flyctl` | `["deployments"]` | `["fly", "fly.io", "flyctl", "app", "apps", "machine", "machines"]` | `["zeig", "zeige", "list", "show", "check", "deploy"]` | `Fly.io: list apps and machines, read status and logs.` | `flyctl status*`, `flyctl apps list*`, `flyctl logs*`, `flyctl machine list*` | `flyctl apps destroy*`, `flyctl machine destroy*` |
| `render` | `["deployments"]` | `["render", "service", "services", "deployment", "deployments"]` | `["zeig", "zeige", "list", "show", "check"]` | `Render: list services and deploys, read status.` | `render services*`, `render deploys list*`, `render whoami*` | `render services delete*` |
| `firebase` | `["deployments"]` | `["firebase", "hosting", "functions", "projekt", "projekte", "project", "projects"]` | `["zeig", "zeige", "list", "show", "check", "deploy"]` | `Firebase: list projects, apps, hosting sites; deploy.` | `firebase projects:list*`, `firebase apps:list*`, `firebase hosting:sites:list*` | `firebase hosting:disable*` |
| `pscale` | `["database"]` | `["planetscale", "pscale", "datenbank", "database", "branch", "branches"]` | `["zeig", "zeige", "list", "show", "check"]` | `PlanetScale: list databases, branches, deploy requests.` | `pscale database list*`, `pscale branch list*`, `pscale org list*` | `pscale database delete*`, `pscale branch delete*` |
| `neonctl` | `["database"]` | `["neon", "datenbank", "database", "branch", "branches", "projekt", "project", "projects"]` | `["zeig", "zeige", "list", "show", "check"]` | `Neon: list projects, branches, databases.` | `neonctl projects list*`, `neonctl branches list*`, `neonctl me*` | `neonctl projects delete*`, `neonctl branches delete*` |
| `glab` | `["repos"]` | `["gitlab", "merge request", "merge requests", "mr", "mrs", "issue", "issues", "repo", "repos", "pipeline", "pipelines"]` | `["zeig", "zeige", "lies", "list", "show", "check", "create", "erstell", "erstelle"]` | `GitLab: list/read merge requests, issues, pipelines.` | `glab mr list*`, `glab issue list*`, `glab pipeline list*`, `glab repo view*`, `glab auth status*` | `glab repo delete*` |
| `twilio` | `["messaging"]` | `["twilio", "sms", "nachricht", "nachrichten", "message", "messages", "anruf", "anrufe", "call", "calls"]` | `["zeig", "zeige", "list", "show", "check"]` | `Twilio: list messages, calls, phone numbers.` | `twilio api:core:messages:list*`, `twilio api:core:calls:list*`, `twilio phone-numbers:list*` | `twilio api:core:* :remove*` | <!-- i18n-allow: German input-vocabulary table row -->

(For entries that already have some of these patterns, merging means: keep existing entries, append the missing ones, no duplicates.)

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/clis/ -q`
Expected: PASS — including the new parity test and all pre-existing catalog/spec tests (the JSON must still validate through `CliSpecModel`).

- [ ] **Step 6: Commit**

```bash
git add jarvis/clis/catalog/seed_catalog.json tests/unit/clis/test_seed_catalog_capabilities.py
git commit -m "feat(clis): curate capabilities + read-only whitelists for seed catalog, add GAM entry (AD-CLI9)"
```

---

## Task 10: Full regression + lint

- [ ] **Step 1: Targeted suites**

Run: `pytest tests/unit/clis/ tests/unit/core/ tests/unit/brain/ tests/integration/test_cli_integration.py -q --timeout=300`
Expected: zero failures; pass count strictly greater than the Task-0 baseline.

- [ ] **Step 2: Router discipline + scrubber guards (binding regression gates)**

Run: `pytest tests/unit/brain/test_routing.py tests/unit/brain/test_output_filter.py -q`
Expected: PASS, unchanged counts.

- [ ] **Step 3: Lint + types**

Run: `ruff check jarvis/clis/ jarvis/brain/evidence_gate.py jarvis/core/capabilities.py jarvis/core/config.py && ruff format --check jarvis/clis/capability_provider.py jarvis/clis/prompt_section.py jarvis/brain/evidence_gate.py`
Expected: clean.

Run: `mypy jarvis/clis/capability_provider.py jarvis/clis/prompt_section.py jarvis/brain/evidence_gate.py`
Expected: no new errors.

- [ ] **Step 4: Final commit (leftovers only, if any)**

```bash
git status --short   # everything from Tasks 1-9 should already be committed
```

- [ ] **Step 5: Report**

Summarize: new modules, gate behaviour (three verdicts), what stays config-off-able (`[brain.evidence_domains] enabled=false`), and note the app needs a restart (pythonw bundle) for the live brain to pick the changes up.

---

## Out of scope (do NOT build in this run)

- Wave 5 inline-budget offload (slow CLI calls → completion announcement).
- PATH auto-scan for uncatalogued CLIs.
- Per-domain refusal telemetry.
- Any `pyproject.toml`/entry-point change (none is needed), any `ROUTER_TOOLS` change (forbidden without ADR-0011 amendment — and not needed: `cli-tools` is already a member).
