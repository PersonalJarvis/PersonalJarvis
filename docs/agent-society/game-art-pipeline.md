# Game-art production standard

Status: binding for future world, building and character art work, adopted
2026-09-08. This establishes a production workflow, not a new island design.
No palette, camera restriction, engine migration or replacement asset is
approved by adopting this standard.

For art-production workflow and review order this document takes precedence
over the earlier world master plans. Existing runtime contracts, saved recipe
IDs, skeletons, attachment slots, asset licensing and platform rules remain
binding until deliberately changed in a separately scoped implementation.

## Tools and responsibilities

- Blender is the authoring source for meshes, UVs, materials, rigs and animation.
  Retain editable `.blend` sources and the export recipe. Procedural modelling
  is useful for an approved modular system; it is not a substitute for visual
  judgement. Blender MCP is an optional interface to Blender, not a quality gate.
- The existing Three.js/WebView renderer remains the integration target.
  Evaluate the actual exported assets there. A beautiful offline render does
  not establish runtime quality: procedural shaders, lighting and material
  effects may require baking or equivalent runtime implementation.
- An engine change needs a small feasibility study and a separate decision
  covering integration, animation, navigation, distribution and target devices.
  Do not migrate engines just because the current art is unsatisfactory.
- Keep artistic ownership explicit. The coding agent can implement tools,
  shaders and behaviours, while authored art still needs deliberate composition,
  iteration and the user's visual review. Never infer quality from asset count.

## Workflow and decision points

### 1. Brief and references

Create an isolated study. Record intent, audience, references, usage rights,
shape language, colour hierarchy, material treatment, lighting, camera and
normal viewing distances. Decide pixel treatment at the intended viewport size.
Set device/viewport/frame-time and geometry budgets before implementation;
derive them from the intended product rather than copying arbitrary limits.
Unresolved art choices stay unresolved in the brief, never silently approved.

### 2. Blockout and movement proof

Use simple geometry to establish scale, circulation, entrances and interaction.
Test one character approaching and leaving one functional building. Establish
separate collision shapes, walkable routes and interaction anchors. Distinguish
terrain height, collision, root motion and visual animation. Include corners,
opposing agents, blocked destinations and transitions that previously clipped.
Passing this step proves layout feasibility, not visual completion.

### 3. Finished reference scene

Author a representative character, a functional building, connecting ground
and a small vegetation/decor set. Make the building's function recognisable in
its silhouette and details. Finish materials, contact, shadows and animation.
Retain editable sources; export into the study, never over shipped assets.

Show it in the actual runtime at normal viewing distance, relevant zooms and
view angles, in motion and with the app's supported appearances. Separate
artistic findings from measured engineering results. Iterate on this small
scene until it establishes the desired visual standard.

**Obtain the user's approval of that specific runtime reference scene before
batch-producing or replacing further asset families.** A request to set up this
pipeline, a technical pass, a Blender render or an agent-written approval file
does not constitute that approval. Existing explicit approval applies within
its stated scope; do not ask again unless the approved direction changes.

### 4. Reusable kit and family rollout

Derive modules, dimensions, materials, palettes, naming and rig conventions
from the approved scene. Scripts automate repeatable construction, variants,
export and checks. Maintain intentional silhouette and function differences.
Roll out one family at a time, comparing it against the approved scene in the
runtime. Re-review changes that materially alter the approved direction.

### 5. Integration and verification

Only then integrate fingerprinted GLBs, catalog entries, animation data,
material conversion and documentation. Preserve saved recipes and user imports.
Check forward axes, ground contact, rig/slot compatibility, embedded textures,
licensing, collision/visual alignment, animation transitions and budgets.
Use the existing figure validator and fit audit where applicable:

```text
python scripts/ci/check_society_figures.py
python scripts/figures/audit_fit.py --json
```

Building, terrain and navigation changes require checks appropriate to their
actual implementation; there is no claim that an existing figure gate tests
these. Capture runtime screenshots and a walkthrough, regression results and
performance measurements with device, resolution and agent count. Missing
device coverage stays explicitly unverified. Follow the normal build/commit
rules; publishing, mass replacement and engine migration are not implicit.

## Isolated study tools

```text
python scripts/art_pipeline.py init reference-study
python scripts/art_pipeline.py check art/studies/reference-study/study.json
```

The initializer creates a pending brief/review and empty `source/`, `exports/`
and `evidence/` directories. It creates no model and approves no design. Never
overwrite an existing study; choose a new ID for a separate direction.

Add an asset entry after choosing the reference scope:

```json
{
  "id": "workshop-reference",
  "kind": "building",
  "source": "source/workshop.blend",
  "collection": "Export",
  "export": "exports/workshop.glb"
}
```

The review exporter reads paths from this manifest and requires a study inside
`art/studies/`. Run Blender separately, without loading embedded scripts:

```text
blender --background --factory-startup --disable-autoexec --python scripts/art/export_study.py -- --manifest art/studies/reference-study/study.json --asset workshop-reference
```

It exports only the named collection in the active scene, embeds textures and
replaces its study GLB only after a structural check. It does not promote assets
to production or replace the existing character build's contract checks.

Record runtime screenshots/video in `runtime_evidence` and a written check
result in `technical_report`, using study-relative file paths. Then run:

```text
python scripts/art_pipeline.py check art/studies/reference-study/study.json --stage reference
```

This returns an evidence fingerprint and still reports rollout as unapproved.
After actual user approval, write an English approval summary and scope in
`review.md` with `Decision: approved`. Keep private conversation content and
personal identifiers outside the public repository. Set `approval.status` to
`approved` and `approval.evidence_sha256` to the reviewed fingerprint:

```text
python scripts/art_pipeline.py check art/studies/reference-study/study.json --stage ready
```

Any changed source, export, brief or evidence invalidates that fingerprint.
The tool checks artifact consistency and approval-record completeness. It
cannot authenticate user consent, judge aesthetics, prove that a screenshot
is genuine or certify collision/performance claims. The agent must verify those
against the conversation and actual runtime tests. No command auto-approves,
copies a study into production or redesigns the island.

## Handoff to another coding agent

Provide the study path, approved reference and its scope, open art decisions,
editable sources, export command, integration map, test evidence and known
limitations. State the current stage and one concrete next action. An unfinished
reference is handed off as unfinished, even when its export and tests pass.
