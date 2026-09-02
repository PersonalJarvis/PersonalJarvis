# World Art Direction — the island

Status: **binding for M3 (world), decided with the maintainer on 2026-09-01/02.**
Subordinate to [`MASTERPLAN.md`](MASTERPLAN.md) §4.1/§4.3; this is the art-direction pass the
master plan requires before environment assets are built. Figures are governed by
[`character-pipeline.md`](character-pipeline.md). Code: `jarvis/ui/web/frontend/src/components/society/world/`
(model in `islandLayout.ts`, colours in `worldPalette.ts`).

## 1. The picture in one paragraph

A bright pixel island in the warm light of a late afternoon — the softness and colour of a
cosy farming game rendered in true 3D — on which a small **solarpunk village** stands: white walls,
glass bands, solar barrel roofs, garden roofs, wood accents, rounded shapes. The village is laid
out like a classic comic village: a ring of houses around ONE open square where everyone gathers,
the biggest house at the head of the square. Around the village, four quarters carry the working
places of the society. Seen steeply from above, lightly pixelated, never dark, never monochrome.

## 2. Decisions (maintainer, 2026-09-01/02)

| # | Question | Decision | Why it beat the runner-up |
|---|---|---|---|
| 1 | Look | **Colourful pixel island** (cosy-farming-game softness in 3D) | matches "bright, beautiful, lightly pixelated"; a block diorama or clean low-poly would lose the warmth |
| 2 | Camera | **Steep bird's-eye: 50° below the horizon, 45° dimetric yaw**, orthographic | reads as "from above" yet keeps house fronts and figure silhouettes; 30–35° gives more façade but less overview |
| 3 | Size | **16 × 16 fields (512 m); the central 4 × 4 fields are the market district** | the maintainer wants a big island that is not seen in one screen — grown from 10 × 10 on 2026-09-02 ("at least 50 % bigger"); one field = one screen at the closest zoom |
| 4 | Landscape | **A village / small town** on an island of **biomes at different heights**: a snow-capped mountain, terraced hills, alpine meadows, a rocky cape, a tropical cove with a lagoon, the harbor bay, orchards, a dark forest | free-text decisions: "a village, ultramodern, future-oriented" (2026-09-01) and "not flat — biomes, heights, spectacular, never overloaded" (2026-09-02) |
| 5 | Architecture | **Solarpunk village** — white + glass + solar + gardens + wood, lots of green between | colourful AND futuristic; pure factory grey or neon-cyber contradicts the bright island |
| 6 | Centre | **Comic-village ring structure, modernised**: houses around the open square, the big tree with the long table in the middle, the lead's hub at the head (north) | the maintainer's own image ("Asterix-style village structure, only modern") |
| 7 | Pixel grain | **Fine — 2 screen pixels per rendered pixel** (a 1280-px stage renders at 640 px) | figures and signs stay readable; 320×180 would be too coarse for a big island |
| 8 | Navigation | **Drag + arrow keys/WASD, five fixed zoom steps (32 / 64 / 128 / 256 / 512 m), minimap with relief** | 256 fields need zoom to find an agent; fixed steps keep pixels crisp; the widest step is the postcard of the island |

Decided by the build, open to the maintainer: fixed warm-afternoon light in V1 (a real-clock
day/night cycle is a later flavour); animated pixel water; the hub carries the pulsing beacon
that will map onto the voice orb.

## 3. Layout

Units: 1 world unit = 1 m; one tile = 2 m; the island is 256 × 256 tiles (512 m). World origin
is the island centre; +x east, +z south. The camera looks toward the north-west, so the tall
things stand at the back (north-west) and the low, bright things in the foreground (south-east).

```
   MOUNTAIN (snow cap)      N  archive HILL      ALPINE meadows, flower fields
   solar terrace            |
                            |
W  dark FOREST  (workshop)—[ MARKET PLATEAU ]—(lighthouse on the rocky CAPE)  E   · islets
                            |
   ORCHARD meadows          |            tropical COVE: lagoon, sandbar, palms, gardens
                            S  harbor BAY, dock, breakwater
```

**Biomes, as tile kinds:** sand · grass · meadow · forest · alpine · heath (heather drifts over
the high moor) · dry (savanna on the eastern lowland) · farm (crop rows, south-west) · marsh
(pools and reeds on the south-western coast) · scree (gravel on the mountain's flanks) · rock ·
snow · quarry (the mine's forecourt). Plain grass is kept to the village plateau and the
lowland around it; everything further out belongs to a named biome.

**Terrain:** a continuous height field (`islandLayout.heightAt`) — a radial fall-off plus one
designed bump or dip per biome (`REGIONS`) plus a little noise — quantised into ten levels
(`LEVEL_Y`, 0.35 m at the beach to 12.6 m at the peak). Every slope becomes a terrace, every
steep place a cliff. The tile kind follows level and region: sand → grass / meadow → forest (west)
→ alpine (high, north-east and the mountain) → rock → snow (level 8+). Flower fields are drifts of
`garden` tiles over the meadows. Offshore: four rock islets with a green crown, the breakwater in
the bay's mouth, the lagoon (sea level, enclosed by a sandbar, a channel to the sea, a jetty).

**Roads are graded:** each spoke is walked outward from the square and clamped to ±1 level per
tile, shoulders included, so a walker can climb to the archive hill or the cape; cliffs of two
steps or more are real barriers (`findPath` refuses them). Every place's plot is flattened to its
own terrace level (`PLOT_LEVEL`): the archive on the hill at 5, the lighthouse knob at 6, the
harbor and the gardens low at 2, the solar field on the mountain's foot terrace at 4.

**Market district (a round plateau of radius 39 tiles, level 3):**
- open square, radius 13 tiles, paved; garden beds alternate around its rim;
- the Quest Board monument in the exact centre, a ring bench around it, the long table on the
  south side — this is the `meeting` checkpoint (MASTERPLAN §2.7);
- the house ring at radius 19 tiles; the four cardinal directions stay open as gates; the ring
  hubs take their slots (`RING_KIT_SLOTS`), the hub's podium the two slots flanking the north
  gate — the remaining slots hold houses, three variants cycling: solar-barrel roof, garden
  roof, glass loft;
- **which way a house faces** (`houseDefaultRotation`, maintainer 2026-09-02): the door faces
  the square — unless that turns the house's back on the camera, which looks from the south-east
  (`CAMERA_FROM`); then the door faces the ring road instead. A door the viewer never sees is a
  house with its back turned. The houses stand in the south-east half of the ring (the hubs took
  the north and west), so nearly all of them face the road; a house on the far side would keep
  its door on the square;
- **the viewer may turn any house or ring hub** (`buildingPoses.ts`, `RotateHandle.tsx`): click a
  house (or open a hub's drawer) and a knob appears on the front of its selection ring; drag the
  knob around the building and it follows, Shift snaps to 15° steps, and close to the designed
  heading it snaps back. The badge beside the knob shows the heading and, once turned, a Reset;
  the HUD offers "reset all". Headings are per viewer (localStorage), never synced, and go
  straight into the island model (`applyBuildingYaws`): the blocked tiles, a hub's stand tile
  and facing all move with the building, and walkers mid-route re-plan;
- **nothing walks through furniture:** houses and hubs block their footprints plus a 0.5 m wall
  margin; the monument's plinth and ring bench block 6 m around the centre and the long table
  its rectangle; the hub's wings, colonnade, planters, pool and flag masts, the harbor kiosk and
  gate pillars, the keeper's hut, every lamp post and every hedge segment block their tiles
  (`blockSquareFurniture`, `blockLandmarkFurniture`, `buildIsland`). A figure that finds a
  building turned over its head steps to the nearest free tile first (`nearestWalkable`);
- the **hub** (lead agent) at the head of the square, north, on a **podium one level up**
  (18 × 10 tiles): a 24 × 12 m hall with a glass band and garden roofs, a set-back upper floor,
  the glass atrium under the dome, the beacon spire with its halo, two solar-roofed wings, a
  colonnade over the entrance, the grand stair down to the square, the reflecting pool and two
  flag masts at its foot;
- ring road at radius 25 (it climbs the podium's back terrace instead of vanishing under it),
  hedge ring with four gates at radius 29, lamps along the spokes;
- four spokes (width 3 tiles) from the square to the quarters.

**Quarters and checkpoints** (backend place → island place, `Walkers.tsx`):

| checkpoint | place | building |
|---|---|---|
| `desk` | workshop (west, a clearing in the forest) | long hall, sawtooth skylights, wide door toward the village |
| `meeting` | market square | the table under the big tree |
| `archive` | archive (north, on the terraced hill) | round tower, two glass bands, blue dome |
| `gate` | harbor gate (south, the bay) | gate pillars over the dock into the bay, kiosk, two moored boats, buoys, breakwater |
| — | lighthouse (east cape, on the rock) | striped tower with rotating lamp, keeper's hut |
| — | gardens (south-east, the cove's low terrace) | six glass greenhouses on garden tiles |
| — | solar field (north-west, the mountain's foot terrace) | 35 tilted panels on posts |
| — | the mine (the mountain's south-eastern flank, off the north road) | timber portal in a cliff, lanterns, rails, ore cart, headframe, foreman's hut |

Idle agents (`idle`) wander inside the square with the rest-biased model (`wander.ts`);
paused agents stand still.

**Light and life:** lamps along the spokes, around the square's rim, along the ring road, the
dock and the mine's road — every lamp throws an additive pool of light on the ground; festoon
strings over the square from eight poles; the lighthouse's two sweeping beams; lanterns on the
mine's portal; a campfire on the cove's beach; lights on the buoys; the hub's halo beacon.

**Sea:** a subdivided plane with real waves — three swells plus breakers that build as the
bottom rises (from the shore-distance field), lit by the sun with a glint, whitecaps on the
crests, a breathing foam line, and a wash sheet that runs up the beach and drains back.

**Vegetation per biome** (`treeChoice`): round trees on grass and meadow (dense and taller in the
forest), pines on the alpine meadows and the high slopes, palms on the cove's sand — thickest
around the lagoon; boulders on the high ground, at the foot of cliffs and on the beaches.

## 4. Palette

Everything inside the viewport comes from `worldPalette.ts`. Never a theme token, never Ink &
Paper, never the Cursor design doc (MASTERPLAN §4.3). Two shades per terrain kind give the
tile-art flicker under the pixel pass.

| Role | Colours |
|---|---|
| sky / clear | `#a5dbff` |
| sea | shallow `#72d3e2`, surface `#44a0dd`, deep `#2f7fc4`, abyss `#1e5c9a`, ripple `#9fdcf6`, foam `#e4f6ff` |
| sand | `#f3e2ad` / `#ead597` |
| grass | `#7ccb5c` / `#6fbe51`; meadow (high ground) `#97d46c` / `#8ac860` |
| forest floor | `#4f9e47` / `#47923f`; alpine `#bcd97c` / `#aecf6f`; snow `#f6f9fc` / `#e9eff6` |
| rock | `#a8a7b3` / `#9a99a6`, faces `#6f6e7c` |
| square / paths | `#ece1cf` / `#e2d5c0`; paths `#dcc9a5` / `#d0bd97` |
| cliff faces under grass | `#8e6a44` |
| walls | `#f7f3ea` / `#e6e0d2`, trim `#c9c2b2` |
| glass | `#8ed2f0` (unlit — it glows) |
| solar | `#26375a`, seams `#3e5f95` |
| wood | `#b57f45` / `#8c5e2f`, doors `#e0893b` |
| lead accent | `#2f6f8f` (the Jarvis palette of the roster), beacon `#ffe08a` |
| trees | trunk `#7a5230`, canopies `#4faf49` / `#6cc35e` / `#93da7c`; pines `#2f7f45` / `#3b9452` / `#5aae64`; palms `#4fb254` / `#7ccf6c`; boulders `#a19fab` / `#7e7c8a` |
| in-world type | Pixelify Sans (OFL, bundled via fontsource), ink `#1f2a3a` on cream chips |

Light: hemisphere sky `#d6ecff` / ground `#7f9c5a`, one warm directional sun `#fff1d6` from the
south-west, **no tone mapping** (`flat`), no shadows (a 2-px shadow is noise; a blob decal under
the feet comes with the figure pipeline). Materials: Lambert for lit surfaces, Basic for glass,
lamps and beacons — never PBR.

## 5. Rendering rules

- Orthographic camera, `RenderPixelatedPass` (normal edge 0.18, depth edge 0.28) +
  `OutputPass`, `dpr = 1`, antialias off. Pixel size is an integer (2); zoom never scales the
  pixel grid.
- Terrain is ONE merged vertex-coloured mesh (high ground tinted lighter, wet sand darker); trees,
  pines, palms, boulders, hedges, lamps, panels and greenhouses are instanced; the sea is one
  plane whose shader reads a shore-distance texture (depth tint, crests rolling in, foam line); buildings are shared unit geometries scaled per use, sharing ~25 materials through
  `WorldKit`.
- Every canvas mounts through `useWebglSurface`; the loop pauses via IntersectionObserver;
  reduced motion → demand-driven frames, no wander, no water drift; no WebGL → fallback to the
  Ledger.
- DOM over the canvas (nameplates, place signs, HUD) is legible at every zoom because it is not
  pixelated; it uses the world's type and colours.

## 6. What V1 ships, what comes next

V1 (this pass): terrain + sea, the market village, the four quarters' landmarks, trees,
navigation (drag / keys / 3 zoom steps / minimap), stand-in walkers with wander + checkpoints +
nameplates + click-to-select, place signs, the World / Ledger switch in the Jarvis Agents section.

Next, in order: the model card opening on a figure click (the card session's overlay); real
figures from the character pipeline replacing `WalkerFigure` (F3); speech bubbles and the
choreography queue on the society event stream (MASTERPLAN §3.3); the "Active now" face strip
and today's cost in the HUD; a day/night flavour; the headless-Chrome screenshot check.
