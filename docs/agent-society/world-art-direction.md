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
| 3 | Size | **10 × 10 fields; the central 4 × 4 fields are the market district** | the maintainer wants a big island that is not seen in one screen; one field = one screen at the closest zoom |
| 4 | Landscape | **A village / small town** on a green island: meadows, beach, a bay, one rocky highland | free-text decision: "a village, ultramodern, future-oriented" |
| 5 | Architecture | **Solarpunk village** — white + glass + solar + gardens + wood, lots of green between | colourful AND futuristic; pure factory grey or neon-cyber contradicts the bright island |
| 6 | Centre | **Comic-village ring structure, modernised**: houses around the open square, the big tree with the long table in the middle, the lead's hub at the head (north) | the maintainer's own image ("Asterix-style village structure, only modern") |
| 7 | Pixel grain | **Fine — 2 screen pixels per rendered pixel** (a 1280-px stage renders at 640 px) | figures and signs stay readable; 320×180 would be too coarse for a big island |
| 8 | Navigation | **Drag + arrow keys/WASD, three fixed zoom steps (32 / 64 / 128 m), minimap** | 100 fields need zoom to find an agent; fixed steps keep pixels crisp |

Decided by the build, open to the maintainer: fixed warm-afternoon light in V1 (a real-clock
day/night cycle is a later flavour); animated pixel water; the hub carries the pulsing beacon
that will map onto the voice orb.

## 3. Layout

Units: 1 world unit = 1 m; one tile = 2 m; the island is 160 × 160 tiles (320 m). World origin
is the island centre; +x east, +z south.

```
            N  (archive tower)
            |
   solar    |    rock highland
   field    |
W (workshop)—[ MARKET DISTRICT ]—(lighthouse cape) E
            |
   gardens  |
 greenhouses|
            S  (harbor gate + dock in the bay)
```

**Market district (64 × 64 tiles, one flat plateau):**
- open square, radius 13 tiles, paved; garden beds alternate around its rim;
- the big tree in the exact centre, a ring bench around it, the long table on the south side —
  this is the `meeting` checkpoint (MASTERPLAN §2.7);
- 12 houses on a ring at radius 19 tiles, doors facing the square; the four cardinal directions
  stay open as gates; three variants cycle: solar-barrel roof, garden roof, glass loft;
- the **hub** (lead agent) at the head of the square, north, radius 20: the largest building,
  glass band, dome, beacon mast;
- ring road at radius 25, hedge ring with four gates at radius 29, lamps along the spokes;
- four spokes (width 3 tiles) from the square to the quarters.

**Quarters and checkpoints** (backend place → island place, `Walkers.tsx`):

| checkpoint | place | building |
|---|---|---|
| `desk` | workshop (west) | long hall, sawtooth skylights, wide door toward the village |
| `meeting` | market square | the table under the big tree |
| `archive` | archive (north) | round tower, two glass bands, blue dome |
| `gate` | harbor gate (south) | gate pillars over the dock into the bay, kiosk, moored boat |
| — | lighthouse (east cape) | striped tower with rotating lamp, keeper's hut |
| — | gardens (south-east) | six glass greenhouses on garden tiles |
| — | solar field (north-west) | 35 tilted panels on posts |

Idle agents (`idle`) wander inside the square with the rest-biased model (`wander.ts`);
paused agents stand still.

## 4. Palette

Everything inside the viewport comes from `worldPalette.ts`. Never a theme token, never Ink &
Paper, never the Cursor design doc (MASTERPLAN §4.3). Two shades per terrain kind give the
tile-art flicker under the pixel pass.

| Role | Colours |
|---|---|
| sky / clear | `#a5dbff` |
| sea | surface `#44a0dd`, deep `#2f7fc4`, ripple `#8fd0f4`, foam `#d9f2ff` |
| sand | `#f3e2ad` / `#ead597` |
| grass | `#7ccb5c` / `#6fbe51`; meadow (high ground) `#97d46c` / `#8ac860` |
| rock | `#a8a7b3` / `#9a99a6`, faces `#6f6e7c` |
| square / paths | `#ece1cf` / `#e2d5c0`; paths `#dcc9a5` / `#d0bd97` |
| cliff faces under grass | `#8e6a44` |
| walls | `#f7f3ea` / `#e6e0d2`, trim `#c9c2b2` |
| glass | `#8ed2f0` (unlit — it glows) |
| solar | `#26375a`, seams `#3e5f95` |
| wood | `#b57f45` / `#8c5e2f`, doors `#e0893b` |
| lead accent | `#2f6f8f` (the Jarvis palette of the roster), beacon `#ffe08a` |
| trees | trunk `#7a5230`, canopies `#4faf49` / `#6cc35e` / `#93da7c` |
| in-world type | Pixelify Sans (OFL, bundled via fontsource), ink `#1f2a3a` on cream chips |

Light: hemisphere sky `#d6ecff` / ground `#7f9c5a`, one warm directional sun `#fff1d6` from the
south-west, **no tone mapping** (`flat`), no shadows (a 2-px shadow is noise; a blob decal under
the feet comes with the figure pipeline). Materials: Lambert for lit surfaces, Basic for glass,
lamps and beacons — never PBR.

## 5. Rendering rules

- Orthographic camera, `RenderPixelatedPass` (normal edge 0.18, depth edge 0.28) +
  `OutputPass`, `dpr = 1`, antialias off. Pixel size is an integer (2); zoom never scales the
  pixel grid.
- Terrain is ONE merged vertex-coloured mesh; trees, hedges, lamps, panels and greenhouses are
  instanced; buildings are shared unit geometries scaled per use, sharing ~25 materials through
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
