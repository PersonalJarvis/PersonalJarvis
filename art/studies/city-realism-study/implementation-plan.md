# Future city implementation plan

Prepared: 2026-09-09. Intended start: 2026-09-10, Europe/Berlin.
Status: reference implementation in progress; no visual reference approved yet.
Current implementation: [authored five-district city, metro interaction and asset-by-asset evidence](evidence/city-integration-review.md). This record supersedes the initial two-stop implementation report.
The accepted [2026-09-10 standard](standard.md) supersedes earlier shortened-travel suggestions below. See [implementation evidence and remaining work](evidence/implementation-review.md) before continuing.
Planning artifact tier: T1. Implementation tiers are assigned below.

## Objective

Replace the island presentation with a large, coherent future city. Redesign all bundled map assets, introduce useful rail travel and improve how agents move between actual work destinations. Use the supplied future-city screenshot as the primary visual reference: broad roof canopies, diagonal structure, elevated walkways, planted public spaces and restrained linear lighting. Read the updated direction at the top of [brief.md](brief.md); earlier island proposals are superseded.

Tomorrow's objective is an isolated, interactive city blockout with an agreed scale and a route above and below a walkway. The first complete milestone, spanning subsequent work as needed, is two stations, a moving train, two work destinations and an agent that boards, rides, exits and responds to changing tasks. Do not promise the entire finished city or this complete milestone in one day.

## Binding scope and boundaries

- Build a city, not an island; do not retain the coastline, central plateau or 512-metre source extent as requirements.
- Existing engine and WebView remain. Assets are authored in Blender and exported as GLB.
- Existing agent IDs, building/place identities, saved figure recipes, user imports and real tool execution remain functional.
- Train journeys never delay commands, missions or tool execution. Visual position and actual task state are distinct.
- The current motion is local presentation of backend checkpoints. Do not invent backend activity events or claim demo events are live work.
- Transport simulation is local and deterministic where possible. No LLM calls, new sockets or network traffic per movement tick.
- No production asset replacement before the user approves the specific finished runtime reference. The present agreement approves planning and direction, not an unseen reference.
- Do not restart, quit or kill the desktop app. Frontend builds use the existing bundle reload mechanism.
- This plan schedules no automation and starts no background implementation job.

## Existing foundation and required changes

Source paths below are relative to the repository. Recheck them at kickoff because the checkout is shared.

| Foundation | Reuse | Change needed |
|---|---|---|
| `scripts/art_pipeline.py`, `scripts/art/export_study.py` | Isolated study manifest, named Blender collection export, evidence fingerprint | Add a scoped runtime preview path for study assets; current scaffold does not supply one |
| `scripts/world/build_world_kit.py` and kit modules | Reproducible building generation and metadata | Author a new modular architectural kit and preserve interaction nodes |
| `scripts/figures/contract.json`, figure builders and validator | Skeletons, slots, clips, provenance, feet/axis/fit checks | Introduce a compatible material profile; existing gate requires nearest sampling and small palette textures |
| `world/islandLayout.ts` | Existing local navigation concepts and functional place semantics | New city layout and elevated route graph; multiple elevations cannot share one height sample |
| `world/Walkers.tsx`, `walkerKinematics.ts`, `WalkerFigure.tsx` | Checkpoint consumption, figure assembly, local motion | Journey planning, boarding/riding/exiting states and route changes |
| `world/KitBuilding.tsx`, `MemoryHouse.tsx`, `worldMaterials.ts`, `figures/assembleFigure.ts` | Loaders and common preview paths | Preserve physical materials instead of converting everything to Toon/Lambert |
| `world/WorldStage.tsx`, camera/composer/settings and WebGL hooks | Runtime integration, lifecycle, appearance and fallback behavior | City framing, study preview, measured render profiles and eventual district loading |

`world/` and `figures/` above refer to `jarvis/ui/web/frontend/src/components/society/`.

## Work packages and dependency order

### P0 — Baseline, scope and safe preview (first session; T2 one frontend surface)

1. Read repository instructions, project memory, the art-production standard, this plan and the study manifest. Check relevant dirty paths and active work. Do not revert or stash another session's edits.
2. Use an isolated checkout if shared frontend changes prevent reliable work. For a new worktree, run the required preflight and verify the actual Python import location. Record the base revision.
3. Capture the current runtime with 1 and 30 agents at 1366x768 and 1920x1080 where available. Record GPU/device, render scale, CPU/frame-time distribution, draw calls, triangles and memory estimates with the measuring method. A 100-agent run is an optional stress case, not an existing supported guarantee.
4. Trace the actual checkpoint feed and available task events. Document which changes can drive journeys today and which would require backend work.
5. Add an isolated developer preview inside the real rendering stack. Select study assets explicitly; keep it out of normal startup and production catalog defaults. Use clearly labelled deterministic demo agents for reproducible checks, then verify live checkpoint binding separately.

Deliverable: baseline report plus a preview that opens and closes cleanly, uses the real renderer and preserves the current production experience. Record missing devices as unverified. No acceptance can be inferred from offline Blender images.

### P1 — City scale and layered blockout (depends on P0; T2 frontend surface)

- Use one metre per world unit. Suggested trial footprint: a 600 by 400 metre district with roughly 200–300 metres between the first two stops. These are tunable trial values, not approved final city dimensions.
- Fit a normal-height figure, door, platform, train and building before duplicating structures. Keep station access short and legible.
- Block out two functional destinations, two platforms, a rail route, a ground walkway and an elevated walkway crossing above it.
- Define district/plot/entrance IDs separately from geometry. Keep mappings from existing semantic checkpoints to city destinations.
- Define a layered graph with node ID, 3D position, surface/layer identity and edge mode. Level changes are explicit connections; overlapping X/Z coordinates do not imply connectivity.
- Start with district overview and close inspection using existing camera controls. Perspective is a separately scoped enhancement if needed; do not casually rewrite picking and camera mathematics.

Deliverable: an interactive blockout with clickable destinations and correct walking above and below the crossing. Freeze trial scale only after inspection. Large-city expansion should use district loading and level-of-detail rather than multiplying the entire detailed district in memory.

### P2 — Useful agent transit (depends on P1; T2 if entirely local presentation)

- Add explicit journey stages: walk to stop, wait, board, ride, exit, walk to destination and arrive. Keep task state separate from journey state.
- One train and two stops are sufficient for the reference. Include dwell time, capacity and a boarding queue; no full railway economy or city traffic simulation.
- Compare end-to-end route time including station access and waiting. Choose walking when it is faster. Use run animation only when the figure has a compatible clip; otherwise use an honest supported mode.
- Capture one activity intent at a time. Ignore obsolete sequence updates; coalesce rapid checkpoint changes to avoid endless U-turns. During a ride, a new target changes the exit/routing choice at a reachable stop.
- A cancelled or completed task updates its visible status immediately; travel cleanup follows a defined safe transition. No phantom completion at a building and no command waiting for arrival.
- Define unreachable destination behavior and bounded waits. Provide explicit reduced-motion/hidden-view catch-up without replaying an entire backlog of journeys.
- Passengers ride relative to the train transform and disembark at valid anchors. Use train-path reservations sufficient to avoid self-conflicting movement in the reference.

Deliverable: reproducible round trips and interruption cases. Proposed presentation goal: normal cross-district work transitions feel complete within about 3–8 seconds. This is a visual pacing experiment, not a real rail-speed requirement; if the physical route cannot satisfy it, use an explicitly designed shortened presentation rather than implausible walking speed.

If actual tool-level event visibility is missing, retain honest checkpoint-driven travel and open a T3 package for the shared activity schema. That package must include five-layer parity where applicable, affected backend contracts, OS parity documentation and the repository's required fresh-install/single-key evidence. Do not silently expand P2 into a cross-layer rewrite.

### P3 — Finished architecture and character reference (depends on P1; can follow P2 iteratively)

- Save editable Blender sources for one station/plaza, one functional building, one representative figure with accessory, a train and a small vegetation set.
- Use the screenshot's structural language; original assets only, with recorded provenance for any licensed inputs.
- Trial physically based materials in the isolated renderer. Keep material changes scoped until approved; verify building cards and figure viewers use the same eventual treatment.
- Preserve skeleton/slot/clip semantics. If higher-resolution textures or tint masks need new metadata, create a versioned compatible asset contract with old-profile coverage. Treat that shared contract change as T3 and satisfy its repository requirements.
- Choose geometry/texture limits from P0 measurements. Existing budgets must not simply be removed to make assets pass.
- Inspect daylight and dusk, overview and close view, movement and both app appearances. Preserve readability when shadows or expensive effects are disabled.

Deliverable: finished assets shown in the real two-stop city reference, source files, exports, runtime images/walkthrough and technical report. Technical success and visual approval are separate records.

### G1 — Specific reference approval

Run the study reference check after evidence exists. Present the actual runtime scene and remaining limitations to the user. Obtain approval of that specific scene before family rollout. Record the real approved scope and matching evidence fingerprint. Never prefill approval because the project direction was accepted. A changed reference invalidates stale approval.

### P4 — Whole-city production (depends on G1)

Derive reusable structure, facade, roof, lighting, landscape and street modules. Roll out in these bounded groups:

1. Rail kit, stations, roads, elevated walkways and plazas.
2. All 12 functional building GLBs, preserving entrances and active-state hooks.
3. Modern character bases and their compatible equipment.
4. Remaining fantasy, science-fiction, cartoon, animal and spirit families. Preserve identities and user recipes; resolve materially different style choices in family review.
5. All 55 equipment parts and both animation libraries, tracking overlap with earlier groups so nothing is counted twice.
6. Procedural housing, landmarks, trees, rocks, ground, water where appropriate, sky and effects. No island boundary is required merely because water exists.
7. Map labels, selection markers, minimap, building/figure previews and city overview navigation.

Use [asset-inventory.csv](asset-inventory.csv) as the 95-file ledger. Add individual procedural variants before each relevant group; the research brief currently inventories those by family only. Add new train/station assets to the same ledger. Every shipped item needs source, export, integration and verification status. New districts reuse modules with deliberate silhouette variation and measured loading/LOD behavior.

### P5 — Integration and completion

Preserve existing layouts/poses through an explicit mapping or versioned migration. Never interpret saved island coordinates as city coordinates without a policy. Keep user imports and old recipe/material profiles working. Verify live agent tasks, correct destination semantics, founding/memory/conversation/retirement interactions and consistent appearance in all viewers. Integrate approved groups incrementally and build the frontend. Shipping code does not require restarting the desktop app.

## Acceptance matrix

All rows start PENDING; this table states requirements, not results.

| ID | Acceptance | Required evidence |
|---|---|---|
| C1 | City layout has independent dimensions and no required island boundary | Blockout overview, recorded dimensions and walk-through |
| C2 | Agent navigates over and under one crossing without changing floors incorrectly | Layered-route tests and runtime clip |
| C3 | Agent boards, rides, exits and reaches both work locations | Journey tests plus runtime round trip |
| C4 | Rail selection includes access/wait time; short trips can remain pedestrian | Deterministic comparative route tests |
| C5 | Commands continue while agent travels | Real task/event evidence with journey timestamps; label demo evidence separately |
| C6 | Retarget, cancellation, capacity and unreachable cases finish without stale loops | Focused behavioral tests and interruption clip |
| C7 | New look survives actual runtime and preview materials | Day/dusk screenshots at two viewing distances and both app appearances |
| C8 | Existing figure recipes, imports, equipment, hooks and interactions remain valid | Figure/fit checks and relevant regression tests |
| C9 | Context recovery, offscreen sleep, reduced motion and no-WebGL fallback work | Lifecycle tests and runtime checks |
| C10 | Reference and city stay within agreed device budgets | Measured baseline comparison with stated device, viewport and agent count |
| C11 | Entire asset scope is accounted for | Completed bundled, procedural and new-transit ledgers; no hidden omissions |
| C12 | Production rollout follows specific reference approval | Conversation-backed approval record and matching evidence fingerprint |

Hard negatives: no island restyle presented as a new city; no looping decorative train presented as agent transport; no movement-gated tool execution; no hidden teleports presented as physically traversed routes; no stale walking animation on a fast-moving character; no shader export assumed equivalent to runtime; no unchecked asset-contract relaxation; no fabricated visual or performance evidence.

## Verification and delivery discipline

Use existing relevant suites: `walkerKinematics.test.ts`, `worldCamera.test.ts`, `useWorldControls.test.tsx`, `islandLayout.test.ts`, `checkpointPlaces.test.ts`, `buildingPoses.test.ts`, `foundry.test.ts`, `conversation.test.ts`, `retirement.test.ts`, and affected figure tests. Add meaningful graph/journey tests for C2–C6; do not create tests merely mirroring constants.

Run the existing figure validator and fit audit when figures change. Run `tests/unit/test_art_pipeline.py` when study tooling changes. Backend event work additionally exercises checkpoints/world-feed tests and the appropriate society contracts. Run `npm run build` in `jarvis/ui/web/frontend` after frontend changes. Required code/test/design reviewers follow applicable repository instructions during implementation. Do not run the full suite for this planning-only artifact.

Commit finished steps with explicit owned paths. Do not capture unrelated shared edits, force-push or bypass hooks. Every handoff updates this plan with the current package, evidence, unresolved failures and one next action. Missing runtime/device checks mean pending acceptance, not Done.

## Tomorrow's start order

1. Complete P0 discovery/baseline and establish the isolated preview.
2. Build P1's layered blockout and trial dimensions.
3. If those checks pass, start P2's deterministic route/journey model. Do not spend the first day batch-producing polished buildings.
4. End with a working preview or a precisely documented blocker, recorded evidence and the next bounded package. The polished reference and G1 remain subsequent milestones.

## Paste-ready kickoff prompt

> Start the future-city project using `art/studies/city-realism-study/implementation-plan.md` and the updated direction in `brief.md`. Read applicable repository rules and the game-art-pipeline skill. Implement P0 and P1 first: inspect current shared work, establish a measured runtime baseline, create an isolated preview in the actual Three.js/WebView rendering stack, and build a layered city blockout with two stations, two work destinations, a ground path and an elevated crossing. This is a future city with new dimensions, not an island. Keep actual agent work independent of travel presentation. Preserve existing production assets, saved recipes and imports. Use the plan's trial dimensions only as starting values and record the tested scale. Verify the preview and crossing behavior, run the relevant checks, commit only owned finished changes and update the plan with evidence. Begin P2 only when P0/P1 are verified. Do not batch-replace asset families before approval of a finished runtime reference. Do not restart or quit the desktop app. Continue autonomously through the authorized package and report one concrete next action.
