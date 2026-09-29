# Agent office map

Status: **walkable prototype built 2026-09-28, awaiting the maintainer's visual review.**
It replaces the Mars colony as the renderer behind **Agents > Map**. The Mars
code, its backend contracts and the communications station remain in the tree
and reachable from the Agents workspace; only the map surface changed.

Per [game-art-pipeline.md](game-art-pipeline.md) §3 this runtime scene is the
reference for the office direction. Do not roll its style out to other asset
families or replace figures before the maintainer approves it.

Code: `jarvis/ui/web/frontend/src/components/society/office/`.

## 1. Why an office

The previous worlds (pixel island, Mars colony) were large, navigable places
that cost a lot of authored art and backend navigation to show one thing: what
the agents are doing. The office answers that question in one screen. Every
agent has a desk, departments group them, and a status ring, a monitor face and
a name pill show the run state. It is a projection of the roster only: the map
never moves an agent, starts work or invents activity (world-behaviour-manual
§1 still holds).

## 2. Style guide (derived from the maintainer's reference screenshots)

| Aspect | Rule |
|---|---|
| Genre | Stylised "toy office" diorama: chunky low-poly 3D, not anime, not pixel art, not realistic. Close to casual social-game and avatar-app styling. |
| Characters | Chibi proportions (big head, short body), dot eyes, flat matte colours, no textures. Seated at desks; the stored figure recipe is kept, agents without one get a stable chibi look. |
| Furniture | Simple primitives with small rounded edges: light-wood desk tops on white pedestals, black monitors, dark swivel chairs, dark-wood name boards with light slats, potted low-poly plants, bookshelves with coloured books, dark couches in a lounge. |
| Materials | Matte (roughness ~0.85), no metalness except the railings; colour does the work. Wood-plank floor, desaturated carpet per department. |
| Light | Cool sky / warm ground hemisphere light plus one soft directional sun with PCF shadows. Always bright enough to read, never moody. |
| Setting | The floor floats in a starry night sky behind glass railings. |
| Camera | Perspective, 35° FOV, three-quarter view from the south-east about 40° below the horizon. Orbit, pan and zoom within limits (20°–72° polar, 4–140 m). Home view frames the occupied desks. |
| Labels | Dark translucent pill above each agent: hexagon badge (initial, star for the lead), name, state dot; the state word only when it is not idle. Scale is clamped by distance. |
| State language | Green = working (pulsing floor ring, code on the monitor), amber = waiting for the person, grey = idle (smiley monitor) or paused (dark monitor). |
| HUD | Theme-token cards over the canvas (title, working/waiting/idle counts, Overview and Agents list buttons), so light and dark mode both work. The diorama itself keeps one palette in both modes. |

## 3. Technology decision

**Three.js through React Three Fiber, in the existing frontend.** This beat the
runner-up (a Blender-authored scene exported as GLB) because the office look is
built from boxes, cylinders and rounded boxes: code builds it in milliseconds,
it adapts to any roster size, and it needs no asset pipeline. Blender stays the
tool for anything organic later (new character bases, special props), following
the game-art pipeline. No engine change (Unity, Godot, Babylon) is warranted:
the renderer is already Three.js inside the WebView, the figures and their
animation clips (`sit`, `work`, `idle`, `walk`, …) already exist, and every OS
target runs WebGL.

## 4. Floor plan and data model

`officeLayout.ts` is pure and tested. It builds the whole floor from the roster:

- **North strip:** lead office (lead-tier agents), team room (long table, board),
  wardrobe (lockers, mirror). Glass walls with a door towards the office.
- **Middle:** departments = provider families (`providerLabel`; empty means
  Jarvis' own brain), two columns, at least four (spare ones are "Open space"),
  at most six (the rest fold into "Other"). Benches of back-to-back desks.
- **South strip:** an open reception/lobby with the elevator, the reception desk
  and the agent board; a walled break room with couches, coffee bar, water
  cooler, arcade and beanbags.
- **Furniture** has fixed footprints (`FURNITURE_SIZE`) shared by renderer and
  navigation; **spots** are hangout places with pose and facing; **checkpoints**
  are action places; **obstacles** are every solid footprint.
- Seating is by arrival (`createdMs`, then id), so a refetch never re-seats anyone.

## 5. Behaviour, player and interaction

- **Navigation** (`officeNav.ts`): a 0.2 m occupancy grid, A* with line-of-sight
  smoothing, and a final snap onto seats that sit inside a furniture footprint.
- **Agents** (`officeBehavior.ts`, `OfficeAgents.tsx`): working → seated at their
  own screen; waiting → standing at the desk, waving; paused → napping on a
  couch; idle → a weighted day of coffee, couch, window, arcade, books, water
  cooler, team table, strolling or chatting at a busy colleague's desk. Spots are
  reserved so nobody sits on anybody. All of it is client-side, deterministic
  per agent, costs no tokens and never starts or stops work. Reduced motion
  places agents without walking. Agents created while the office is open arrive
  by the elevator.
- **The person's character** (`OfficePlayer.tsx`, `playerProfile.ts`): WASD /
  arrows (camera-relative, Shift runs) or click the floor to walk; E interacts
  with the nearest agent or checkpoint. Name and body are chosen in the
  wardrobe and stored per browser profile (not roster data).
- **Camera** (`OfficeCameraRig.tsx`): one orbit camera that glides after the
  character; zoom out and it is the overview. Everything stays clickable from
  afar, so walking is optional. A right-drag pan or a fly-to stops following;
  moving resumes it.
- **Checkpoints** (`OfficePanels.tsx`): reception → create an agent (existing
  dialog); agent board → list, show on map, open, agent management; team room →
  pick agents and create a team (existing chat groups), gather teams at the
  table; wardrobe → your look; lead office → talk to the lead; break room → call
  free agents for a coffee break (visual only).
- **Agent panel:** open chat, walk there, call over, show on map, add to a new team.

## 5a. Figures, pets and live monitors (2026-09-28, round 2)

- **Toy figures** (`ToyFigure.tsx`, `toyFigureModel.ts`): every agent and the
  person are procedural toy characters in the reference style — big round
  head, dot eyes, smile, T-shirt, trousers, chunky sneakers, eight hair/hat
  styles. Colours come from the stored figure recipe; hair from a stable hash
  (the person picks it in the wardrobe). Poses are pure and tested: the hips
  land exactly on each seat height (chair 0.52, couch 0.56, meeting 0.50,
  beanbag 0.42) and nothing reaches more than 0.16 m behind the seat centre,
  so figures no longer clip through backrests. Chairs sit 0.62 m from the desk
  centre and keyboards at the desk edge so the short arms reach them.
  The agent creator still shows the older rigged figures; switching it to the
  toy style is the rollout step after the maintainer approves this look.
- **Pets**: each agent's symbol (the profile companion) follows it at about a
  fifth of its height, reusing `AgentFollower`; the lead gets Gigi.
- **Live monitors** (`LiveMonitors.tsx`, `useDeskChats.ts`, `deskChat.ts`): an
  agent seated at its own desk shows the tail of its chat (user lines, replies,
  running tools) on its monitor, refreshed by jittered polling of the existing
  session endpoint for seated agents only (at most six per tick). Clicking the
  screen dives the camera into it and then opens that agent's chat.
  Follow-up: a `limit` query on the session endpoint, so long chats are not
  fetched whole.
- **Pace**: the person walks at 2.0 m/s and sprints at 4.4 m/s (Shift).
  Working agents stay at their screen even when called.

## 5c. Lead office as an executive suite (2026-09-29)

The lead office is the one room that breaks the plain toy-office palette on
purpose, so the boss's room reads as the most precious place on the floor
(`LeadSuite.tsx`, colours in `LEAD_SUITE`):

- **Shell:** dark walnut chevron floor, brass-framed walls with warm-tinted
  glass, a gold-on-black door sign.
- **Feature wall (north, 2.9 m):** walnut slats around a backlit black-glass
  star emblem, two lit bookcases with books, trophies and vases, warm LED coves.
- **Executive desk:** 2.4 m walnut desk with a brass edge, three monitors, a
  banker's lamp, a star plaque on the visitor side and a high-back oxblood
  leather chair. Seat distance and height match every other desk, so the
  seating and figure-pose contracts are unchanged. A second lead gets a
  partner desk; there is never an empty lead desk.
- **Furnishing:** navy rug with a gold border and medallion, two tan club
  armchairs for visitors, a bar cabinet and a chesterfield lounge with a glass
  coffee table along the west wall (fronts towards the camera), a floor globe,
  two floor lamps and palms. Two warm point lights make the room glow.
- Every piece stays inside its `FURNITURE_SIZE` footprint (props test) and the
  navigation tests still reach every seat, spot and checkpoint.

Awaiting the maintainer's visual review like the rest of the office.

## 6. Plan

1. First map (done 2026-09-28).
2. Walkable prototype (done 2026-09-28): rooms, checkpoints, agent life, own character.
3. **Review:** the maintainer plays it and gives feedback on look, camera, rooms and actions.
4. Polish: typing animation while working, speech bubbles for "waiting for you",
   door animations, agents greeting the person, sound.
5. Scale: instanced chairs/props beyond desks, measured frame times on a laptop
   GPU and a low-end integrated GPU, macOS/Linux WebViews.

## 7. Verification so far

- Unit tests: layout (grouping, stable seating, no overlap, camera framing),
  navigation (every seat, spot and checkpoint reachable from the elevator for
  0/10/40 agents; paths never cross obstacles), motion, behaviour (state rules,
  exclusive spots, determinism), props (every kind renders inside its footprint).
- Runtime (Chrome, dev build, live roster of ten agents): walking by click and
  by keyboard, camera follow and overview, nearby prompt and E, agent panel,
  "call over". Desks are instanced; the full floor measured ~730 draw calls and
  ~18 ms per frame on the development machine.
- Runtime: checked in Chrome against the live roster (ten agents, one lead)
  through a temporary dev preview: rendering, click on figure and on pill.
- Not yet measured: frame time and device coverage; macOS/Linux WebViews.
