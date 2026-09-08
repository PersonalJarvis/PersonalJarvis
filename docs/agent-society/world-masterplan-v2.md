# World Master Plan v2 — from the first island to a professional game world

> Rendering, camera, owned assets and locomotion are superseded by [Pixel Diorama](pixel-diorama.md), accepted 2026-09-07.

Status: **binding since 2026-09-02** — the maintainer took the four recommended decisions of §9
(grain off by default, one house per agent, the 15 hubs, the scripted Blender World Kit); this
document succeeds [`world-art-direction.md`](world-art-direction.md) for everything it covers. Subordinate to
[`MASTERPLAN.md`](MASTERPLAN.md) (§2 decisions stay untouched: the world is a projection of the
society event stream, idle is LLM-free, dispatch is the scheduler's privilege).

The maintainer's brief, in one line: *"Roblox-style but really modern, a Clash-of-Clans-grade
village; the app's marketplaces and hubs become real 3D shops the figures walk to when they use
skills; a hub where new agents walk out; everything steerable; the most professional level you can
picture."*

---

## 1. Where V1 stands, and the gap to the target

V1 (commit `a70e24853`) proves the pipeline end to end: deterministic island, village ring, four
quarters, walkers, navigation, minimap, lazy chunk, WebGL discipline. Held against a Clash of Clans
village or a polished Roblox experience, the gap is not the layout — it is **material and light**:

| What the eye reads | V1 today | CoC / modern Roblox | Fix category |
|---|---|---|---|
| Surfaces | flat Lambert, one colour per face | soft gradient shading, ambient occlusion in every corner, rim light on edges | lighting + baked AO (§3) |
| Shadows | none | soft contact shadows under every object — THE depth cue of the CoC look | shadow map + blob decals (§3) |
| Edges | sharp box edges | bevelled, rounded — light catches the bevel | kit geometry with bevels (§4) |
| Buildings | primitives (boxes, half-cylinders) | authored models with silhouette, trims, signs, props | the World Kit (§4) |
| Ground | two-shade checker tiles | painted ground with worn paths, grass tufts, flower decals, stone rims | ground atlas + decals (§3) |
| Water | scrolling pixel texture | depth tint, foam line along the shore, specular sparkle | shader + shoreline mask (§3) |
| Life | 3 walkers, no props | banners, smoke, lanterns glowing, birds, carts, particles when something happens | props + effects (§5) |
| Pixel grain | 2 px, everywhere | none (CoC) or a soft film look | make the grain optional (§9-1) |
| Interaction | click a figure | hover glow, click a building → it opens, cards, follow-cam, steering | §6 |

The pixel pass was the right first move (it also carries the GPU budget), but the reference the
maintainer now names is **clean and smooth**, not pixelated. The medicine for WebView2 stays the
same — render at a reduced internal resolution — the *look* changes from "nearest-filtered retro" to
"soft, stylised, well-lit".

## 2. Visual target — the style guide in one screen

**Shape language:** chunky, rounded, generous. Every edge has a visible bevel (2–6 cm on a 6 m
house). Roofs overhang. Nothing thin (pillars ≥ 0.5 m, rails ≥ 0.15 m) — thin things flicker.
Proportions slightly toy-like: doors a bit too big, windows a bit too round, trees a bit too round.

**Colour:** the V1 palette is right in hue (`worldPalette.ts`), wrong in *range*. CoC uses one
saturated hue per material with a strong light/shadow ramp (≈ 25 % lightness span) and a warm key
light / cool ambient split. Rule: every material gets three stops — lit, mid, shade — baked into the
kit's palette atlas; the sun adds the fourth (specular/rim) at runtime.

**Light:** one warm sun from the south-west at ~55° elevation (long soft shadows toward the
north-east, where the camera looks from), sky-blue ambient from above, green bounce from below.
Emissives (glass, lamps, holograms, the beacon) get a small bloom. Evening mode later shifts the
sun to orange and turns the lamps on — the same scene, two moods.

**Camera:** 50° pitch, 45° yaw, orthographic, five zoom steps — the CoC camera within a few
degrees, and what makes shadows and rooflines read. Those two angles are the **starting** view,
not the only one: a right-button drag orbits the island (yaw all the way round, so any building
can be seen from behind; pitch between 20° and 80°), Q / E step a quarter turn, and the HUD
compass points at north and clicks back to the designed view. Nothing about the island moves
with it — the houses keep the headings the layout designed, the sun keeps shining from the
south-west, and only the picture turns. The view is not persisted; `?world=x,z,zoom,yaw,pitch`
carries one when it is worth sharing.

**Surface at 1:1:** no visible pixel grain by default. A `grain` setting keeps the V1 look as an
option (0 = off, 2 = V1, 3 = coarse); the pixel pass then also becomes the cheap-GPU fallback.

## 3. Rendering plan — how the look is achieved on an integrated GPU

All of this is three r0.185 + R3F v8, already shipped; no new engine.

1. **Internal resolution scale** instead of pixelation: render to a target at 0.66–0.75× of the
   canvas (a WebGL render target with *linear* filtering, upscaled with a light sharpen) — the
   same fragment saving as today's pixel pass without the stair-steps. `PixelPass.tsx` becomes
   `WorldComposer.tsx` with passes: scene → (optional GTAO half-res) → bloom (half-res, threshold
   0.85, strength 0.35) → upscale/sharpen → output. Grain > 0 swaps the upscale for the nearest
   blit.
2. **Shadows:** one directional light with `PCFSoftShadowMap`, 2048² map, orthographic shadow
   camera that **follows the view** (fit to the visible ground rectangle from
   `visibleGroundCorners`) — sharp shadows everywhere the player looks, none rendered off-screen.
   Terrain receives, kit objects cast + receive, walkers cast. Trees cast via their canopy blobs.
   Budget: one shadow pass ≈ +25 % draw calls; fine under 300.
3. **Ambient occlusion:** *baked*, not screen-space. The World Kit bakes AO into vertex colours
   (Blender bake at build time, §4); the terrain gets a cheap analytic AO — tiles adjacent to a
   higher step or a building footprint darken 10–15 % in `terrainGeometry.ts`. GTAO at half-res
   stays an opt-in "high" setting; on the iGPU it costs more than it gives.
4. **Materials:** `MeshToonMaterial` with a 4-step gradient map for kit and figures (the CoC ramp),
   `MeshLambertMaterial` for terrain, `MeshBasicMaterial` for emissives. Still no PBR: the
   palette atlas carries the colour, the ramp carries the light.
5. **Ground:** replace the two-shade checker with a **ground atlas** (256×256, 8 tiles: grass ×3,
   path, plaza stone ×2, sand, garden) sampled with per-tile UV offsets baked into the merged
   geometry — the terrain stays ONE draw call. Decals as a second, alpha-masked mesh layer:
   flower clusters, worn dirt at doors, path edges, plaza pattern (a ring mosaic around the
   tree), tufts of grass (crossed quads, instanced, ~2000).
6. **Water:** a small custom shader: depth tint from a shore-distance texture baked from the tile
   map (`islandLayout` knows every coast tile), animated foam line, two scrolling normal-like
   noise layers for sparkle, no reflection. One plane, one draw call.
7. **Sky:** a vertical gradient (`SKY.clear` top → warm haze at the horizon) as the clear colour is
   enough in orthographic; clouds as a few large soft sprites drifting over the island at height
   60 m casting *fake* shadows (dark soft decals moving on the ground) — a signature CoC touch,
   nearly free.
8. **Effects, all sprite-based and pooled:** chimney smoke puffs, sparks at the workshop when a
   run is hot, a soft glow pulse on a building when it receives an event, confetti burst on
   `RESULT`, "zzz" on paused agents, footstep dust on sand. One `InstancedMesh` of billboarded
   quads per effect type; ≤ 400 particles alive.
9. **Budget (documented, measured before M3b ships):** ≤ 300 draw calls, ≤ 400 k triangles,
   1 shadow pass, 1 half-res bloom, 30 walkers → 60 FPS at 1600×1000 on the maintainer's
   integrated GPU with local inference idle. The VRAM-coexistence rule from MASTERPLAN §7
   stands: when local inference is hot the composer drops to scale 0.5 and disables bloom.

## 4. The World Kit — real assets, built the way the figures are built

Primitives got us to V1; the target needs authored geometry. The answer is a **modular building
kit** produced by the same discipline as the character pipeline: one headless Blender script, one
contract, one validator, GLBs in the tree.

### 4.1 Kit contract (v1)

```
jarvis/ui/web/frontend/src/assets/society/world/
  kit/          one GLB per module (walls, roofs, doors, signs, props) and per assembled building
  ground/       ground atlas, decal atlas, shore mask
  SOURCES.md    provenance (own .blend, CC0 kits, AI outputs with terms + date)
scripts/world/  build_world_kit.py (Blender), kit.json (module list, footprints, anchors)
scripts/ci/check_world_kit.py   the gate
```

Every building GLB carries `extras.jarvis_building`:
`{ contract: 1, id, footprint: [w, d] tiles, forward: "+Z", door: [x, z], stand: [x, z],
sign: [x, y, z], lights: [...emissive node names], height_m }`. Origin at the ground centre of
the footprint, door on local +z (toward the street), the `stand` point is where a figure stands
when "at" the building, `sign` is where the DOM label hangs. Budgets: ≤ 3 000 triangles per
building, ≤ 200 per prop, one palette-atlas material per object (+ one emissive), baked vertex AO,
bevels ≥ 2 cm. The validator checks all of it (footprint against `islandLayout`, origin, forward,
budgets, atlas, AO present).

### 4.2 Where the models come from — and the Blender MCP question

**The honest answer on Blender MCP.** The Blender MCP is a bridge that lets an AI session drive a
*running* Blender through its Python API: create and edit meshes, set materials, import assets
from Poly Haven / Sketchfab / poly.pizza, request AI-generated meshes (Hyper3D Rodin, Hunyuan3D),
and take viewport screenshots to check the result. It is **not** a standard tool in professional
web development. The professional pipeline is: an artist (or a script) builds in Blender → exports
glTF/GLB → `gltf-transform` optimises (dedupe, quantize, Draco/meshopt, texture resize) → three.js
/ R3F loads it. The MCP shortens exactly one step of that chain: *getting the Blender side done
without a human at the keyboard*. Used well, it is an inner loop, not a build dependency
(character-pipeline.md §7 made the same call).

What it is good for here:

| Use | Verdict |
|---|---|
| Developing `build_world_kit.py` interactively — run a step, screenshot, adjust bevels/proportions, then commit the script | **Recommended** — this is the whole point; the committed script rebuilds everything headless on any OS |
| Importing CC0 props from Poly Haven / poly.pizza as starting points (lanterns, crates, plants), then restyling to the atlas | Good, with a `SOURCES.md` row per import |
| AI mesh generation (Hyper3D / Hunyuan3D) for architecture | **Not for buildings** — generative meshes are organic, un-modular, off-palette; cleanup costs more than modelling. Acceptable for one-off statues/decor after retopo |
| Sketchfab downloads | Only CC0 / CC-BY with a recorded licence; most are not |
| Anything at build or runtime | Never — the MCP needs a GUI Blender; CI runs `blender -b` |

So: the shops get **modelled by script**, kit-style (a wall module, a roof module, a glass band, a
solar barrel, a sign frame, an awning), assembled per building in `kit.json`, with the MCP as the
fast way to *see* what the script produces while it is written. That gives a consistent
solarpunk-modern kit, reproducible, licence-clean, and every future shop is a JSON entry plus at
most one new module.

### 4.3 CC0 kits worth mining for modules (verified candidates, terms re-read at import)

KayKit (city / medieval / furniture bits, CC0), Kenney (city kit, nature kit, CC0), Quaternius
(stylised buildings and props, CC0). None matches solarpunk-modern as-is; they are *proportion and
silhouette references* and prop donors, restyled onto our atlas. No game trademarks ever.

## 5. The market — every hub of the app as a building, and what happens there

The app's sections **are** the society's economy. The market district turns them into places with
a silhouette, a job for the agents, a job for the player, and live signals. Two rings:

- **Inner ring — the hubs** (the "shops"), around the square, on the spokes' inner ends.
- **Outer ring — the homes**: **one house per agent**, created with the agent, styled from its
  palette and figure recipe. An agent sleeps at home when paused, leaves in the morning when a
  routine fires, and the house *is* its model card (click the house = click the figure). The
  comic-village ring gets its meaning: a village of villagers, each with a hut.

| App section | Building | Signature silhouette | Agents go there when… | Player click | Live signals |
|---|---|---|---|---|---|
| Skills & Tools → **Skills** | **Skill Forge / Academy** | tall hall with a glass "book" atrium and a chimney | a run's dominant capability is a skill; a learned skill awaits approval (AP-15 draft) | opens `?view=skills`; drawer lists skills in use right now | forge glow when in use; a flag per pending draft skill |
| Skills & Tools → **Plugins** | **Plugin Docks** | a row of loading bays with roll-up doors, one per installed plugin | a plugin tool is in use (Gmail, calendar, …) | opens plugins; hover a bay = plugin name | bay door open + light when its plugin is active; red lamp on error |
| Skills & Tools → **MCPs** | **Relay Tower** | slim tower with a rotating dish and cable spans to the docks | an MCP server call | opens MCPs | dish spins during calls; connection state as ring colour |
| **CLIs & CLI Test Hub** | **Terminal Cantina** | café with a long counter of glowing terminals under an awning | a coding session runs on a CLI seat (Claude Code, Codex, …) — the agent sits at a terminal | opens the Agentic IDE / that session | one terminal lit per live session, provider mark on the awning |
| **Marketplace** | **Bazaar** | open market stalls in the square's outer band, canopies in provider colours | a package is being installed/published | opens the marketplace; a stall = a category (skills, plugins, wallpapers) | new arrivals as crates; a "Featured" banner |
| **Local models** | **Model Foundry** | round power-plant with cooling fins and a core light | local inference runs (the agent's brain is local) | opens local models | core glows by load; smoke when hot (also the VRAM-degrade trigger) |
| **API Keys** | **Vault** | small solid bank with a round door | never (agents do not touch keys — AP-2/AP-12) | opens API keys | door lamp green when the tier has a working key |
| **Wiki / memory** | **Archive** (exists) | round tower, dome | knowledge writes / reviewed promotion | opens the wiki | lamp when writing; a queue of scrolls for unreviewed knowledge |
| **Automations** | **Clock Tower** | tower with a visible dial and bell | a routine fires (the agent walks out of its house, bell rings) | opens automations | dial shows the next fire; bell swing on fire |
| **Artifacts** | **Gallery / Warehouse** | long hall with skylights and display windows | a `RESULT` is stored (the agent carries a crate there) | opens artifacts filtered to that run | windows show the newest three artefact thumbnails as sprites |
| **Spend** | **Counting House** | small office with a coin sign on the square | never | opens spend | today's cost on the sign (from the ledger — never a second meter) |
| Approvals (queue) | **Harbor Gate** (exists) | gate over the dock | an `ask`-tier action waits — the agent stands at the gate | opens the approvals drawer | lantern amber per waiting approval; badge count |
| Agent creation | **Agent Foundry** (new) | modern hall with a large front door and a conveyor ramp | **a new agent is created here and walks out of the door to its new house**; avatar changes happen inside | opens the create dialog / the avatar workflow | door opens, light spill, a short fanfare particle burst |
| Lead / voice | **Jarvis Hub** (exists) | dome + beacon | Jarvis idles here; the beacon pulses with the voice orb | opens Jarvis' canonical chat | beacon = orb state (listening / speaking / thinking) |
| Bounded rooms | **the square's table** (exists) | tree, ring bench, long table | `ROOM_OPEN → SAY* → ROOM_SETTLE` — members sit, bubbles show the round counter | opens the room transcript | seats fill; "Runde 2/3" tag over the table |
| Sessions / Transcription / Board / Contacts / Docs / Settings | *not buildings* | — | — | these stay in the sidebar; not every section deserves a roof — a village with 20 shops reads as a mall | — |

Placement: hubs the agents visit most (Forge, Docks, Cantina, Archive, Foundry) sit closest to the
square; the Vault, Counting House and Clock Tower frame the square's north side beside the hub;
the Bazaar's stalls ring the square's outer band; the Model Foundry stands in the north-west field
(where the solar panels already are — the energy quarter); the Gallery on the south spoke toward
the harbor. `islandLayout.ts` gains a `BUILDINGS` table (id, tile anchor, rotation, kit id,
section id) and the plots move from hard-coded rectangles to kit footprints.

## 6. Behaviour — the connections ("when a figure walks somewhere, why")

The backend owns the *semantic place*; the world decides how to show it (MASTERPLAN §2.7). V1 has
five checkpoints; the market needs a richer but still tiny vocabulary:

`checkpoint ∈ home | square | hub:<building-id> | gate | archive | foundry | wander`

**Derivation rule (trusted Python, in the society bridge, no LLM):** a running worker's
checkpoint is `hub:<X>` where X is the building of the **dominant capability family** of its last
N = 8 tool calls (plugin → Docks, cli → Cantina, mcp → Relay, skill → Forge, core file/shell work →
Workshop). It only changes when the family changes for ≥ 20 s (hysteresis) — a figure never
ping-pongs between two shops. `RESULT` → `archive` (carry a crate to the Gallery/Archive) then
`home`. `HOLD`/approval → `gate`. `ROOM_OPEN` → `square`. `paused` → `home` (sleep). A new
agent → `foundry` for 3 s → walks to `home`. Idle → `wander` (rest-biased, near home and square).

**Choreography queue (client):** events arrive in bursts; the walker walks to the *latest*
checkpoint, shows a digest bubble for the skipped ones, and never teleports. Speech bubbles carry
the envelope's `text` (typed protocol, §2.1), attributed and clickable → the feed thread.

**Steering the figures (the maintainer asked for figures the user can direct):** the world never moves a
figure directly — that would desync it from the truth. Instead every world gesture issues a typed
event and the figure follows because the state changed:

| Gesture | Event | Who may |
|---|---|---|
| click figure → card → "Assign task" (text) | `ASSIGN` via the scheduler (tier wall applies) | user always; lead/orchestrators |
| drag a figure onto a hub building | `ASSIGN` pre-filled with that building's capability focus ("use the Gmail plugin to …") — the card opens with the focus set, user confirms | user |
| right-click figure → "Go home / Pause" | `state = paused` | user |
| voice: "Jarvis, schick Scout in die Werkstatt und lass ihn …" (i18n-allow: quoted German voice example) | the router's `delegate-to-agent` (M4) | user via Jarvis |
| click a building → "Send someone here" | picks an agent → same as drag | user |
| follow-cam button on a figure | camera only (client) | anyone |

This keeps MASTERPLAN §2.5 intact (no spawn tools, scheduler-only dispatch) while the world feels
like a game you steer.

## 7. Interaction and polish that professional worlds have

- **Hover**: outline glow on buildings and figures (a second pass with an `OutlinePass` on the
  hovered object, or a cheap emissive lift), cursor changes, a small tooltip with name + state.
- **Click a building** → the section opens as a **drawer over the world** (one-viewer doctrine:
  no page navigation; the island keeps living behind it), with a "open full section" link.
- **Follow-cam**: pick a figure, the camera keeps it framed; the minimap shows the selection.
- **Time of day** as a flavour switch (day / evening) and, later, the real clock (opt-in).
- **First-run**: the camera flies from the harbor to the square once (3 s), labels fade in.
- **Sound** (off by default, per-world toggle): ambient birds/water, a soft chime on `RESULT`, a
  bell on routine fire. All local files, all optional.
- **Accessibility** unchanged: reduced motion freezes the island; the Ledger stays the declared
  equivalent; every building and figure is also reachable by keyboard through the rail.
- **Two windows**: the island is deterministic and the state comes from the log, so a second
  window shows the same village with the same agents at the same places; footsteps differ by
  design (§2.7).

## 8. Roadmap — M3 in four slices (each ends in a commit, a screenshot check and a green gate)

| Slice | Delivers | Proof |
|---|---|---|
| **M3a Look** (this week) | composer with resolution scale + bloom + optional grain; sun shadows following the view; toon ramp; analytic terrain AO; ground atlas + decals; shore-foam water; sky gradient + cloud shadows | the V1 screenshots re-taken: same island, CoC-grade light |
| **M3b Kit** | `build_world_kit.py`, contract, validator, atlas; first 10 modules; 6 buildings rebuilt from the kit (hub, house ×3 variants, workshop, archive) | validator green; the village rebuilt without one primitive box |
| **M3c Market** | the hub table of §5 in `islandLayout.BUILDINGS`; Foundry, Forge, Docks, Relay, Cantina, Bazaar, Clock Tower, Gallery, Vault, Counting House, Model Foundry; one house per agent; building click → drawer; live signals from the section APIs that already exist | every sidebar hub has a roof; clicking it opens the drawer |
| **M3d Life** | checkpoint vocabulary + derivation rule in the bridge (T3: joins the five-layer parity set); choreography queue; bubbles; steering gestures → `ASSIGN`; effects; follow-cam; first-run fly-in | a real mission plays out: the agent leaves home, works at the Docks, carries the result to the Gallery, sleeps |

Figures (character-pipeline F1–F7) run in parallel in the sibling session; `WalkerFigure` is swapped
for `<Figure>` the day F3 lands. The Ledger, reduced motion and no-WebGL fallbacks stay as they
are.

## 9. Decisions (1–4 taken by the maintainer on 2026-09-02, the recommended option each time; 5–6 default until revisited)

1. **Pixel grain — Recommended: off by default, kept as a setting.** The named references (Roblox,
   Clash of Clans) are smooth; the grain then serves as a look option and as the low-GPU fallback.
   Runner-up: keep grain 2 as the identity — cheaper, but it caps the fidelity every other item
   here buys.
2. **One house per agent on the ring — Recommended: yes.** It gives the comic-village ring its
   meaning, makes "paused" and "routine fired" visible, and doubles as the card's door. Runner-up:
   fixed decorative houses — simpler, but the village stays a stage set.
3. **Shop list — Recommended: the 15 buildings of §5, no roof for Sessions/Transcription/Board/
   Contacts/Docs/Settings.** Runner-up: every sidebar section as a building — reads as a mall.
4. **Assets — Recommended: the scripted Blender World Kit (§4), Blender MCP as the inner loop,
   CC0 props restyled onto the atlas, no AI-generated architecture.** Runner-up: keep building from
   primitives with bevels and AO — reaches "clean", never "authored".
5. **Steering — Recommended: gestures issue typed events (§6), starting with card "Assign task"
   and drag-to-hub in M3d; direct figure control never.** Runner-up: client-side "walk here"
   commands — feels immediate, desyncs from the truth, contradicts MASTERPLAN §2.7.
6. **Sound — Recommended: off by default, ambient + two chimes behind a toggle, M3d.**
