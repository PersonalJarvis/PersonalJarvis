---
name: game-art-pipeline
description: Develop or redesign Personal Jarvis world art, buildings, characters and 3D assets through an authored Blender reference scene, runtime review and controlled asset rollout. Use for game-art production and world redesign, not ordinary UI copy or behaviour-only bug fixes.
---

# Game-art production

Read [the binding production standard](../../../docs/agent-society/game-art-pipeline.md)
before planning or editing world/character art. It governs workflow; existing
runtime contracts still govern integration. Run commands from the repository root.

Start by establishing what the user authorized: pipeline setup, art direction,
reference scene, approved-family rollout or integration. Pipeline setup does not
authorize a redesign. Do not revive a rejected design or infer an engine change.

Use `python scripts/art_pipeline.py init <study-id>` for a new isolated study.
Retain editable Blender sources and use the manifest-driven exporter described
in the standard. Blender MCP can assist authoring; script-generated geometry
still needs visual judgement. Existing shipped assets remain untouched during
reference development.

Develop a small, finished reference in the actual Jarvis runtime. A functional
building, representative character and connecting ground should demonstrate
the intended look and motion. Review appearance, readability and character separately
from collision, animation and performance checks.

**Obtain user approval of the specific runtime reference before batch-producing
or replacing other asset families.** Respect approval already given for that
same scope. A passing validator or an approval record authored by an agent is
not authorization. The evidence fingerprint prevents accidental stale approval;
verify consent from the conversation itself.

After approval, derive a modular kit and roll it out by family. Keep palette,
rig/slot, naming, material and scale conventions consistent with the approved
reference while preserving functional differences. Preserve user imports and
saved recipes. Do not equate a large asset count with quality.

Before integration, run the existing figure/fit checks where applicable and
the actual movement/render tests for affected surfaces. Measure runtime budgets
on stated hardware. Report unavailable checks honestly. Do not write invented
evidence or silently promote review GLBs into the production catalog.

End a handoff with the study path, current stage, approved scope, unresolved
findings, evidence and the next action. No approval exists merely because the
user asks a new coding agent to continue.
