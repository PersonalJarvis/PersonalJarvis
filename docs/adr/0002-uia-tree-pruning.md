---
title: "ADR-0002: UIA-Tree-Pruning"
slug: adr-0002-uia-tree-pruning
diataxis: adr
status: active
owner: sam
last_reviewed: 2026-04-29
phase: 5
audience: developer
---

# ADR-0002 — UIAutomation tree-pruning strategy

**Status:** Accepted  (2026-04-22)
**Phase:** 5 — Vision Capability

## Context

The CU harness needs structured UI access so that vision hallucinations („klick auf OK bei (340,512)" / "click OK at (340,512)") do not become the default case — mandate §150. Naive UIA tree traversal (via `pywinauto.uia_element_info`) returns 3,000–8,000 nodes for Chrome/VSCode/Slack, each with 20+ properties. Serializing alone takes seconds, and sending a 5,000-node tree to Sonnet costs ~60k input tokens — per step.

## Decision

**Three-stage pruning before handing off to the LLM**, order of application:

1. **Depth limit 6** — from the root window. Deeper children are collapsed as `{"children": "…N nodes omitted"}`. Rationale: relevant controls rarely sit deeper than 6 levels.
2. **Interesting-role filter** — whitelist: `Button, Edit, ComboBox, List, ListItem, Tab, MenuItem, CheckBox, RadioButton, Hyperlink, Text` (only when `Name != ""`). Everything else (panes, groups, decorations, separators) is passed through as a container but not emitted itself, unless it carries a non-trivial `AutomationId`.
3. **OnScreen-rect filter** — an element is only included if its `BoundingRectangle` lies at least 50% within the primary monitor and is not completely covered by higher Z-order windows (heuristic: `IsOffscreen == False`).

Goal: **≤ 150 nodes** per observation. If exceeded: a second pruning round with depth 5, then depth 4. If still > 150 → `fallback_to_screenshot_only = True` in the observation event.

Serialized format: compact, one node = `{"r":"Button","n":"Speichern","id":"btnSave","b":[100,200,180,32],"p":<parent_idx>}`. No redundancy, no whitespace.

## Consequences

+ Token budget per observation ~3k–5k instead of 60k → the CU loop becomes economically viable.
+ Deterministic, no ML training needed.
+ The pruning stages are independently configurable in `jarvis.toml:[vision.pruning]`.
- False negatives: very deep, rarely used controls (e.g. 8 levels deep in a TreeView) are missed. Mitigation: if the LLM says „ich sehe das gesuchte Element nicht" ("I don't see the element I'm looking for"), the depth limit temporarily goes to 10 (one retry pass).
- Dependency on `pywinauto.uia_element_info.UIAElementInfo` — the alternative `uiautomation` package is NOT needed in `requirements`, but might be more robust for modern WinUI3 apps. Worth keeping an eye on.

## Alternatives Considered

- **Full tree to the LLM:** Cost/latency unacceptable, mandate §150 effectively forbids it.
- **Screenshot-only:** loses actionable IDs, vision hallucinates more. A single screenshot is a fallback, not the default.
- **ML-based pruning** (e.g. a small local model that classifies „interesting" nodes): overhead (training data, model loading, additional dep). YAGNI for Phase 5.
- **Screenpipe-style OCR+layout:** needs external infrastructure, no structured click handle. Rejected.

## Configurability

```toml
[vision.pruning]
max_depth = 6
max_nodes = 150
interesting_roles = ["Button", "Edit", "ComboBox", "List", "ListItem",
                     "Tab", "MenuItem", "CheckBox", "RadioButton",
                     "Hyperlink", "Text"]
fallback_to_screenshot_if_overflow = true
```
