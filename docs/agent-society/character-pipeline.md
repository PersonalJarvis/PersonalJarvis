# Character Pipeline — the 3D figure standard

Status: **binding since 2026-09-02 — the maintainer took the three §12 decisions as recommended,
and the first figure (`biped-medium.glb`, KayKit Rogue base) ships through the gate.** Amended
the same day with what the build taught (§4.3 bone set, §4.4 budgets, §4.6 cells, §5 rules).
Subordinate to [`MASTERPLAN.md`](MASTERPLAN.md)
(§4.2 figure decisions, §4.3 world branding, §8 M3). Where the two disagree, the master plan
wins — but every figure-related rule that the master plan only names lives HERE in full.

The problem this document solves: the society will need dozens of different characters — humans,
hobbits and dwarves, animals, fantasy folk (mages, knights, rangers, orcs), stylised likenesses of
real people, and Gigi — and every one of them must walk on the island without a single
"he walks backwards", "her feet slide", "it snaps around" or "it floats above the ground" defect.
Those defects are not art problems; they are contract problems. One contract, one validator, one
runtime — and any character that passes the gate walks correctly, whoever or whatever made it.

---

## 1. What already exists (verified in the tree, 2026-09-01)

| Piece | Where | What it gives the figures |
|---|---|---|
| three `0.185.1`, `@react-three/fiber` v8, `@react-three/drei` `^9.122` | `jarvis/ui/web/frontend/package.json` | `GLTFLoader`, `SkinnedMesh` + `AnimationMixer`, `RenderPixelatedPass`, `SkeletonUtils.clone`, drei `useGLTF` / `useAnimations` / `Clone` / `Html` / `OrthographicCamera`. No `postprocessing` package — the pixel pass comes from three's own addons. `InstancedMesh` has NO skinning support in this version (checked `src/objects/InstancedMesh.js`): walkers are cloned skinned meshes, not instances. |
| Vite `^6.4` | `frontend/vite.config.ts` | `.glb`/`.gltf` are known asset types: `import url from "./biped.glb"` yields a fingerprinted URL in `dist/assets/` — the same route the app mark took in commit `65f3b826d` (never `public/`, which is copied verbatim and caches stale). |
| `useWebglSurface` (AP-32) | `src/hooks/useWebglSurface.ts` | context release on unmount, `webglcontextlost` recovery, remount key. Enforced by `scripts/ci/check_webgl_contexts_released.py` — every figure canvas mounts through it or the build is red. |
| `useCanvasAwake` | `src/hooks/useCanvasAwake.ts` (untracked, model-card session) | IntersectionObserver gate for rAF; never `document.hidden` (WebView2 lies for minutes). |
| `PlaceholderFigure` + `data.ts` | `src/components/society/card/figure/PlaceholderFigure.tsx`, `src/components/society/data.ts` (untracked) | the card's stand-in figure and the `avatarUri: string \| null` slot this pipeline replaces (§9). |
| `GigiFigure` | `src/components/deck/room/GigiFigure.tsx` | the mascot as an extruded SVG — breath, pointer-turn, blink, R3F click. Becomes the `spirit` archetype's mesh (§3). |
| `deckRoom.ts` + `deckRoom.test.ts` | `src/lib/` | the house pattern for 3D numbers: pure constants + pure functions pinned by vitest. The walker kinematics follow it (§6). |
| Bundle budget gate | `scripts/ci/check_frontend_bundle_budget.py` | counts only the ENTRY chunk. Figures live in the lazy society chunk and in asset files — they never touch the budget, and must not: no figure import from any eagerly-loaded module. |
| Blender **5.0.1** | installed on the maintainer's box; headless `blender -b -P script.py` runs on Windows, macOS and Linux | the build tool for the rig and every derived GLB (§7). The Blender MCP (`mcp__blender__*`) drives the same `bpy` code interactively and needs a running Blender GUI; the pipeline never depends on it. |
| Image generation route | xAI `grok-imagine` via keyring `grok_api_key` (project memory, works as of 2026-08) | AI-generated detail sheets over the UV template (§8, route D). |
| Tripo MCP | registered user-scope, needs `TRIPO_API_KEY` | image-to-3D + stylize (voxel/low-poly) + auto-rig (v1.0 biped, v2.5 quadruped/avian/…) + 90+ preset animations, GLB out — the exact-likeness route (§8, route C). |

Nothing else exists: **no GLB asset, no rig, no clip, no validator** is in the tree today.

---

## 2. Goals and non-goals

Goals (the acceptance bar for M3's figure work):

1. Every character walks **forward**, turns smoothly, stops into idle, never slides, never floats,
   never snaps — on the island (~22 px tall at the 320×180 target) AND on the model card
   (~400 px tall). Same asset, same look, two canvases.
2. **One runtime for all characters.** The world and the card code know archetypes and clip
   names, never individual characters. Adding a character is adding data, not code.
3. **Any origin passes the same gate:** hand-modelled, palette-swapped, AI-textured, AI-generated
   from a photo, or Gigi. The validator (§10) is the definition of "done" for an asset.
4. **Pixel-retro look at low cost:** low-poly meshes, nearest-filtered pixel-art sheets, flat/toon
   lighting, rendered through the pixelated pass — WebView2 on integrated graphics stays above the
   FPS floor with 30 walkers on screen.
5. **Reproducible and cross-platform:** every shipped GLB is rebuilt from source by one headless
   Blender script on any OS; no hand-exported binary whose recipe lives in someone's head.

Non-goals (explicitly out of scope for this standard):

- Facial animation, lip-sync, cloth or hair physics, inverse kinematics, ragdolls.
- Photoreal likeness. "Real people" means a **stylised, consented** pixel likeness (§11).
- Instanced crowds. The island holds a society (≤ ~30 figures), not a battlefield.
- Runtime mesh generation. Everything is baked at build time; the runtime loads and animates.

---

## 3. Archetypes — the three skeletons the runtime knows

The runtime code is written against archetypes. A character declares exactly one.

| Archetype | Locomotion | Covers | Rig | Clips (§5) |
|---|---|---|---|---|
| `biped` | walks on two legs | humans, real-person likenesses, hobbits, dwarves, elves, orcs, mages, knights, robots, skeletons | 19 bones (§4.3); three proportion presets `small` / `medium` / `large` baked as separate GLBs from one source (§7) | full set |
| `quadruped` | walks on four legs | dogs, cats, foxes, wolves, deer, horses, boars, dragons-on-the-ground | 22 bones (§4.3) | `idle walk run sit sleep talk celebrate` (no `work`, no `sit_desk`: animals do not type — a working animal stands at the desk and `talk`s) |
| `spirit` | hovers | Gigi (lead figure), ghosts, will-o'-wisps, floating familiars | 3 bones: `root`, `body`, `face` | `idle walk talk celebrate sleep` — `walk` is a glide with bob; heading rule applies exactly as to legs (a face that glides backwards reads just as wrong) |

Later archetypes (`flyer`, `serpent`) are M6 material and are added by adding a row to this table,
a bone list to §4.3, and a build target to §7. Never by special-casing a character in the runtime.

Proportion presets are NOT runtime bone scaling (scaling a bone at runtime squashes its children
and breaks the clip): they are **baked variants**. One biped source → `biped-small.glb`
(hobbits, dwarves, children, gnomes: legs ×0.72, head ×1.15, feet ×1.3),
`biped-medium.glb` (humans, elves), `biped-large.glb` (orcs, trolls, golems: torso ×1.25,
arms ×1.2). Same bone names, same clips, three files.

---

## 4. The figure asset contract (v1)

Everything in this section is machine-checked by the validator in §10. A rule that the validator
cannot check does not belong in this section; it belongs in §8 (workflow guidance).

### 4.1 Units, axes, origin

- **Units:** metres in the world (1 unit = 1 m, matches `deckRoom.ts`). A GLB ships in its
  source's native units and records its measured `height_m` in the extras; the runtime scales the
  figure to the recipe's height (`heightM`) or the variant default (`biped-medium` 1.75 m,
  `small` 1.15 m, `large` 2.3 m, `quadruped` 0.9 m, `spirit` 1.9 m — Gigi is 3.1 m in the deck,
  the lead is the tallest on the island, not a giant). Stride and speed scale with it (§6.3).
  Rescaling in Blender was rejected on purpose: "apply scale" does not touch action keys.
- **Up axis:** +Y (glTF). **Forward axis: +Z** — the glTF specification's front. The figure's
  face, nose, chest and toes point toward +Z in rest pose.
- **In Blender** that means: the character looks INTO the front view (numpad 1), i.e. its face
  points toward **−Y**; the glTF exporter's default "+Y up" conversion maps Blender −Y to glTF
  +Z. A character modelled facing +Y in Blender is the single most common cause of "walks
  backwards" — it exports facing −Z, and every heading rule in §6 then points it away from where
  it goes.
- **Origin:** the scene root sits on the ground plane between the feet: `min(y)` of the rest-pose
  mesh is `0 ± 0.01`, the XZ centroid of the feet (biped: `foot_l`, `foot_r`; quadruped: all
  four feet) is `(0, 0) ± 0.05`. Nothing floats, nothing sinks, and the world can place a walker
  at `(x, terrainHeight, z)` without per-character offsets.
- **Scale:** every node's scale is `(1, 1, 1)` after build (transforms applied). **No negative
  scale anywhere** — a mirrored node flips winding and shading and is the second classic
  "walks backwards / looks inside-out" cause.
- **Forward marker:** a leaf node named `FWD` under the root at local `(0, 0.5, 1)`. The
  validator reads its world position; a modeler who accidentally rotated the rig fails the gate
  instead of shipping a moonwalker. The runtime never uses it (it uses the root's +Z), and the
  dev Figure Lab (§10.3) draws it as an arrow.

### 4.2 Files and packaging

```
jarvis/ui/web/frontend/src/assets/society/figures/
  biped-small.glb        biped-medium.glb        biped-large.glb
  quadruped.glb          spirit.glb
  parts/                 one GLB per swappable part, skinned to the archetype skeleton (§4.5)
    biped/hair-01.glb  biped/hood.glb  biped/robe.glb  biped/pack.glb  quadruped/collar.glb …
  sheets/                default detail sheets per archetype (§4.6), embedded copies live in the GLBs
  SOURCES.md             provenance ledger: every source file, its license, what was changed (§11)
scripts/figures/         the build (§7): build_figures.py (Blender), contract.json, proportions.json
scripts/ci/check_society_figures.py   the validator (§10)
```

- One GLB per archetype variant carries the skeleton, the base body mesh and **all clips**.
  Parts are separate GLBs with no clips, skinned to the identical skeleton (same bone names, same
  rest pose, same joint order) so they attach by re-binding to the loaded base skeleton.
- Textures are **embedded** in the GLB (`image/png` buffer views), never side files: a single
  fingerprinted asset per part, always > Vite's 4 KB inline limit, so nothing lands base64 in a
  JS chunk.
- Binary `.glb` only. No `.gltf` + `.bin` pairs, no Draco, no meshopt, no KHR_texture_basisu:
  the assets are tiny and every extra decoder is a lazy chunk and a failure mode.
- Every GLB carries `asset.extras.jarvis_figure`:

```json
{
  "contract": 1,
  "archetype": "biped",
  "variant": "medium",
  "forward": "+Z",
  "height_m": 1.75,
  "clips": { "walk": { "stride_m": 1.28 }, "run": { "stride_m": 2.4 } },
  "source": "scripts/figures/src/biped.blend@<git-sha>"
}
```

`stride_m` is the ground distance one full loop of the clip would cover if the feet did not
slide. It is the number that makes §6.3 work and the build script measures it (heel travel in
the clip's own root space), a human never types it.

### 4.3 Skeletons

Bone names are the contract between base, parts, clips, retargeting scripts and the runtime.
Lower-case, underscore, `_l`/`_r` suffixes, no numbering, no prefixes (`mixamorig:` and
`Rig_Medium/…` prefixes are stripped at build time).

`biped` (23 — the CC0 base's deform set, renamed): `root` · `hips` · `spine` · `chest` · `head` ·
`upper_arm_l` `lower_arm_l` `wrist_l` `hand_l` `handslot_l` · `upper_arm_r` `lower_arm_r` `wrist_r`
`hand_r` `handslot_r` · `upper_leg_l` `lower_leg_l` `foot_l` `toes_l` · `upper_leg_r` `lower_leg_r`
`foot_r` `toes_r`. `handslot_*` is the prop attachment point (§4.5 `hand_l`/`hand_r` slots);
`toes_*` are the ground-contact joints the stride is measured on.

`quadruped` (22): `root` · `hips` · `spine` · `chest` · `neck` · `head` · `jaw` · `tail_1` `tail_2`
· `upper_leg_fl` `lower_leg_fl` `foot_fl` · `upper_leg_fr` `lower_leg_fr` `foot_fr` ·
`upper_leg_bl` `lower_leg_bl` `foot_bl` · `upper_leg_br` `lower_leg_br` `foot_br` · `ear_l`/`ear_r`
optional (allowed extras: `ear_l`, `ear_r`, `wing_l`, `wing_r`).

`spirit` (3): `root` · `body` · `face`.

`root` is the only bone allowed to translate in a clip, and only in Y (bob). Validator rule.

### 4.4 Mesh budgets

| | triangles | materials | draw calls after assembly |
|---|---|---|---|
| biped base | ≤ 4 500 | 1 | 1 (the six source meshes are joined at build) |
| quadruped base | ≤ 4 500 | 1 | 1 |
| spirit | ≤ 1 200 | ≤ 3 (the mascot has a lit body and unlit marks) | ≤ 3 |
| any part | ≤ 600 | 1 | 1 |
| assembled figure | ≤ 7 000 | — | ≤ 6 |

The first build measured the CC0 base at 4 263 triangles with its face modelled in geometry
(eyes, brows, nose — which is why no detail sheet is needed for it), so the budgets sit there.
Fragment cost is what the 320×180 target caps; vertex cost stays trivial: 30 walkers × 6 draw
calls = 180 calls, ~130 k triangles — comfortable on integrated graphics behind the pixel pass.
Vertex attributes: position, normal, uv, joints, weights — no tangents, no second UV, no vertex
colors (flat colors come from the sheet's palette strip, §4.6, so palette swaps stay one code path).
Max 4 influences per vertex (glTF default), weights normalized.

### 4.5 Part slots

A part is a mesh skinned to the archetype skeleton, occupying one slot. The base body already
looks complete without any part (a plain-clothes human, a plain fox) so an empty recipe is valid.

| Archetype | Slots |
|---|---|
| `biped` | `hair` · `headgear` · `face_extra` (beard, glasses, mask) · `torso_over` (cloak, robe, armor) · `belt` · `back` (pack, quiver, wings) · `hand_l` · `hand_r` (staff, book, mug, sword — props, unskinned, parented to the hand bone) · `feet` |
| `quadruped` | `headgear` · `collar` · `back` (saddle, pack) · `tail_extra` |
| `spirit` | `headgear` · `hand_l` · `hand_r` |

Part GLB naming: `<archetype>/<slot>-<name>.glb`. A part declares its slot in
`extras.jarvis_part = { "contract": 1, "archetype": "biped", "slot": "headgear", "hides": ["hair"] }`
— `hides` lets a hood suppress the hair mesh under it, the standard trick for keeping poke-through
out of a low-poly stack.

### 4.6 Texture sheets

- **One sheet per base / per part, embedded, PNG, 128×128** (parts may use 64×64). Power-of-two
  only, never larger than 256×256 — at 22 px on the island and ~400 px on the card, more texels
  only shimmer.
- **Nearest filtering, no mipmaps**, sRGB: the build sets sampler `magFilter/minFilter = NEAREST`
  in the GLB and the runtime re-asserts `NearestFilter` + `generateMipmaps = false` +
  `SRGBColorSpace` on load (glTF samplers are advisory; three honours them, but a gate is a gate).
- **Layout:** the top 16 rows of the sheet are the **palette strip** — 16 cells of 8×16 px, each a
  flat color. Every flat-colored face of the mesh maps its UVs INTO one palette cell (a tiny
  island in the middle of the cell — never on a cell edge, or nearest sampling bleeds the
  neighbor). The remaining 112 rows are the **detail area**: face, eyes, emblem, buttons, fur
  markings — pixel art, drawn by hand or generated (§8 route D).
- **Palette swap = repaint 16 cells.** The runtime clones the texture into a canvas, fills the
  16 cells from the recipe's `palette[16]`, uploads it as a new `CanvasTexture` (nearest, no
  mips). Cost: one 128×128 upload per figure at recipe change. Face and detail survive the swap
  untouched — a green hobbit and a red hobbit share one sheet.
- **Cell semantics are fixed per archetype** so palettes are portable across characters:
  `0 skin · 1 skin shade · 2 hair · 3 eyes · 4 primary garment · 5 primary shade · 6 secondary
  garment · 7 secondary shade · 8 accent · 9 metal · 10 leather · 11 fur/feather main · 12 fur
  shade · 13 shoes · 14 eye white · 15 emissive` (`scripts/figures/contract.json` is the list the
  build and the runtime read; unused cells keep the source's default colour).
  The existing `AgentPalette { primary, secondary, accent }` in `data.ts` maps onto cells 4/6/8
  and the build derives the shade cells (−18 % lightness) — the model card keeps working the day
  the first real sheet lands.
- Alpha: 1-bit only (fully opaque or fully transparent, `alphaMode: MASK`, cutoff 0.5). No blended
  transparency — it sorts wrong through the pixel pass and costs a second render path.

---

## 5. Animation clips

| Clip | Loop | Archetypes | Used when |
|---|---|---|---|
| `idle` | yes | all | standing; the wander model's rest beats (Hermes constants, MASTERPLAN §2.7) |
| `walk` | yes | all | moving at walking speed (`spirit`: glide) |
| `run` | yes | biped, quadruped | moving to a purposeful checkpoint when the choreography queue is > 2 events deep |
| `work` | yes | biped, spirit | at the desk, mission running (typing / conjuring) |
| `talk` | yes | all | speaking in a room, or its bubble is open |
| `sit` | yes | biped, quadruped | meeting pavilion (`sit_desk` is the same clip with the `work` upper body; not a separate asset) |
| `sleep` | yes | all | agent paused |
| `celebrate` | no | all | RESULT event for this agent; plays once, then blends to idle |
| `wave` | no | biped, spirit | agent created / selected in the card |

Rules (validated):

1. **In place.** No root XZ translation in any clip: `root` position keys vary only in Y, and
   `hips` is not the root (a rig that uses `hips` as root fails). Other bones MAY translate
   (a hips bob, a shrug) — rule 2 is what stops a baked drift. Locomotion is the runtime's job.
2. **Loops close.** For every looping clip the first and last key of every channel agree —
   translations within 2 mm, rotations within 1° — no hitch every second.
3. **Durations in band** (`contract.json`): `walk` 0.8–1.2 s, `run` 0.5–0.9 s, `idle` 0.8–6 s,
   `talk` 0.8–3 s, the rest 0.5–6 s. `walk` starts on the left heel strike (documented, not
   validated).
4. **`stride_m` measured at the finish step** for `walk` and `run` (§4.2) by forward kinematics
   over the exported clip — the same code the gate re-measures with. Runtime nominal speeds are
   derived from it (§6.3), never typed into a constant.
5. Clips live only in base GLBs. A part GLB with an animation fails the gate.
6. Sampled at the source's own frame rate (30 fps for the CC0 base — the import lands keys on
   integer frames, so the export samples every key exactly); no scale channels survive the finish.

---

## 6. Locomotion — the rules that stop the classic defects

Pure functions in `src/components/society/world/walkerKinematics.ts`, pinned by
`walkerKinematics.test.ts` exactly like `deckRoom.ts`. The R3F component reads them; it contains
no math of its own.

### 6.1 Heading

The figure faces +Z in its own space (§4.1). With `Object3D.rotation.y = θ`, local +Z maps to
world `(sin θ, 0, cos θ)`, so the heading for a velocity `v = (vx, vz)` is

```
θ = atan2(vx, vz)           // NOT atan2(vz, vx), NOT atan2(vx, −vz)
```

`Object3D.lookAt(target)` gives the same result for non-camera objects (it points local +Z at
the target) **only if** `target.y === position.y`; a target at terrain height on a slope tilts
the figure. Use the formula, not `lookAt`.

### 6.2 Turning

- Yaw moves toward θ along the **shortest arc** with a maximum angular speed (biped 540 °/s,
  quadruped 360 °/s, spirit 720 °/s) and an ease-out — never a snap, never the long way round.
- Heading updates only while `|v| > 0.05 m/s`. A stopped figure keeps its last heading; a figure
  never "looks at" a waypoint it is not moving toward (that is what makes a walker appear to back
  into a desk).
- A reversal (target more than 120° behind) turns in place for the first 0.15 s, then moves.
  Readable under the fixed dimetric camera, and the reason nobody perceives a "reverse gear".
- Arrival: decelerate over the last 0.4 m, then blend `walk → idle` in 0.2 s. Then, and only then,
  optionally turn to the checkpoint's facing (desks face the screen, the pavilion faces the table).

### 6.3 Speed and foot contact

The clip dictates the speed, not the other way round:

```
nominalSpeed  = stride_m / clipDuration          // e.g. 1.28 m / 1.0 s = 1.28 m/s
timeScale     = |v| / nominalSpeed               // 1.0 when walking at nominal speed
```

The walker moves at `nominalSpeed` by default, so `timeScale` sits at 1.0 and the feet plant
where they land. `timeScale` is clamped to `[0.7, 1.4]`; outside that band the runtime switches
clip (`walk ↔ run`) instead of speeding the loop into a cartoon. Wander uses `walk` at 0.8×
nominal (rest-biased, calm); purposeful moves use nominal; `run` only for the burst rule in §5.

### 6.4 Path following

- The island is a tile grid (M3 terrain); checkpoints are named nodes; A* over walkable tiles
  (~80 lines, no library). Path smoothing: string-pulling over convex corners so figures do not
  zig-zag tile edges. Movement is continuous along the smoothed polyline at the clip's speed.
- Ground snap: `y = terrainHeight(x, z)` each frame (a height field lookup, no raycast).
- Choreography queue (MASTERPLAN §3.3): a new checkpoint event while walking retargets the path
  from the current position — never teleports, never plays the walk backwards to "undo".
- Two walkers sharing a tile: a lateral offset of ±0.3 m keyed by agent id, no avoidance
  simulation. Cosmetic, deterministic, cheap.

### 6.5 Readability under the pixel pass

At 320×180 a 22 px figure has 4–5 px per limb. The rules that keep it readable: strong silhouette
per archetype (the hood is a shape, not a texture), outline via `RenderPixelatedPass`'s normal
edge (strength 0.3) not a texture line, `walk` with exaggerated arm swing (±45°), and clips
authored at 24 fps so nearest-filtered motion reads as deliberate pixel steps.

---

## 7. Build — reproducible, headless, one script

`scripts/figures/build_figures.py` runs inside Blender's Python:

```
blender -b --python scripts/figures/build_figures.py -- --out jarvis/ui/web/frontend/src/assets/society/figures
```

It works on Windows, macOS and a headless Linux CI runner (Blender runs without a display in `-b`
mode); it is the ONLY producer of shipped GLBs. It never imports `jarvis.*` (Blender's bundled
interpreter). What it does, in order, for every target in `scripts/figures/contract.json`:

1. **Load the source** (`scripts/figures/src/*.blend`, or a downloaded CC0 source recorded in
   `SOURCES.md` with its sha256 — route A in §8).
2. **Normalize the rig:** rename bones to §4.3 (mapping table per source), strip prefixes, drop
   IK/control bones, re-parent so `root` is the sole top bone, set rest pose facing −Y (Blender)
   → +Z (glTF), apply all transforms, remove negative scales, move the origin to the feet.
3. **Bake proportion variants** (`proportions.json`): scale bone lengths in edit mode, then the
   clips still fit (rotational data, unchanged hierarchy). Emits `biped-small/medium/large`.
4. **Normalize clips:** import the clip set, rename to §5, strip root XZ motion (bake root to
   `(0, y, 0)`), close loops (copy first key to last), resample 24 fps, drop scale keys, measure
   `stride_m` (heel travel), write `extras`.
5. **Sheet + UVs:** assign the archetype UV template (palette-cell islands + detail area), embed
   the default sheet, set NEAREST samplers, `alphaMode MASK`.
6. **Export GLB** (glTF 2.0, binary, +Y up, embedded images, skins, animations, no Draco), add
   the `FWD` marker, write `extras.jarvis_figure` / `jarvis_part`.
7. **Run the validator (§10)** on the output; a failing target fails the build.

Everything the script needs — bone maps, clip maps, proportions, sheet templates — is data in
`scripts/figures/*.json`, committed. A new character from an existing source is a JSON entry.
The Blender MCP is the fast inner loop for developing this script (run a step, screenshot the
viewport), never a build dependency.

---

## 8. Workflows — how ONE exact character is made

Every route ends in the same two artifacts: a contract-conforming GLB (base or custom) and a
**recipe** (§9). The routes differ only in where the mesh and the sheet come from.

| Route | Produces | Cost | Fit |
|---|---|---|---|
| **A — Base + parts + palette (Recommended)** | recipe only, no new binary | minutes, $0 | the roster's everyday characters: any human, hobbit, dwarf, elf, mage, knight, fox, dog; the default for user-created agents |
| B — New part or new animal mesh, modelled | one part/base GLB via the build | hours in Blender | a silhouette the parts library lacks (a wizard hat, a dragon) |
| C — AI mesh from a photo or prompt (Tripo) | a custom base GLB, normalized by the build | ~30–60 Tripo credits, 20–40 min | exact likenesses of real people (consented, §11) and one-off creatures |
| D — AI detail sheet (xAI image route) | a new 128×128 sheet in the recipe | seconds, cents | faces, emblems, fur patterns over the UV template |

Route A beats B for the roster because a new character must be creatable from the model card in
under a minute with zero binaries and zero review; B, C and D feed the parts/sheets library that A
draws from. Route A is also the reason every base and part must look finished on its own (§4.5).

### 8.1 Route A — a hobbit ranger, step by step

1. Archetype `biped`, variant `small` (§3 proportions do the hobbit; no hobbit mesh exists).
2. Parts: `hair-curly`, `torso_over-cloak`, `back-pack`, `hand_r-staff`, `feet` = none (hobbits go
   barefoot — the base body has feet).
3. Palette: cells 4/6/8 from the agent's `AgentPalette`, cell 2 hair auburn, cell 0/1 skin from the
   preset list, cell 10 leather for the pack.
4. Sheet: the archetype default (a friendly face). Optionally route D for freckles.
5. Save the recipe on the roster row. The world and the card render it the same frame.

Nothing was modelled, nothing was exported, nothing needs review — and it cannot walk backwards
because the only mesh involved passed the gate months ago.

### 8.2 Route C — a real person from one photo (Tripo), step by step

1. **Consent and scope** (§11): only people who asked for their own figure; the maintainer's
   likeness is the first and reference case.
2. `image-to-3D` (Tripo, one front-facing photo, full body if possible; otherwise a
   T-pose-shaped reference image generated first via route D's image route) → `stylize`
   (`voxel` or low-poly) → `convert` with a face limit ≤ 1 200 → `rig` v1.0 (biped, Mixamo-style
   bone names) → `retarget` for `idle`, `walk`, `run` presets → GLB download. All async, all
   polled; a `scripts/figures/tripo_fetch.py` helper wraps it with the key from keyring (never a
   `.env`, AP-12).
3. `build_figures.py --custom <file>`: the same normalization as step 2–6 of §7 (Mixamo bone map
   → §4.3 names, root motion stripped, origin to feet, height from the recipe, sheet baked down to
   128×128 nearest, the archetype clips substituted for the Tripo presets so the character shares
   the society's `work`/`talk`/`sit` set — Tripo's rig is standard humanoid, the clips retarget).
4. Validator passes → the GLB ships as `custom/<agent-id>.glb` referenced by the recipe's
   `model` field; parts still attach (same skeleton), palette strip is present because step 3
   re-UVs the flat regions onto it. Likeness lives in the detail area.
5. If step 3 cannot re-UV cleanly (organic AI topology), the fallback is documented, not
   improvised: keep Tripo's baked texture as a 128×128 detail-only sheet, palette swap disabled
   for that figure (`recipe.paletteLocked = true`, the card says so).

### 8.3 Route B — a new animal (a fox), step by step

1. Model in Blender against `quadruped` rest pose and bone set (§4.3) — or start from a CC0
   quadruped source listed in `SOURCES.md` and re-topologize under budget.
2. Weight-paint to the 22 bones, place `FWD`, origin to the feet, face −Y.
3. Sheet: the quadruped UV template; fur main/shade in cells 11/12, markings in the detail area.
4. Add the target to `contract.json`; `build_figures.py` produces `quadruped-fox.glb`; the
   validator measures `stride_m` and rejects root motion.
5. The recipe of a fox agent: `{ archetype: "quadruped", base: "fox" }`. Done.

### 8.4 Route D — an AI detail sheet

The UV template PNG (`sheets/<archetype>-template.png`: labelled regions, palette strip masked)
is the image prompt's reference; the prompt asks for pixel art within the labelled regions at
128×128, nearest-neighbour, no anti-aliasing. The result runs through a **validate-and-repair
step** (MASTERPLAN §4.2): resize to 128×128 nearest, quantize to ≤ 32 colours, re-stamp the
palette strip from the recipe, force alpha to 1-bit, reject if the face region's contrast is
below a floor. Output is a sheet asset in the recipe, never a new GLB.

---

## 9. Runtime and data model

### 9.1 The recipe (replaces `avatar_uri`)

```ts
interface FigureRecipe {
  contract: 1;
  archetype: "biped" | "quadruped" | "spirit";
  /** base id: "medium" | "small" | "large" for biped; "fox" | "dog" … for quadruped; "gigi" for spirit */
  base: string;
  /** slot → part id; missing slot = nothing worn */
  parts: Partial<Record<PartSlot, string>>;
  /** 16 hex colours, cell semantics per §4.6; derived from AgentPalette when absent */
  palette?: string[];
  /** custom detail sheet (route D) — asset URI, 128×128 */
  sheet?: string;
  /** custom normalized base GLB (route C) — asset URI; parts and clips still attach */
  model?: string;
  /** route C fallback: the sheet cannot be palette-swapped */
  paletteLocked?: boolean;
  /** world scale override in metres (spirit: Gigi 1.9) */
  height_m?: number;
}
```

- Backend: `society_agents.figure_json` (TEXT, JSON, nullable) **instead of** `avatar_uri`.
  This is an M1 schema decision and M1 is being built now — cheaper today than a migration in
  M3 (§12, decision 2). `archetype` joins the five-layer parity set (AP-4) beside `tier`,
  `state`, `msg_type`; the Pydantic model validates cell count, slot names and asset URIs.
- Frontend: `SocietyAgent.avatarUri` → `figure: FigureRecipe | null` in `data.ts`; `null`
  keeps `PlaceholderFigure` until the first base GLB lands; the chat avatar is a 32×32 face crop
  rendered once from the card canvas (`toDataURL`, cached by recipe hash), so the roster rail,
  chat bubbles and the world agree on who is who.

### 9.2 Loading and assembling

`src/components/society/figures/`:

- `figureRegistry.ts` — `import` of every base/part GLB (fingerprinted URLs), keyed by
  archetype/base/part id. The registry is the only module that names files.
- `useFigureAsset(url)` — `useGLTF` (drei cache: one parse per URL, shared by 30 walkers) plus
  the texture re-assertion (nearest, no mips, sRGB) and the `extras` read.
- `assembleFigure(recipe)` — `SkeletonUtils.clone` of the base scene (skinned meshes need a real
  clone, not `Object3D.clone`), part meshes re-bound to the clone's skeleton by bone name, `hides`
  applied, palette strip painted into a `CanvasTexture`. Returns `{ root, mixer, clips, stride }`.
  Pure with respect to React; unit-tested with a tiny fixture GLB.
- `<Figure recipe state velocity />` — R3F component: `useAnimations` over the clone, state →
  clip cross-fades (0.2 s), `timeScale` from §6.3, heading from §6.1, disposal on unmount
  (geometry stays shared, materials/textures per figure are disposed).
- `<FigureViewer />` (card) — the existing `AgentFigureViewer` slot: same `<Figure>`, its own
  small pixel-pass canvas (nearest 240×320 target, `dpr=1`), drag-to-orbit, `idle` + `wave` on
  open, `paused` under reduced motion. It renders through the same pass as the world so the two
  never disagree on the look.

### 9.3 Rendering

- World and card: `RenderPixelatedPass(pixelSize, scene, camera, { normalEdgeStrength: 0.3,
  depthEdgeStrength: 0.4 })` from `three/examples/jsm/postprocessing/`, composer target sized to
  320×180 (world) / 240×320 (card), `dpr={1}`, `frameloop="demand"` when no figure moves and no
  clip plays, `useCanvasAwake` gating the loop. Lights: one hemisphere + one directional, no
  shadows (a 22 px figure has no shadow worth a shadow map; a flat blob decal under the feet reads
  better and costs one quad).
- Materials: `MeshLambertMaterial` (or `MeshToonMaterial` with a 3-step gradient, the
  art-direction pass decides) — never `MeshStandardMaterial` for figures: PBR on a 16-colour
  sheet is wasted fragment cost and muddies the palette.
- Every canvas through `useWebglSurface`; the context gate enforces it.

### 9.4 Performance envelope (documented before M3 ships, MASTERPLAN §7)

30 walkers → ≤ 180 draw calls, ≤ 72 k triangles, 30 `AnimationMixer.update` calls per frame
(≈ 0.3 ms), 30 × 128×128 textures (2 MB VRAM). Target: 60 FPS on the maintainer's integrated
GPU with local inference idle; the VRAM-coexistence policy (world pauses when local inference is
hot) is unchanged. Mixers of off-screen figures (outside the ortho frustum) do not update.

---

## 10. The gate — `scripts/ci/check_society_figures.py`

Stdlib only (a GLB is a 12-byte header, a JSON chunk and a binary chunk; accessors are typed
arrays — no `pygltflib`), runs in pre-commit and CI over `src/assets/society/figures/**/*.glb`,
covered by `tests/unit/ui/test_society_figures_gate.py` with a hand-made 200-byte fixture GLB.
Exit 0 pass, 1 fail, 78 skipped (no assets yet), like the bundle gate.

### 10.1 Checks (each names the rule it enforces)

| # | Check | Rule |
|---|---|---|
| 1 | `extras.jarvis_figure` or `jarvis_part` present, `contract == 1`, archetype known | §4.2 |
| 2 | `FWD` node exists, world z > 0.9 after applying the node chain | §4.1 forward |
| 3 | No node with a negative scale component; every node scale within `1 ± 1e-4` | §4.1 scale |
| 4 | Rest-pose bounds: `min y ∈ [−0.01, 0.01]`, height within the archetype band, feet centroid XZ within 0.05 | §4.1 origin/height |
| 5 | Bone names exactly the archetype set (+ allowed extras), `root` is the sole top bone | §4.3 |
| 6 | Triangles, materials, primitive count under budget; attributes exactly the allowed set; ≤ 4 influences | §4.4 |
| 7 | Every image PNG, power-of-two, ≤ 256, sampler NEAREST/NEAREST, `alphaMode` OPAQUE or MASK | §4.6 |
| 8 | Palette strip present: rows 0–15 consist of 16 flat cells (each cell one colour) unless `paletteLocked` | §4.6 |
| 9 | Base: clip set complete for the archetype, names exact, durations in band, 24 fps | §5 |
| 10 | Every clip: `root` translation XZ constant, no scale channels, `hips` not the root | §5.1 |
| 11 | Looping clips: first and last key equal within 1e-3 per channel | §5.2 |
| 12 | `stride_m` present for `walk`/`run` and consistent with the heel travel the gate re-measures (±10 %) | §4.2 / §6.3 |
| 13 | Part: zero animations, `slot` valid for the archetype, skin joint names ⊆ archetype bones | §4.5 |
| 14 | File ≤ 512 KB (base; nine 30-fps clips on 23 bones weigh ~230 KB) / ≤ 120 KB (part); no Draco/meshopt/basisu extension required | §4.2 |
| 15 | `SOURCES.md` has a row for every file (path, origin, license, sha256 of the source) | §11 |

### 10.2 Frontend tests

- `walkerKinematics.test.ts`: heading formula against the four cardinal directions and the
  diagonals; shortest-arc turning across the ±π seam; heading frozen at rest; reversal turns in
  place; `timeScale` at nominal speed is 1.0 and clamps trigger the clip switch; arrival
  deceleration reaches exactly the target.
- `assembleFigure.test.ts` (fixture GLB): parts bind to the base skeleton, `hides` works,
  palette paint touches only the 16 cells.
- Headless-Chrome screenshot check for the world (recipe in project memory): a walker sent from
  the desk to the gate is screenshotted mid-walk; the test asserts the `FWD`-derived heading
  vector's screen projection matches the movement vector's sign — the automated "not backwards"
  proof, run in CI with the M3 world.

### 10.3 Looking at a figure before it ships

`blender -b --python scripts/figures/preview_figures.py -- --out <dir> [--parts]` renders every base — and, with `--parts`, every base wearing every part its style offers — from four angles, holding a frame of `--clip` (default `idle`). It binds a part to the BODY's skeleton exactly as `assembleFigure` does and deletes the faces of a slot the part `hides`, because a preview that poses the two separately invents bugs: it reported a floating hat and a staff across the shoulders that the runtime never had. Judge in the idle pose, never the rest pose — the rest pose holds the arms straight out.

### 10.4 The Figure Lab (dev-only, the human proof)

`?view=agents&lab=figures` (dev instance only, behind the existing dev flag pattern): every base ×
every clip on a treadmill grid, heading arrows drawn from `FWD`, a palette editor, a part toggle
board, the pixel pass switchable on/off, FPS and draw-call counters. This is where the maintainer
looks at a new character before it enters the parts library; a character that looks wrong here
never reaches the island. No production code path imports it.

---

### 5.1 The third kind of file: a clip library

`asset.extras.jarvis_clips` marks a file that is a skeleton and its clips and nothing else — no
mesh, no sheet, no `FWD`. A figure that borrows from one carries `extras.clips_from` with the
library's filename and the clip FACTS it needs (duration, loop, stride) copied in, so the runtime
reads nothing new. The gate checks the clips on whichever file actually holds them and refuses a
body whose library is missing or belongs to another archetype.

Why: nine clips of a 23-bone rig are ~200 KB, five times a procedural body's geometry. Shipping a
copy inside every look made a new look cost 240 KB and rewrote all of them on every rebuild; one
shared copy makes it 45 KB. Three.js binds a track by node name, so a borrowed clip drives any body
of that rig unchanged. Only the four CC0 adventurer bodies keep their own clips — they were built
before the split and nothing is gained by moving them.

---

## 11. Sources, licenses, people

- **Provenance ledger:** `src/assets/society/figures/SOURCES.md`, one row per shipped file:
  source (own `.blend` path or URL), license, sha256 of the source, what the build changed.
  Gate check 15. CC0 sources need no attribution but get a row anyway; anything under a license
  requiring attribution is ALSO added to `public/THIRD_PARTY_NOTICES.txt` (existing ledger).
- **Allowed inputs:** own work; CC0 (KayKit character animations — 133 humanoid clips for
  `Rig_Medium`/`Rig_Large`, CC0 — and Quaternius' Universal Animation Library 2 — 130+ clips on a
  universal humanoid rig, CC0, `.blend` source — and Quaternius' low-poly animated animals, CC0,
  are the verified candidates for the skeleton + clip base of route A); AI outputs from services
  whose terms grant commercial use of outputs (Tripo, xAI — re-read the terms at import time and
  record the date in `SOURCES.md`). Quaternius' Universal Base Characters (13 k triangles,
  realistic proportions) are the wrong style and budget and are NOT a candidate.
- **Forbidden inputs:** Minecraft skins or any game's asset format/trademark (MASTERPLAN §7), Mixamo
  clips redistributed as files (Adobe's terms allow use inside a project, not redistribution of
  the raw clips — our repo is public), anything "found on Sketchfab" without a recorded license,
  any likeness of a real person without that person's consent.
- **Real people:** stylised pixel likenesses only; consent recorded in `SOURCES.md` (name of the
  consenting person is NOT written — a row id and date are); no public figures, no celebrities,
  ever. A user creating an agent "that looks like me" runs route C on their own photo locally;
  the resulting GLB is user data (`data/society/figures/`, never committed, never uploaded
  anywhere unless a future marketplace lane with its own consent flow exists — M6 at the earliest).
- **Marketplace skins (M6):** the report-then-delist precedent applies; a shared recipe is JSON
  plus optional sheet PNG, never an arbitrary GLB from a stranger (GLB parsers are attack surface;
  a sheet PNG is not).

---

## 12. Decisions the maintainer owns (taken 2026-09-02: all three as recommended)

Taken: (1) KayKit Character Pack: Adventurers (CC0) is the biped skeleton + clip base — MASTERPLAN
§7 now reads "own meshes and textures on a CC0 skeleton with CC0 clips"; (2) the roster row
carries the recipe as JSON (`avatar` in `/api/society/agents`, `figure` in `data.ts`); (3) the
Tripo likeness route ships behind the keyring flow, off by default (F7). The original
recommendations follow for the record.

1. **Skeleton + clip base for `biped` — Recommended: CC0 library (KayKit or Quaternius UAL2),
   normalized by the build; own meshes and sheets on top.** It beats "author every clip
   ourselves" because ~130 clean humanoid clips exist under CC0 today and animating a walk/run/
   sit/work set that does not look robotic is 2–3 weeks of skilled work the month does not have;
   MASTERPLAN §7 says "first-party (own base rig + textures)" — this widens "own rig" to "own
   meshes and textures on a CC0 skeleton with CC0 clips", which needs a one-line §7 edit.
   Runner-up: fully own rig and clips via the build script (route B for everything) — take it only
   if the CC0 clip sets turn out to carry root motion the build cannot strip cleanly (the gate will
   say within an hour of trying).
2. **`figure_json` instead of `avatar_uri` in the M1 schema — Recommended: yes, now.** The M1
   session is writing `society_schema.sql` today; a nullable JSON column costs nothing and spares
   an M3 migration plus a parity change. Runner-up: keep `avatar_uri` and point it at a recipe
   JSON file — works, but puts user content in the asset tree and makes the five-layer parity
   test guess at the format.
3. **Route C budget — Recommended: enable Tripo behind the existing keyring flow with a per-figure
   credit note in the card ("~40 credits"), off by default.** It is the only route that yields an
   exact likeness; runner-up "route D only" (AI sheet on the base body) is free but gives a
   face, not a person.

Open, not blocking: whether the art-direction pass (MASTERPLAN §4.3) wants `MeshToonMaterial`
steps or plain Lambert; whether `run` exists in V1 or every purposeful move walks.

---

## 13. M3 figure work, in order (each step ends in a commit and a green gate)

| Step | Delivers | Proof |
|---|---|---|
| F1 ✅ 2026-09-02 | `contract.json`, `check_society_figures.py` + its unit test (mutation-based: turned, mirrored, blurred, clip-less, root-moving), `SOURCES.md` | gate exits 78 with no assets, 0/1 on real ones |
| F2 ✅ 2026-09-02 | `build_figures.py` on the KayKit Rogue → `biped-medium.glb` (404 KB, 23 bones, `idle walk run work talk sit sleep celebrate wave`, palette-strip sheet) — `proportions.json` and the small/large variants are still open | gate green; the card shows the figure turning |
| F3 ✅ 2026-09-02 (partly) | `figureRecipe`, `figureRegistry`, `assembleFigure`, `AgentFigureViewer` (pixel pass, orbit from above and below, wave on open), the creator's live look editor; the walker maths live in `world/walkerKinematics.ts` (world session). Open: unit tests for `assembleFigure`, the Figure Lab | card and creator render the real figure; palette from the recipe |
| F4 ✅ 2026-09-02 (parts; variants open) | Four bases (Ranger, Knight, Mage, Barbarian) and 15 parts (helmets, hats, capes, sword, shields, staff, spellbook, knife, axe, mug) built from the CC0 pack; `catalog.json` GENERATED by the build with style tags, palettes and credits; hair as its own primitive so headgear hides it; the creator picks style → base → parts per slot; the roster rail shows rendered headshots (`faceCrop.ts`); a person's own GLB imports through `POST /api/society/figures` behind the same gate. Open: `biped-small`/`-large` (`proportions.json`) | a knight in helmet, cape, sword and shield from a recipe alone; an imported figure refused with the gate's reasons |
| F4b ✅ 2026-09-02 | **Every style has a body.** `humanoid_builder.py` models Office and Casual (`modern`), Chibi (`cartoon`) and Android (`scifi`) as box bodies on the CC0 donor's rig and its nine clips — the donor is imported for the skeleton alone and every mesh it ships is deleted — plus four accessories (backpack, tablet, wrench, cap) hung on bones every biped shares, so one file fits every base of its style. The Ranger is `fantasy` only again. Parts are filtered BY STYLE, the base carries its archetype into the recipe, a switch drops what the new base cannot wear, and the height band follows the archetype | the creator opens on a modern person, and every style tab yields a figure |
| F4c ✅ 2026-09-02 | **A wardrobe, not a sample.** 26 bases and 50 parts: five modern bodies, three cartoon, four sci-fi, seven fantasy (four CC0 + Dwarf/Elf/Monk) and six animals; nine slots filled with hats, faces, coats, belts, packs and props. `Profile` and `Shape` grew the knobs that tell one look from the next, so a new body is an entry in a dict. **The clips ship once**: a rig's nine (or seven) clips are ~200 KB against a procedural body's 40 KB of geometry, so `biped-clips.glb` and `quadruped-clips.glb` carry them and a body names one in `extras.clips_from` — 240 KB a look became 45 KB. A part cut to a skull carries `fits_family`, so a cap for this module's crown is never offered on the wider KayKit head even though the two now share `fantasy`. `spirit` left the creator: there is one Gigi and he is Jarvis on the map | every style opens on several builds and fills at least three slots; the gate reads 78 files |
| F5 ✅ 2026-09-02 | `spirit-gigi.glb`: Gigi modelled and keyed BY SCRIPT in Blender (`scripts/figures/gigi_builder.py` — body extruded from the mascot's SVG outline, eyes/mouth/scanlines/glitch pixels/tube arms, rig `root → body → face`, clips idle/walk-glide/talk/sleep/celebrate-spin/wave, two primitives: lit body + unlit `-marks`), 282 KB. `quadruped-fox.glb`: `quadruped_builder.py` writes the 21-bone rig, the body AND all seven clips — there is no CC0 donor of this archetype — plus a collar. Jarvis' default recipe is Gigi; the roster seeds only Jarvis | Gigi floats on the market square; the fox walks, sits and sleeps on the treadmill |
| F6 ✅ 2026-09-02 (partly) | The island's walkers wear the real figure (`FigureRig` inside the world session's `WalkerFigure`), driven by the sim's mode and speed with the stride from the asset, at **hero scale 1.6×** — seen 50° from above at 64 m a true-scale person is a 20 px sliver, and every readable isometric game cheats the same way (the card keeps true scale). Open: mixer culling off-screen, the headless "not backwards" screenshot check | Jarvis walks the market square in his suit with his mug |
| F7 | Route D sheet generation + validate-and-repair; route C helper (`tripo_fetch.py`) + custom-model normalization; card "Change avatar" | a consented likeness walks to the desk |

F1–F3 are independent of the terrain (the treadmill is a flat plane) and can start the day the
decisions in §12 are taken, in parallel with M3's art-direction pass.
