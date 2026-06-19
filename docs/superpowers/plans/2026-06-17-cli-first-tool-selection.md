# CLI-First Tool Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make connected CLIs trigger implicitly from natural requests, generically for every connected CLI, with CLIs always preferred over equivalent plugins and the model self-discovering commands from `<cli> --help`.

**Architecture:** Reuse the existing evidence gate (the only deterministic CLI-forcing path). Derive its trigger vocabulary from connected CLIs' own catalog `objects` (config becomes an override/denylist layer); invert its plugin-vs-CLI preference; structurally hide plugin tools whose CLI is connected; add prompt guidance for `<cli> --help`.

**Tech Stack:** Python 3.11, pytest (`asyncio_mode=auto`), the `jarvis.clis` + `jarvis.brain` packages.

## Global Constraints

- **All artifacts English** — code, comments, docstrings, test names, directives, prompt strings. No German source strings (CI `language-policy` gate). The evidence directive and prompt additions are English.
- **Ruff clean on touched lines** — `ruff check` must not add new errors on edited lines (pre-existing baseline untouched).
- **No LLM / no network on the gate path** — pure regex + registry lookups (AP-9/AP-11). The new derivation/merge/suppress helpers are pure data transforms.
- **Every gate/tool-assembly helper degrades gracefully** — any fault returns the safe default (gate → PASS, derivation → `{}`, suppression → unchanged tools); never raises on the voice path.
- **Python interpreter for tests:** `"C:/Program Files/Python311/python.exe" -m pytest …` (the Hermes-venv `python` has no pytest).
- **Commits:** local commits only; never push (project rule — pushing is a separate, user-initiated step via the ship skill).

---

### Task 1: Derive trigger keywords from connected CLIs' objects (denylisted)

**Files:**
- Modify: `jarvis/clis/capability_provider.py` (add `_KEYWORD_DENYLIST`, `connected_domain_keyword_map`)
- Test: `tests/unit/clis/test_capability_provider.py`

**Interfaces:**
- Produces: `connected_domain_keyword_map(cli_registry) -> dict[str, list[str]]` — for every usable CLI, the union of its capability `objects` per declared `domain`, normalized, minus `_KEYWORD_DENYLIST`. `_KEYWORD_DENYLIST: frozenset[str]` of ambiguous bare cost/price nouns.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/clis/test_capability_provider.py`:

```python
from dataclasses import replace as _replace

from jarvis.clis.capability_provider import connected_domain_keyword_map
from jarvis.clis.spec import CliCapabilityDecl
from tests.unit.clis._fakes import FakeCliRegistry, FakeTool, make_spec


def test_keyword_map_unions_objects_per_domain():
    fake = FakeCliRegistry(
        {"gh": make_spec("gh", domains=("repos",))},  # objects ("pull request","issue")
        active=[FakeTool("cli_gh")],
    )
    out = connected_domain_keyword_map(fake)
    assert set(out["repos"]) == {"pull request", "issue"}


def test_keyword_map_empty_when_no_active_cli():
    fake = FakeCliRegistry({"gh": make_spec("gh")}, active=[])
    assert connected_domain_keyword_map(fake) == {}


def test_keyword_map_drops_ambiguous_cost_nouns():
    spec = _replace(
        make_spec("gcloud", domains=("cloud",)),
        capabilities=(
            CliCapabilityDecl(
                domains=("cloud",),
                verbs=("zeig",),
                objects=("kosten", "cost", "abrechnung", "guthaben"),
                description="cloud billing",
            ),
        ),
    )
    fake = FakeCliRegistry({"gcloud": spec}, active=[FakeTool("cli_gcloud")])
    out = connected_domain_keyword_map(fake)
    assert "abrechnung" in out["cloud"] and "guthaben" in out["cloud"]
    assert "kosten" not in out["cloud"] and "cost" not in out["cloud"]


def test_keyword_map_defensive_against_broken_registry():
    class _Broken:
        def active_tools(self):
            raise RuntimeError("boom")

    assert connected_domain_keyword_map(_Broken()) == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"C:/Program Files/Python311/python.exe" -m pytest tests/unit/clis/test_capability_provider.py -k keyword_map -v`
Expected: FAIL with `ImportError: cannot import name 'connected_domain_keyword_map'`.

- [ ] **Step 3: Implement the helper**

In `jarvis/clis/capability_provider.py`, add after `connected_domain_tool_map` (around line 106), and the imports (`from jarvis.core.capabilities import Capability, _normalize` — `_normalize` is in that module):

```python
# Ambiguous bare nouns that appear in CLI capability objects but would hijack
# unrelated questions ("was kostet ein Tesla?") if used as forcing keywords.
# Applied ONLY to derived objects — curated config keywords are never filtered.
_KEYWORD_DENYLIST: frozenset[str] = frozenset(
    {"kosten", "cost", "costs", "preis", "preise", "price", "geld", "money"}
)


def connected_domain_keyword_map(cli_registry: Any) -> dict[str, list[str]]:
    """Map evidence domain -> trigger keywords, derived from usable CLIs' objects.

    Unions each usable CLI capability's ``objects`` per declared ``domain`` so a
    connected CLI becomes implicitly triggerable from its own catalog vocabulary
    with no hand-maintained config. Ambiguous bare cost/price nouns are dropped
    (``_KEYWORD_DENYLIST``). Defensive: any fault returns ``{}`` (the gate then
    runs on the config keyword list exactly as before).
    """
    out: dict[str, list[str]] = {}
    try:
        active = {t.name for t in cli_registry.active_tools()}
        for spec in cli_registry.catalog().all().values():
            if f"{TOOL_NAME_PREFIX}{spec.name}" not in active:
                continue
            for decl in spec.capabilities:
                for domain in decl.domains:
                    bucket = out.setdefault(domain, [])
                    for obj in decl.objects:
                        kw = _normalize(obj)
                        if kw and kw not in _KEYWORD_DENYLIST and kw not in bucket:
                            bucket.append(kw)
    except Exception:  # noqa: BLE001 — derivation must never break the gate
        log.debug("cli domain keyword map failed", exc_info=True)
        return {}
    return out
```

Add `_normalize` to the existing `from jarvis.core.capabilities import Capability` import line.

- [ ] **Step 4: Run tests to verify they pass**

Run: `"C:/Program Files/Python311/python.exe" -m pytest tests/unit/clis/test_capability_provider.py -k keyword_map -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add jarvis/clis/capability_provider.py tests/unit/clis/test_capability_provider.py
git commit -m "feat(clis): derive evidence-gate keywords from connected CLI objects"
```

---

### Task 2: Merge derived keywords into the evidence gate (config as override layer)

**Files:**
- Modify: `jarvis/clis/capability_provider.py` (add `merged_evidence_domains`)
- Modify: `jarvis/brain/manager.py` (`_run_evidence_gate`, ~line 2112-2118 — use merged domains)
- Test: `tests/unit/clis/test_capability_provider.py`, `tests/unit/brain/test_evidence_gate.py`

**Interfaces:**
- Consumes: `connected_domain_keyword_map` (Task 1).
- Produces: `merged_evidence_domains(cli_registry, config_domains: Mapping[str, Sequence[str]]) -> dict[str, list[str]]` — per domain, the union of derived keywords and config keywords; config-only domains preserved; config keywords always included (curated override).

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/clis/test_capability_provider.py`:

```python
from jarvis.clis.capability_provider import merged_evidence_domains


def test_merge_adds_cli_domain_absent_from_config():
    # stripe declares payments with billing objects; config has no payments.
    spec = _replace(
        make_spec("stripe", domains=("payments",)),
        capabilities=(
            CliCapabilityDecl(
                domains=("payments",), verbs=("zeig",),
                objects=("stripe", "umsatz", "invoice"), description="payments",
            ),
        ),
    )
    fake = FakeCliRegistry({"stripe": spec}, active=[FakeTool("cli_stripe")])
    out = merged_evidence_domains(fake, {"calendar": ["kalender"]})
    assert "umsatz" in out["payments"] and "stripe" in out["payments"]
    # config-only domain preserved
    assert out["calendar"] == ["kalender"]


def test_merge_config_keywords_always_win():
    # gcloud objects include "kosten" (denylisted in derivation); a curated
    # config keyword for the same domain still survives.
    spec = _replace(
        make_spec("gcloud", domains=("cloud",)),
        capabilities=(
            CliCapabilityDecl(
                domains=("cloud",), verbs=("zeig",),
                objects=("kosten", "gcp"), description="cloud",
            ),
        ),
    )
    fake = FakeCliRegistry({"gcloud": spec}, active=[FakeTool("cli_gcloud")])
    out = merged_evidence_domains(fake, {"cloud": ["abrechnung"]})
    assert "abrechnung" in out["cloud"]  # config curated
    assert "gcp" in out["cloud"]         # derived
    assert "kosten" not in out["cloud"]  # denylisted in derivation


def test_merge_defensive_returns_config_on_fault():
    class _Broken:
        def active_tools(self):
            raise RuntimeError("boom")

    assert merged_evidence_domains(_Broken(), {"cloud": ["abrechnung"]}) == {
        "cloud": ["abrechnung"]
    }
```

Add to `tests/unit/brain/test_evidence_gate.py` (gate-level proof that derived vocab forces the CLI):

```python
def test_derived_payments_keyword_forces_cli_stripe():
    # Simulates the merged domains a connected stripe would produce. The
    # utterance carries a lookup-shape token ("zeig") + a payments keyword
    # ("stripe") so the gate matches the payments domain.
    domains = {"payments": ["stripe", "umsatz", "invoice"]}
    v = check_evidence_domain(
        "Zeig mir meinen aktuellen Stripe-Umsatz",
        enabled=True,
        domains=domains,
        capability_registry=CapabilityRegistry(),
        domain_tool_map={"payments": "cli_stripe"},
        refusal_hint_fn=None,
    )
    assert v.kind == "require_tool"
    assert v.tool_name == "cli_stripe"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"C:/Program Files/Python311/python.exe" -m pytest tests/unit/clis/test_capability_provider.py -k merge -v`
Expected: FAIL with `ImportError: cannot import name 'merged_evidence_domains'` (the RED for the new code).

Also run the gate contract pin: `"C:/Program Files/Python311/python.exe" -m pytest tests/unit/brain/test_evidence_gate.py -k derived_payments -v`
Expected: PASS — this test calls `check_evidence_domain` with an explicit domains dict (no new code), so it pins the end-to-end contract that derived payments vocab forces `cli_stripe`. It is a characterization pin, not a RED.

- [ ] **Step 3: Implement `merged_evidence_domains` and wire it in**

In `jarvis/clis/capability_provider.py`, add after `connected_domain_keyword_map`:

```python
def merged_evidence_domains(
    cli_registry: Any, config_domains: "Mapping[str, Sequence[str]]"
) -> dict[str, list[str]]:
    """Domain -> keywords, deriving from connected CLIs and overlaying config.

    Config keywords are always included (curated override); derived CLI-object
    keywords augment them. Config-only domains (no backing CLI) are preserved.
    Defensive: on any fault returns ``dict(config_domains)`` unchanged.
    """
    try:
        derived = connected_domain_keyword_map(cli_registry)
        out: dict[str, list[str]] = {d: list(kws) for d, kws in derived.items()}
        for domain, kws in config_domains.items():
            bucket = out.setdefault(domain, [])
            for kw in kws:
                if kw not in bucket:
                    bucket.append(kw)
        return out
    except Exception:  # noqa: BLE001
        log.debug("merged evidence domains failed", exc_info=True)
        return {d: list(kws) for d, kws in config_domains.items()}
```

Add `from collections.abc import Mapping, Sequence` to the imports.

In `jarvis/brain/manager.py` `_run_evidence_gate`, change the import and the `domains=` argument. Current (around 2095-2118):

```python
            from jarvis.clis.capability_provider import (
                connected_domain_tool_map,
                refusal_hint,
            )
            ...
            return check_evidence_domain(
                user_text,
                enabled=cfg.enabled,
                domains=cfg.domains,
                capability_registry=get_registry(),
                domain_tool_map=domain_map,
                refusal_hint_fn=_hint,
            )
```

Replace with:

```python
            from jarvis.clis.capability_provider import (
                connected_domain_tool_map,
                merged_evidence_domains,
                refusal_hint,
            )
            ...
            return check_evidence_domain(
                user_text,
                enabled=cfg.enabled,
                domains=merged_evidence_domains(cli_reg, cfg.domains)
                if cli_reg is not None
                else cfg.domains,
                capability_registry=get_registry(),
                domain_tool_map=domain_map,
                refusal_hint_fn=_hint,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `"C:/Program Files/Python311/python.exe" -m pytest tests/unit/clis/test_capability_provider.py tests/unit/brain/test_evidence_gate.py tests/unit/brain/test_evidence_gate_wiring.py -v`
Expected: PASS (all, including the existing gate + wiring suites).

- [ ] **Step 5: Commit**

```bash
git add jarvis/clis/capability_provider.py jarvis/brain/manager.py tests/unit/clis/test_capability_provider.py tests/unit/brain/test_evidence_gate.py
git commit -m "feat(brain): merge derived CLI keywords into the evidence gate"
```

---

### Task 3: Invert the gate preference — CLI over plugin (supersedes AD-CLI6)

**Files:**
- Modify: `jarvis/brain/evidence_gate.py` (the AD-CLI6 block, ~lines 118-146)
- Test: `tests/unit/brain/test_evidence_gate.py`

**Interfaces:** No new signature. Behavior change: a connected CLI for the matched domain now wins over a non-CLI capability.

- [ ] **Step 1: Replace the failing test + add the fallback test**

In `tests/unit/brain/test_evidence_gate.py`, REPLACE `test_non_cli_capability_wins_and_passes` (the AD-CLI6 test) with:

```python
def test_cli_capability_wins_over_plugin():
    # CLI-first (req 4): a connected CLI for the domain is forced even when a
    # plugin/skill also covers it. Inverts the old AD-CLI6 plugin preference.
    reg = CapabilityRegistry()
    reg.register(Capability(
        id="skill.paired.gmail", source="skill",
        verbs=("lies",), objects=("mail", "inbox", "postfach"),
        description="Paired Gmail skill.", risk_tier="ask",
        requires_evidence=True,
    ))
    v = _gate("Hab ich neue Mails?", registry=reg, tool_map={"email": "cli_gam"})
    assert v.kind == "require_tool"
    assert v.tool_name == "cli_gam"


def test_plugin_is_fallback_when_no_cli_covers_domain():
    # No CLI for the domain (empty tool_map) -> the non-CLI capability owns the
    # turn and the gate PASSes (plugin/skill handles it).
    reg = CapabilityRegistry()
    reg.register(Capability(
        id="skill.paired.gmail", source="skill",
        verbs=("lies",), objects=("mail", "inbox", "postfach"),
        description="Paired Gmail skill.", risk_tier="ask",
        requires_evidence=True,
    ))
    v = _gate("Hab ich neue Mails?", registry=reg, tool_map={})
    assert v.kind == "pass"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"C:/Program Files/Python311/python.exe" -m pytest tests/unit/brain/test_evidence_gate.py -k "cli_capability_wins or plugin_is_fallback" -v`
Expected: `test_cli_capability_wins_over_plugin` FAILs (`assert 'pass' == 'require_tool'` — current code lets the skill win); `test_plugin_is_fallback_when_no_cli_covers_domain` PASSes.

- [ ] **Step 3: Invert the block**

In `jarvis/brain/evidence_gate.py`, REPLACE the AD-CLI6 block (the comment "AD-CLI6 preference …" through the `tool_name = domain_tool_map.get(...)` + `if tool_name:` directive return) so the CLI is checked FIRST. Current:

```python
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
```

Replace with:

```python
    # CLI-first preference (req 4, supersedes AD-CLI6): a connected CLI for the
    # domain ALWAYS wins over a plugin/skill — a CLI runs a local subprocess and
    # is cheaper than a plugin's MCP/HTTP/API round-trip. Plugins are fallback
    # only, so we mandate the CLI before considering any non-CLI capability.
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

    # No CLI covers the domain: a non-CLI capability (paired skill / MCP plugin)
    # owns the turn — let the existing machinery handle it (the fallback).
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `"C:/Program Files/Python311/python.exe" -m pytest tests/unit/brain/test_evidence_gate.py -v`
Expected: PASS (all, including the cloud/gcloud cases, the hard negative, the two new CLI-preference tests).

- [ ] **Step 5: Commit**

```bash
git add jarvis/brain/evidence_gate.py tests/unit/brain/test_evidence_gate.py
git commit -m "feat(brain): evidence gate prefers CLI over plugin (invert AD-CLI6)"
```

---

### Task 4: Plugin↔CLI overlap map + suppression helper

**Files:**
- Modify: `jarvis/clis/capability_provider.py` (add `PLUGIN_CLI_OVERLAP`, `suppress_plugin_tools_covered_by_cli`)
- Test: `tests/unit/clis/test_capability_provider.py`

**Interfaces:**
- Produces:
  - `PLUGIN_CLI_OVERLAP: dict[str, str]` — plugin/native-tool id → CLI name that supersedes it.
  - `suppress_plugin_tools_covered_by_cli(tools: dict[str, Any]) -> dict[str, Any]` — drops plugin tools (namespaced `<id>/*` and the exact native `<id>` tool) whose CLI (`cli_<value>`) is present in `tools`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/clis/test_capability_provider.py`:

```python
from jarvis.clis.capability_provider import (
    PLUGIN_CLI_OVERLAP,
    suppress_plugin_tools_covered_by_cli,
)


def test_suppress_drops_namespaced_plugin_when_cli_present():
    tools = {
        "cli_gh": FakeTool("cli_gh"),
        "github/list_prs": FakeTool("github/list_prs"),
        "github/create_issue": FakeTool("github/create_issue"),
        "search_web": FakeTool("search_web"),
    }
    out = suppress_plugin_tools_covered_by_cli(tools)
    assert "cli_gh" in out and "search_web" in out
    assert "github/list_prs" not in out and "github/create_issue" not in out


def test_suppress_drops_native_tool_when_cli_present():
    tools = {"cli_vercel": FakeTool("cli_vercel"), "vercel": FakeTool("vercel")}
    out = suppress_plugin_tools_covered_by_cli(tools)
    assert "cli_vercel" in out and "vercel" not in out


def test_suppress_keeps_plugin_when_cli_absent():
    tools = {"github/list_prs": FakeTool("github/list_prs")}
    out = suppress_plugin_tools_covered_by_cli(tools)
    assert "github/list_prs" in out  # no cli_gh -> plugin stays as fallback


def test_suppress_defensive_on_bad_input():
    assert suppress_plugin_tools_covered_by_cli(None) is None  # type: ignore[arg-type]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"C:/Program Files/Python311/python.exe" -m pytest tests/unit/clis/test_capability_provider.py -k suppress -v`
Expected: FAIL with `ImportError: cannot import name 'PLUGIN_CLI_OVERLAP'`.

- [ ] **Step 3: Implement the map + helper**

In `jarvis/clis/capability_provider.py`, add:

```python
# A marketplace plugin (or native REST tool) and a CLI for the SAME service.
# When the CLI is connected, its plugin counterpart is hidden so the CLI is the
# only choice (req 4: CLI > plugin; plugin is a fallback only). Key = plugin id
# / native tool name; value = CLI name. Guarded against drift by a parity test.
PLUGIN_CLI_OVERLAP: dict[str, str] = {
    "github": "gh",
    "vercel": "vercel",
    "supabase": "supabase",
    "stripe": "stripe",
    "gmail": "gam",
}


def suppress_plugin_tools_covered_by_cli(tools: dict[str, Any]) -> dict[str, Any]:
    """Drop plugin/native tools whose CLI counterpart is connected this turn.

    For each overlap entry whose ``cli_<name>`` is present in ``tools``, removes
    the namespaced ``<plugin_id>/*`` tools and the exact native ``<plugin_id>``
    tool. Defensive: returns ``tools`` unchanged on any fault.
    """
    try:
        present_clis = {n for n in tools if n.startswith(TOOL_NAME_PREFIX)}
        drop: set[str] = set()
        for plugin_id, cli_name in PLUGIN_CLI_OVERLAP.items():
            if f"{TOOL_NAME_PREFIX}{cli_name}" not in present_clis:
                continue
            prefix = f"{plugin_id}/"
            for name in tools:
                if name == plugin_id or name.startswith(prefix):
                    drop.add(name)
        if not drop:
            return tools
        return {n: t for n, t in tools.items() if n not in drop}
    except Exception:  # noqa: BLE001 — suppression must never blind the brain
        log.debug("plugin suppression failed; using full tool set", exc_info=True)
        return tools
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `"C:/Program Files/Python311/python.exe" -m pytest tests/unit/clis/test_capability_provider.py -k suppress -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add jarvis/clis/capability_provider.py tests/unit/clis/test_capability_provider.py
git commit -m "feat(clis): plugin-CLI overlap map + suppression helper"
```

---

### Task 5: Anti-drift parity test for the overlap map

**Files:**
- Test: `tests/unit/clis/test_plugin_cli_overlap_parity.py` (create)

**Interfaces:** Consumes `PLUGIN_CLI_OVERLAP` (Task 4), the marketplace catalog, and the CLI catalog.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/clis/test_plugin_cli_overlap_parity.py`:

```python
"""Anti-drift guard: every PLUGIN_CLI_OVERLAP entry maps a real plugin id to a
real CLI name. Prevents the map rotting when a catalog entry is renamed
(multi-layer drift class — docs/anti-drift-three-layer.md)."""
import json
from pathlib import Path

from jarvis.clis.capability_provider import PLUGIN_CLI_OVERLAP

_ROOT = Path(__file__).resolve().parents[3]


def _plugin_ids() -> set[str]:
    data = json.loads(
        (_ROOT / "jarvis/marketplace/seed_catalog.json").read_text(encoding="utf-8")
    )
    return {p["id"] for p in data.get("plugins", [])}


def _cli_names() -> set[str]:
    data = json.loads(
        (_ROOT / "jarvis/clis/catalog/seed_catalog.json").read_text(encoding="utf-8")
    )
    items = data if isinstance(data, list) else data.get("clis", data.get("entries", []))
    return {c["name"] for c in items if isinstance(c, dict)}


def test_overlap_keys_are_real_plugin_ids():
    unknown = set(PLUGIN_CLI_OVERLAP) - _plugin_ids()
    assert not unknown, f"PLUGIN_CLI_OVERLAP keys not in plugin catalog: {unknown}"


def test_overlap_values_are_real_cli_names():
    unknown = set(PLUGIN_CLI_OVERLAP.values()) - _cli_names()
    assert not unknown, f"PLUGIN_CLI_OVERLAP values not in CLI catalog: {unknown}"
```

- [ ] **Step 2: Run the test to verify it passes (guard already satisfied)**

Run: `"C:/Program Files/Python311/python.exe" -m pytest tests/unit/clis/test_plugin_cli_overlap_parity.py -v`
Expected: PASS — this is a guard; it should already be green because Task 4's map uses real ids/names. If it FAILs, a map entry is wrong — fix the map in `capability_provider.py`. (Note: this guard's value is catching FUTURE drift, so it ships green.)

- [ ] **Step 3: (no implementation — guard only)**

If Step 2 failed, correct `PLUGIN_CLI_OVERLAP` so all keys/values exist in the catalogs, then re-run.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/clis/test_plugin_cli_overlap_parity.py
git commit -m "test(clis): parity guard for plugin-CLI overlap map"
```

---

### Task 6: Wire suppression into the per-turn tool assembly

**Files:**
- Modify: `jarvis/brain/manager.py` (add `_suppress_plugins_covered_by_cli` method; apply it at `_turn_tools`, ~line 3985-3992)
- Test: `tests/unit/brain/test_plugin_cli_suppression_wiring.py` (create)

**Interfaces:** Consumes `suppress_plugin_tools_covered_by_cli` (Task 4). Produces `BrainManager._suppress_plugins_covered_by_cli(self, tools: dict) -> dict` (defensive wrapper, mirrors `_apply_plugin_relevance`).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/brain/test_plugin_cli_suppression_wiring.py`:

```python
"""BrainManager hides plugin tools whose CLI is connected (req 4 fallback)."""
from types import SimpleNamespace

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig


def _mgr() -> BrainManager:
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"
    return BrainManager(config=cfg, bus=EventBus(), tools={}, tool_executor=None)


def test_method_drops_plugin_when_cli_present():
    mgr = _mgr()
    tools = {
        "cli_gh": SimpleNamespace(name="cli_gh"),
        "github/list_prs": SimpleNamespace(name="github/list_prs"),
        "search_web": SimpleNamespace(name="search_web"),
    }
    out = mgr._suppress_plugins_covered_by_cli(tools)
    assert set(out) == {"cli_gh", "search_web"}


def test_method_keeps_plugin_when_cli_absent():
    mgr = _mgr()
    tools = {"github/list_prs": SimpleNamespace(name="github/list_prs")}
    assert mgr._suppress_plugins_covered_by_cli(tools) == tools
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `"C:/Program Files/Python311/python.exe" -m pytest tests/unit/brain/test_plugin_cli_suppression_wiring.py -v`
Expected: FAIL with `AttributeError: 'BrainManager' object has no attribute '_suppress_plugins_covered_by_cli'`.

- [ ] **Step 3: Add the method and apply it**

In `jarvis/brain/manager.py`, add a method next to `_apply_plugin_relevance` (after its definition, ~line 2510):

```python
    def _suppress_plugins_covered_by_cli(
        self, tools: dict[str, "Tool"]
    ) -> dict[str, "Tool"]:
        """Hide plugin/native tools whose CLI counterpart is connected (req 4).

        A CLI runs a local subprocess and is cheaper than a plugin's MCP/API
        hop, so when a CLI for a service is active its plugin is removed from the
        turn's tool surface (fallback only). Defensive: returns the tools
        unchanged on any fault (never blind the brain on the voice path).
        """
        try:
            from jarvis.clis.capability_provider import (
                suppress_plugin_tools_covered_by_cli,
            )

            return suppress_plugin_tools_covered_by_cli(tools)
        except Exception:  # noqa: BLE001
            log.debug("plugin-CLI suppression failed; full tool set", exc_info=True)
            return tools
```

Then change the `_turn_tools` assignment (~line 3985-3992) from:

```python
            _turn_tools = (
                self._smalltalk_tool_override() if is_smalltalk_turn
                # Non-smalltalk turn: pass the full set minus plugin tools
                # that are irrelevant to this utterance (progressive
                # disclosure — keeps the surface small once 3+ plugins are
                # connected). None would mean "use self._tools verbatim".
                else self._apply_plugin_relevance(user_text, self._tools)
            )
```

to:

```python
            _turn_tools = (
                self._smalltalk_tool_override() if is_smalltalk_turn
                # Non-smalltalk turn: drop plugin tools irrelevant to this
                # utterance (progressive disclosure), then hide any plugin whose
                # CLI counterpart is connected (req 4: CLI > plugin fallback).
                else self._suppress_plugins_covered_by_cli(
                    self._apply_plugin_relevance(user_text, self._tools)
                )
            )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `"C:/Program Files/Python311/python.exe" -m pytest tests/unit/brain/test_plugin_cli_suppression_wiring.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add jarvis/brain/manager.py tests/unit/brain/test_plugin_cli_suppression_wiring.py
git commit -m "feat(brain): hide plugin tools when their CLI is connected"
```

---

### Task 7: Prompt — CLI-first wording + `<cli> --help` self-discovery

**Files:**
- Modify: `jarvis/clis/prompt_section.py` (`_HEADER`, `_FOOTER`)
- Test: `tests/unit/clis/test_prompt_section.py` (create if absent; else extend)

**Interfaces:** No new signature. The rendered section gains CLI-first + `--help` instructions.

- [ ] **Step 1: Write the failing test**

Create/extend `tests/unit/clis/test_prompt_section.py`:

```python
from jarvis.clis.prompt_section import render_connected_clis_section
from tests.unit.clis._fakes import FakeCliRegistry, FakeTool, make_spec


def _reg():
    return FakeCliRegistry({"gh": make_spec("gh")}, active=[FakeTool("cli_gh")])


def test_section_prefers_cli_over_plugin():
    out = render_connected_clis_section(_reg())
    assert "plugin" in out.lower()  # explicit CLI-over-plugin wording


def test_section_tells_model_to_self_discover_with_help():
    out = render_connected_clis_section(_reg())
    assert "--help" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"C:/Program Files/Python311/python.exe" -m pytest tests/unit/clis/test_prompt_section.py -v`
Expected: FAIL — current `_HEADER`/`_FOOTER` contain neither "plugin" nor "--help".

- [ ] **Step 3: Extend the header and footer**

In `jarvis/clis/prompt_section.py`, replace `_HEADER` and `_FOOTER`:

```python
_HEADER = (
    "CONNECTED CLIS\n"
    "You have direct command-line tools for these connected services. Prefer "
    "them for matching requests instead of refusing, spawning a worker, or "
    "using an equivalent plugin — these CLIs are faster and cheaper, and a "
    "plugin is only a fallback when no CLI covers the task:\n"
)
_FOOTER = (
    "\nAnswer ONLY from the tool result — never invent external data. Prefer "
    "machine-readable output flags (--json, --format json) when the CLI "
    "supports them. If you are unsure of the exact command or flags, first run "
    "`<cli> --help` or `<cli> <group> --help` (read-only) to discover them, "
    "then issue the real command."
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `"C:/Program Files/Python311/python.exe" -m pytest tests/unit/clis/test_prompt_section.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add jarvis/clis/prompt_section.py tests/unit/clis/test_prompt_section.py
git commit -m "feat(clis): CLI-first prompt + <cli> --help self-discovery"
```

---

### Task 8: Full-suite regression + ruff

**Files:** none (verification only)

- [ ] **Step 1: Run the affected suites**

Run: `"C:/Program Files/Python311/python.exe" -m pytest tests/unit/clis/ tests/unit/brain/test_evidence_gate.py tests/unit/brain/test_evidence_gate_wiring.py tests/unit/core/test_evidence_domains_config.py -v`
Expected: PASS (all). Note any failure in unrelated subsystems as a foreign/shared-tree baseline (e.g. wake-word/codex/persona) and confirm it is not in the touched files.

- [ ] **Step 2: Ruff on touched files**

Run: `"C:/Program Files/Python311/python.exe" -m ruff check jarvis/clis/capability_provider.py jarvis/brain/evidence_gate.py jarvis/brain/manager.py jarvis/clis/prompt_section.py`
Expected: no NEW errors on edited lines (compare against the pre-existing baseline; fix any error introduced by this work).

- [ ] **Step 3: Final commit (if ruff fixes were needed)**

```bash
git add -p
git commit -m "chore(clis): ruff cleanup for CLI-first tool selection"
```

---

## Self-Review

**Spec coverage:**
- Req 1 (implicit) + Req 2 (generic) → Tasks 1+2 (derive keywords from connected CLIs' objects, merge into the gate). ✓
- Req 4 core (CLI > plugin) → Task 3 (invert AD-CLI6). ✓
- Req 4 hard (plugin fallback) → Tasks 4+5+6 (overlap map, parity guard, suppression wiring). ✓
- Req 3 (self-documentation) → Task 7 (`<cli> --help` prompt guidance). ✓
- Spec's denylist, degradation, anti-drift parity, English-artifact constraint → covered in Tasks 1, 4, 5 and Global Constraints. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code; commands have expected output. ✓

**Type consistency:** `connected_domain_keyword_map`, `merged_evidence_domains`, `PLUGIN_CLI_OVERLAP`, `suppress_plugin_tools_covered_by_cli`, `_suppress_plugins_covered_by_cli` are named identically across the tasks that define and consume them. `TOOL_NAME_PREFIX` and `_normalize` are existing symbols in the touched modules. ✓
