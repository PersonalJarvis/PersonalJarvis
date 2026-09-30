---
name: game-art-pipeline
description: Develop or redesign Personal Jarvis world art, buildings, characters and 3D assets through an authored Blender reference scene, runtime review and controlled asset rollout. Use for game-art production and world redesign, not ordinary UI copy or behaviour-only bug fixes.
---

# Game-art production

Read [the binding production standard](../../../docs/agent-society/game-art-pipeline.md)
before planning or editing world/character art. It governs workflow; existing
runtime contracts still govern integration. Run commands from the repository root.

Start by establishing what the user asked for: pipeline setup, art direction,
reference scene, family rollout or integration. Do not revive a rejected design
or infer an engine change.

Use `python scripts/art_pipeline.py init <study-id>` for a new isolated study.
Retain editable Blender sources and use the manifest-driven exporter described
in the standard. Blender MCP can assist authoring; script-generated geometry
still needs visual judgement. Existing shipped assets remain untouched during
reference development.

Develop a small, finished reference in the actual Jarvis runtime. A functional
building, representative character and connecting ground should demonstrate
the intended look and motion. Review appearance, readability and character separately
from collision, animation and performance checks.

No separate user approval of the reference is required before batch-producing
other asset families; verify the reference in the runtime and continue.

Derive a modular kit from the reference and roll it out by family. Keep palette,
rig/slot, naming, material and scale conventions consistent with the reference
while preserving functional differences. Preserve user imports and
saved recipes. Do not equate a large asset count with quality.

Before integration, run the existing figure/fit checks where applicable and
the actual movement/render tests for affected surfaces. Measure runtime budgets
on stated hardware. Report unavailable checks honestly. Do not write invented
evidence or silently promote review GLBs into the production catalog.

End a handoff with the study path, current stage, scope, unresolved findings,
evidence and the next action.
