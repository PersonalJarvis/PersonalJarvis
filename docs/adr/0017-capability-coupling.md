# ADR-0017 — Capability Coupling: Single Source of Truth for Executable Surface

**Status:** Accepted · **Date:** 2026-05-20 · **Phase:** Capability Coupling

## Context

Personal Jarvis can — and routinely does — confirm actions it is not capable of
performing. Classic example from production: a user says "Schick eine Email an
Sam" and the Ack-Brain replies "wird erledigt" while no email capability
exists anywhere in the running process. The TTS happily reads the phantom
success; the mail is never sent; the user is deceived.

This is not a hallucination of the LLM in isolation — it is a *structural
coupling failure*. The brain layer and the critic layer both run against a model
of the world that is independent of the actual executable surface. There is no
authoritative list of what the running Jarvis instance can do, so nothing can
enforce the gap between "what the LLM says it will do" and "what a registered
tool can actually execute".

Three existing architectural decisions make the problem worse:

**AD-9 (jarvis-agents-bridge.md):** The Critic currently ratifies empty diffs for
non-file tasks. A Jarvis-Agent mission can complete with no file changes, no
tool-call evidence, and a positive `success=True` from the Critic if the worker
text *says* the task is done. For `requires_evidence=True` capabilities this is
a silent lie.

**PHILOSOPHY.md graceful-no-op rule:** The doctrine requires every component to
degrade gracefully when a capability is absent, rather than silently pretending
the capability exists.

**search_web prompt-claim drift (manager.py:774):** The system prompt contains a
hardcoded `NUTZE: search_web` block that promises the LLM it can search the web.
If no `web-search` capability is registered, the brain will still attempt to
call a tool that does not exist and may report success — or the LLM will invent
a result. This is exactly the hallucination class this ADR addresses.

The pre-existing anti-drift pattern (`docs/anti-drift-three-layer.md`) already
solves *string-enum drift* by treating a vocabulary as a versioned contract.
This ADR applies the same philosophy one layer higher: the *capability
vocabulary* — what Jarvis can do — must be a versioned, registered contract, and
every layer that references that vocabulary (brain, gate, critic) must read from
the same source of truth.

## Decision

### 1. Single Source of Truth: `CapabilityRegistry`

A new module `jarvis/core/capabilities.py` defines `Capability` (frozen
dataclass) and `CapabilityRegistry` (runtime singleton). Every tool, MCP
server, harness adapter, and local-action pattern that is reachable via the
voice path **must** be registered in the `CapabilityRegistry` at boot. No
capability registration means no voice-path invokability.

The `Capability` schema:

```python
@dataclass(frozen=True)
class Capability:
    id: str                          # stable, e.g. "tool.run-shell"
    source: Literal["router_tool", "mcp", "harness", "local_action", "skill"]
    verbs: tuple[str, ...]           # DE+EN action verbs
    objects: tuple[str, ...]         # nouns / domains
    description: str                 # English, one line, shown to the brain
    risk_tier: Literal["safe", "monitor", "ask", "block"]
    requires_evidence: bool          # True → Critic must see tool-call evidence
```

Seeding at boot (`bootstrap`):
1. `ROUTER_TOOLS` frozenset → one `Capability` per tool, verbs and objects from
   a static seed map in `jarvis/core/capabilities_seed.py`.
2. `MCPRegistry.load` → `CapabilityRegistry.register(...)` for every namespaced
   MCP tool, verbs derived best-effort from schema descriptions plus optional
   user overrides in `[capabilities.mcp.<server>.<tool>]` in `jarvis.toml`.
3. Harness adapters (`openclaw`, `mcp-remote`, `python-script`) → register their
   action surface.
4. Local-action-gate patterns → register `open_app`, `type_text`, `hotkey`,
   `reset_orb_position`, `terminal_count`.

### 2. Pre-Generation Gate (two insertion points, regex-only)

**(a) `jarvis/brain/local_action_gate.py`** — after `_normalize()`, before
pattern checks, add:

```python
if registry.has_action_intent(normalized) and \
        registry.resolve_intent(normalized) is None:
    return LocalActionPlan(
        mode=LocalActionMode.UNSUPPORTED,
        response_text=_unsupported_response(normalized, lang),
    )
```

New `LocalActionMode.UNSUPPORTED` routes directly to TTS; the brain is never
called. This is intentionally AP-11 compliant — no LLM, regex-only.

**(b) `jarvis/brain/manager.py`** — sibling check `_capability_resolves(text)`
alongside the existing `_should_force_openclaw` heuristic. If
`has_action_intent AND NOT _capability_resolves AND NOT _is_smalltalk`:
skip brain dispatch and Jarvis-Agent spawn; emit the deterministic UNSUPPORTED
response via the same TTS path as a short reply.

Deterministic response phrasing (no LLM, constant strings):

- DE: *"Das kann ich noch nicht. Mir fehlt dafür ein Werkzeug — wenn du mir
  verrätst welches MCP oder welche Integration zuständig wäre, kann ich's
  lernen."*
- EN: *"I can't do that yet. I don't have a registered tool for it. Tell me
  which MCP or integration should handle it and I can learn."*

### 3. Capability-Aware Prompts + Critic Honesty

**3a. Dynamic system prompt** — the hardcoded `NUTZE: search_web / cli_*` block
in `manager.py:770-786` is replaced with `registry.render_for_prompt(lang)`.
A hard rule is appended:

> You must never claim to perform an action that is not listed above. If the
> user asks for one, respond with: "Das kann ich noch nicht — mir fehlt das
> passende Werkzeug." Do not invent tools.

**3b. Ack-Brain forbidden vocabulary** (`jarvis/brain/ack_brain/persona_prompt.py`)
— extend the forbidden vocabulary with action-promise patterns:
`"mache ich"`, `"wird erledigt"`, `"ist gesendet"`, `"ist eingetragen"`,
`"kümmere mich"`, `"I'll do that"`, `"will be sent"`, `"will be scheduled"`,
`"consider it done"`. The Ack-Brain is permitted only: acoustic acknowledgment,
context-restating clarifying questions, or silence on uncertainty.

**3c. Critic capability-honesty gate** — the Critic parses worker output for
tool-call evidence. If the mission's resolved `Capability` has
`requires_evidence=True` and no tool-call evidence is present, `CriticVerdict`
is `success=False` with `reason="capability_not_executed"`. `summary_de` is
derived from tool-call evidence via `summarise_from_tool_calls(calls)` in
`jarvis/missions/critic/summary.py`, not from the worker's text claim.

For Jarvis-Agent missions in the current Welle-2 mock state (no tool-call telemetry
streaming), the Critic **defaults to `success=False`** for
`requires_evidence=True` capabilities. This conservative default is intentional
and will be relaxed in Welle 3 when proper tool-call telemetry lands.

### 4. Extensibility Contract

Adding a new capability (e.g. a Gmail MCP server) requires:

1. Register the server via `mcp.json` or `jarvis.mcp` entry-point group.
2. `MCPRegistry.load` automatically calls `CapabilityRegistry.register(...)` for
   each namespaced tool — **zero edits** to brain, gate, filter, or critic.
3. Optional: override verbs/objects via `[capabilities.mcp.gmail.send_mail]` in
   `jarvis.toml`.

See `docs/plans/capability-coupling/EXTENSIBILITY.md` for the step-by-step
contributor guide.

## Consequences

**Positive:**

- Phantom confirmation is structurally impossible for any capability class that
  is gated at the pre-generation layer: the LLM is never asked to perform an
  action it cannot execute.
- Registering a new MCP server or harness adapter automatically extends the
  truthful surface with no manual edits to brain or critic code. The capability
  vocabulary is self-documenting: `registry.render_for_prompt()` generates the
  system prompt section automatically.
- The `requires_evidence` flag gives the Critic a machine-readable contract per
  capability instead of a blanket "empty diff = failure" rule. File-editing
  tasks and shell tasks correctly require evidence; smalltalk and Q&A tasks
  (which have no `requires_evidence=True` capability) are not incorrectly
  penalised.
- The dynamic system prompt eliminates `search_web` prompt-claim drift
  (manager.py:774) permanently: if `web-search` is not registered, it is not
  listed; if it is listed, it must exist.
- The AP-11 constraint is preserved. Both gate insertion points are regex-only;
  no LLM call is added to the hot path.

**Negative / trade-offs:**

- Every new tool, MCP server, harness adapter, or local-action pattern now
  requires a registration step. This is a small extra cost per feature but an
  explicit contract rather than an implicit one. The extensibility contract
  (Section 4) is designed to make this cost negligible when the adapter already
  exists.
- The Critic will conservatively fail `requires_evidence=True` missions that use
  the Welle-2 mock Jarvis-Agents path (no tool-call telemetry). This is the correct
  default — accepting unverifiable success claims is the bug this ADR fixes.
  Welle-3 tool-call telemetry resolves this at the Critic layer; the gate and
  prompt layers are unaffected.
- The output filter (`jarvis/brain/output_filter.py`) is **not touched**. This
  is intentional (AP-11 preservation). Post-generation scrubbing is the wrong
  layer for capability enforcement; the gate is pre-generation.

## Alternatives Considered

**Alt-A — Post-generation regex scrub (rejected).**
Scan every brain output for action-confirmation phrases ("wird erledigt",
"I'll send", "gesendet") and replace them with an UNSUPPORTED message. Rejected
for three reasons: (1) false positives on valid tool-call confirmations that
*do* have a registered capability; (2) requires maintaining a phrase blacklist
against an infinite vocabulary; (3) AP-11 forbids LLM calls in the output
filter path, and a phrase classifier capable of distinguishing phantom from real
confirmations is necessarily LLM-quality logic.

**Alt-B — LLM-based intent classifier (rejected).**
Run a fast model (Gemini Flash Lite) as a pre-classifier to decide "is this
utterance a request for an action that is not in our capability list?" before
dispatching to the brain. Rejected because: (1) adds 200–400 ms to every turn
in the voice critical path; (2) is non-deterministic — the same utterance may
classify differently on retries; (3) the existing `_should_force_openclaw`
heuristic already performs verb classification deterministically via regex;
extending it with a registry lookup is free.

**Alt-C — Require manual approval for every unregistered action (rejected).**
When an action-intent has no matching capability, present the user with an
approval prompt asking them to confirm before attempting. Rejected because this
is the anti-confirmation-fatigue contract violation documented in the memory
entry `feedback_no_nagging.md` and the global ADR-0011 alternative analysis.
The correct UX is a clear "I cannot do that yet" with a path to teach Jarvis,
not a confirmation loop for phantom capability.

## Cross-References

- Spec: `docs/plans/capability-coupling/SPEC.md`
- Extensibility guide: `docs/plans/capability-coupling/EXTENSIBILITY.md`
- Implementation:
  - `jarvis/core/capabilities.py` (new — Agent A)
  - `jarvis/core/capabilities_seed.py` (new — Agent A)
  - `jarvis/brain/local_action_gate.py` (extended — Agent B)
  - `jarvis/brain/manager.py` (prompt + gate — Agent C)
  - `jarvis/brain/ack_brain/persona_prompt.py` (forbidden vocab — Agent C)
  - `jarvis/missions/critic/runner.py` (honesty gate — Agent D)
  - `jarvis/missions/critic/summary.py` (new — Agent D)
- Tests:
  - `tests/unit/core/test_capabilities.py` (Agent A)
  - `tests/unit/brain/test_local_action_gate.py` (Agent B)
  - `tests/unit/brain/test_routing.py` (Agent C)
  - `tests/missions/critic/test_runner_dryrun.py` (Agent D)
  - `tests/integration/test_capability_coupling_e2e.py` (this ADR — Agent E)
- Bug register: `docs/BUGS.md` — BUG-028 Capability Hallucination
- Sibling pattern: `docs/anti-drift-three-layer.md`
- Jarvis-Agents bridge: `docs/jarvis-agents-bridge.md` AD-9 (Critic + risk-tier)
- Welle-3 telemetry: Jarvis-Agents bridge contract AP-OC7
