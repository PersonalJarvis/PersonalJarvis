# Capability Coupling — Implementation Spec

**Status:** draft — 2026-05-20
**Goal:** Jarvis must only confirm what it can actually do. Unknown tasks → deterministic "I cannot (yet) do that." Solution must be extensible: registering a new capability (tool, MCP, harness) must extend the truthful surface with zero touches to brain / gate / filter logic.

**Doctrine reference:** PHILOSOPHY.md graceful-no-op rule; AD-4 (Critic validates Risk-Tier before Jarvis-Agents); new ADR-0017 to be authored by Agent E.

---

## Architecture (3 coupled layers)

### Layer 1 — Capability Registry (Single Source of Truth)

New module: `jarvis/core/capabilities.py`.

```python
@dataclass(frozen=True)
class Capability:
    id: str                    # stable identifier, e.g. "tool.run-shell", "mcp.gmail/send_mail"
    source: Literal["router_tool", "mcp", "harness", "local_action", "skill"]
    verbs: tuple[str, ...]     # DE+EN action verbs that trigger this capability
    objects: tuple[str, ...]   # nouns/domains (e.g. "email", "calendar", "file", "shell")
    description: str           # English, 1 line, shown to the brain
    risk_tier: Literal["safe", "monitor", "ask", "block"]
    requires_evidence: bool    # True when Critic must see a tool-call to ratify success

class CapabilityRegistry:
    def register(self, cap: Capability) -> None: ...
    def all(self) -> tuple[Capability, ...]: ...
    def resolve_intent(self, utterance: str) -> Optional[Capability]:
        """Deterministic verb+object match. Normalises umlauts.
        Returns None when no capability covers the intent."""
    def has_action_intent(self, utterance: str) -> bool:
        """Heuristic: does the utterance look like a request for action
        (verb present) vs. smalltalk/Q&A. Mirrors _should_force_openclaw verbs."""
    def render_for_prompt(self, lang: Literal["de","en"]="de") -> str:
        """Bullet list rendered into the system prompt. Replaces the
        hardcoded 'NUTZE: search_web' block."""
```

**Seeding sources** (at boot, in `bootstrap`):
1. `ROUTER_TOOLS` frozenset → one `Capability` per tool, verbs+objects from a static map in `jarvis/core/capabilities_seed.py`.
2. `MCPRegistry` (`jarvis/mcp/registry.py`) → on `register_mcp_tools_in_registry`, also call `capabilities.register(...)` for each namespaced MCP tool. Verbs derived from the MCP tool's schema description (best-effort) plus user-overridable `[capabilities.mcp.<server>.<tool>]` block in `jarvis.toml`.
3. Harness adapters (`openclaw`, `mcp-remote`, `python-script`, etc.) → register their action surface.
4. Local-action-gate patterns → register `open_app`, `type_text`, `hotkey`, `reset_orb_position`, `terminal_count`.

**No tool/MCP/harness may be invokable through the voice path unless it has a registered Capability.** Registration is the contract.

---

### Layer 2 — Pre-Generation Capability Gate (Brain)

Goal: refuse to even call the LLM for action-intents that don't map to a capability.

Two insertion points (regex-only, AP-11 compliant):

**(a) `jarvis/brain/local_action_gate.py` line ~108** — after `_normalize()`, before pattern checks:
```python
if registry.has_action_intent(normalized) and registry.resolve_intent(normalized) is None:
    return LocalActionPlan(
        mode=LocalActionMode.UNSUPPORTED,
        response_text=_unsupported_response(normalized, lang),
    )
```
New mode `UNSUPPORTED` is added to `LocalActionMode`; manager.py routes it directly to TTS, skipping brain dispatch.

**(b) Mirror gate in `jarvis/brain/manager.py`** — `_should_force_openclaw` already runs verb-classification. Add a sibling `_capability_resolves(text) -> bool` check. If `has_action_intent AND not _capability_resolves AND not _is_smalltalk`:
- Skip both brain and Jarvis-Agents spawn.
- Emit `_unsupported_response(text)` via the same TTS path as a normal short reply.

Response phrasing (deterministic, no LLM):
- DE: *"Das kann ich noch nicht. Mir fehlt dafür ein Werkzeug — wenn du mir verrätst welches MCP oder welche Integration zuständig wäre, kann ich's lernen."* ("I can't do that yet. I'm missing a tool for it — if you tell me which MCP or integration should be responsible, I can learn it.")  <!-- i18n-allow: product voice output DE -->
- EN: *"I can't do that yet. I don't have a registered tool for it. Tell me which MCP or integration should handle it and I can learn."*

---

### Layer 3 — Capability-Aware Prompts + Critic Honesty

**3a. System prompt (`manager.py:770-786`)** — replace the hardcoded `NUTZE: search_web / cli_* / dispatch_to_harness` block with `registry.render_for_prompt(lang)`. Append a hard rule:
> You must never claim to perform an action that is not listed above. If the user asks for one, respond with: "Das kann ich noch nicht — mir fehlt das passende Werkzeug." ("I can't do that yet — I'm missing the right tool.") Do not invent tools. <!-- i18n-allow: product voice output DE -->

**3b. Ack-Brain persona (`jarvis/brain/ack_brain/persona_prompt.py`)** — extend the forbidden vocabulary list with action-promise patterns:
- "mache ich" ("I'll do it"), "wird erledigt" ("will be taken care of"), "ist gesendet" ("is sent"), "ist eingetragen" ("is entered"), "kümmere mich" ("I'll handle it")  <!-- i18n-allow: product speech output patterns -->
- "I'll do that", "will be sent", "will be scheduled", "consider it done"

The Ack-Brain is allowed only: (a) acoustic acknowledgment ("mhm", "verstanden" / "understood"), (b) context-restating questions ("welche Adresse?" / "which address?"), (c) silence on uncertainty.

**3c. Critic capability-honesty gate (`jarvis/missions/critic/`)** — currently the Critic ratifies empty diffs for non-file tasks. Fix:
- The Worker output is parsed for tool-call evidence (`tool_calls` array or equivalent harness signal).
- If the mission's resolved capability has `requires_evidence=True` and no tool-call evidence is present → `CriticVerdict.success=False`, `reason="capability_not_executed"`.
- `summary_de` must be derived from the **tool-call evidence**, not the worker's text claim. New helper `summarise_from_tool_calls(calls) -> str` in `jarvis/missions/critic/summary.py`.
- For Jarvis-Agents missions that lack tool-call telemetry (current Wave 2 mock state), Critic must default to `success=False` for `requires_evidence=True` capabilities until Wave 3 lands proper tool-call streaming.

---

## Extensibility contract (the constraint)

Adding a new capability later (e.g. an MCP server for Gmail):

1. Server registers via `mcp.json` or new `jarvis.mcp` entry-point group.
2. `MCPRegistry.load` calls `CapabilityRegistry.register(Capability(...))` for each namespaced tool — automatic; the MCP adapter does this for the developer.
3. Optional: user overrides verbs/objects in `[capabilities.mcp.gmail.send_mail]` block.
4. **No edits required** in `brain/manager.py`, `brain/local_action_gate.py`, `brain/output_filter.py`, `missions/critic/*`. The capability is now part of `registry.all()` and the system prompt renders it dynamically.

---

## File ownership (5-agent partition)

| Agent | Owns | Touches |
|---|---|---|
| A — Registry foundation | `jarvis/core/capabilities.py` (NEW), `jarvis/core/capabilities_seed.py` (NEW), `jarvis/mcp/adapter.py` (register-on-wrap) | unit tests under `tests/unit/core/test_capabilities.py` |
| B — Pre-Gen Gate (local-action) | `jarvis/brain/local_action_gate.py` (extend), new mode `UNSUPPORTED` in same file | `tests/unit/brain/test_local_action_gate.py` (extend) |
| C — Prompt + Ack-Brain | `jarvis/brain/manager.py` (system-prompt builder + sibling `_capability_resolves`), `jarvis/brain/ack_brain/persona_prompt.py` | `tests/unit/brain/test_routing.py` extension |
| D — Critic honesty | `jarvis/missions/critic/runner.py`, `jarvis/missions/critic/prompts.py`, new `jarvis/missions/critic/summary.py`, `jarvis/missions/voice/readback.py` (truthfulness pass) | `tests/missions/critic/test_runner_dryrun.py` extension |
| E — ADR + integration tests + MCP docs | `docs/adr/0017-capability-coupling.md` (NEW), `docs/anti-drift-three-layer.md` (cross-ref), `tests/integration/test_capability_coupling_e2e.py` (NEW) — covers mail/calendar hard-negatives | reads from A-D as ground truth |

**Coordination rule:** Agent A ships the Registry public API first (writes file, commits unit tests). B/C/D/E then code against that public surface. If A's API needs to change mid-flight, post a note in `docs/plans/capability-coupling/CHANGELOG.md`.

---

## Hard negatives (acceptance criteria for Agent E)

Each of these utterances must trigger the UNSUPPORTED path AND no fake confirmation reaches TTS:
1. "Schick eine Email an sam@example.com mit dem Betreff Hallo" ("Send an email to sam@example.com with subject Hello") → "Das kann ich noch nicht. (...)" ("I can't do that yet.") <!-- i18n-allow: example user voice query with English inline -->
2. "Trag einen Termin morgen 10 Uhr ein" ("Add an appointment tomorrow at 10 AM") → unsupported
3. "Sende eine WhatsApp an Mama" ("Send a WhatsApp to Mum") → unsupported
4. "Bestelle eine Pizza" ("Order a pizza") → unsupported
5. "Poste auf X dass ich heute frei habe" ("Post on X that I'm off today") → unsupported (until Buffer-MCP registered)

Each of these utterances must STILL work (no false negatives):
6. "Öffne Chrome" ("Open Chrome") → local-action open_app
7. "Lies die Datei foo.txt" ("Read the file foo.txt") → Jarvis-Agents spawn (file ops registered)
8. "Wie spät ist es?" ("What time is it?") → smalltalk, brain answers directly
9. "Such im Web nach Python 3.13" ("Search the web for Python 3.13") → only works if a web-search capability is actually registered; otherwise unsupported (this catches the search_web prompt-claim drift)

---

## Out of scope (for this wave)

- Auto-learning verb/object lists (LLM-based capability description parsing) — manual seed map first.
- Live Jarvis-Agents subprocess tool-call telemetry (Wave 3) — Critic will conservative-fail until that lands.
- Replacing every phantom-confirmation site in `pipeline.py:1278/1283/1384/2048` — Agent C only fixes the source of the lies (Ack-Brain + system prompt). The pipeline-level "Fertig./Das hat nicht geklappt." ("Done." / "That didn't work.") remains as it reflects the Critic's verdict, which is now honest.
