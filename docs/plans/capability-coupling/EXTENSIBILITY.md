# Capability Coupling — Extensibility Guide

How to add a new capability (tool, MCP server, harness adapter) so that Jarvis
truthfully advertises it, the gate allows it, the Critic can ratify it, and the
system prompt lists it automatically.

**Five steps. No edits to brain, gate, filter, or critic code required.**

---

## Step 1 — Define the Capability

Create or extend a seed file that maps your integration to a `Capability`
dataclass. For a new MCP server the right place is
`jarvis/core/capabilities_seed.py` (for static tools) or your MCP adapter
module (for dynamically loaded tools).

```python
from jarvis.core.capabilities import Capability

MY_GMAIL_CAPABILITY = Capability(
    id="mcp.gmail/send_mail",
    source="mcp",
    verbs=("schick", "sende", "send", "mail", "email"),
    objects=("email", "mail", "nachricht", "message"),
    description="Send an email via the Gmail MCP server.",
    risk_tier="ask",          # user confirmation required before sending
    requires_evidence=True,   # Critic must see a tool-call to ratify success
)
```

Fields to choose carefully:

| Field | Guidance |
|---|---|
| `id` | Stable, namespaced, never reused. Convention: `<source>.<server>/<tool>` for MCP, `tool.<tool-name>` for router tools, `local_action.<name>` for gate patterns. |
| `verbs` | DE + EN action verbs that a user would say to trigger this capability. Keep it tight — broad verbs create false positives in the gate. |
| `objects` | Nouns / domains the capability operates on. Used by `resolve_intent` for disambiguation. |
| `risk_tier` | Follow the Risk-Tier Policy in `jarvis/safety/risk_tier.py`. When in doubt, use `"ask"`. |
| `requires_evidence` | `True` for any action that modifies external state (send mail, write file, post to social). `False` for read-only or Q&A. |

---

## Step 2 — Register at Boot

Call `CapabilityRegistry.register(...)` from the place where your integration
is initialised. The registration must happen **before** the first voice turn.

**Router tool** — add to `jarvis/core/capabilities_seed.py` and call
`registry.register(MY_TOOL_CAPABILITY)` inside `bootstrap` in
`jarvis/missions/init.py`.

**MCP server** — `MCPRegistry.load` already calls
`CapabilityRegistry.register(...)` for every namespaced tool it loads. You
only need to ensure the `Capability` metadata is reachable from the MCP schema
description. If the auto-derived verbs/objects are wrong, override them in
`jarvis.toml`:

```toml
[capabilities.mcp.gmail.send_mail]
verbs = ["schick", "sende", "send", "mail", "email"]
objects = ["email", "mail", "nachricht", "message"]
```

**Harness adapter** — call `registry.register(...)` inside the adapter's
`__init__` or `setup` method before the voice path becomes active.

**Local-action pattern** — add a `Capability` call alongside the pattern
registration in `jarvis/brain/local_action_gate.py`.

---

## Step 3 — Verify the Gate Allows It

Run the unit test for the local-action gate and spot-check that your new
capability is reachable:

```bash
pytest tests/unit/brain/test_local_action_gate.py -v
```

Then verify manually with a quick registry probe:

```python
from jarvis.core.capabilities import get_registry
reg = get_registry()
cap = reg.resolve_intent("Schick eine Email an test@example.com")
print(cap)  # should print your MY_GMAIL_CAPABILITY, not None
```

If `resolve_intent` returns `None`, the verbs or objects in your `Capability`
do not match the utterance. Expand the `verbs` or `objects` tuple.

---

## Step 4 — Check the Dynamic System Prompt

Confirm that `render_for_prompt` now lists your capability:

```python
from jarvis.core.capabilities import get_registry
print(get_registry().render_for_prompt(lang="de"))
```

Your capability's `description` should appear in the output. If it does not,
the registration did not happen before `render_for_prompt` was called — check
the boot order in `jarvis/missions/init.py`.

---

## Step 5 — Add a Regression Test

Add at least one positive test to
`tests/integration/test_capability_coupling_e2e.py` confirming that an
utterance that should trigger your capability resolves correctly and does **not**
return `UNSUPPORTED`. Also add a negative test if any related utterance should
still be blocked (e.g. "send a fax" should stay UNSUPPORTED even after Gmail is
registered).

```python
def test_gmail_send_resolves_after_registration() -> None:
    from jarvis.core.capabilities import CapabilityRegistry
    from jarvis.core.capabilities_seed import MY_GMAIL_CAPABILITY

    reg = CapabilityRegistry()
    reg.register(MY_GMAIL_CAPABILITY)
    cap = reg.resolve_intent("Schick eine Email an test@example.com")
    assert cap is not None
    assert cap.id == "mcp.gmail/send_mail"
```

Run the full integration suite to confirm no regressions:

```bash
pytest tests/integration/test_capability_coupling_e2e.py -v
```

---

## What you do NOT need to touch

- `jarvis/brain/manager.py` — the `_capability_resolves` check reads from the
  registry automatically.
- `jarvis/brain/local_action_gate.py` — the UNSUPPORTED gate reads from the
  registry automatically.
- `jarvis/brain/output_filter.py` — unchanged per AP-11.
- `jarvis/missions/critic/runner.py` — the honesty gate reads the capability's
  `requires_evidence` flag from the registry automatically.

The capability you register is the single source of truth. All four of those
modules are consumers, not owners.
