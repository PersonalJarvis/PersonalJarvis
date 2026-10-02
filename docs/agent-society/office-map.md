# Agent office map (Jarvis Verse)

Product name: **Jarvis Verse** (since 2026-09-30). The interface calls the
whole map, the Map tab and the IDE side-panel tab "Jarvis Verse"; the code keeps
its `office` identifiers, and rooms and floors inside it keep their own names.

Status: **walkable prototype built 2026-09-28, awaiting the maintainer's visual review.**
It replaces the Mars colony as the renderer behind **Agents > Jarvis Verse**. The Mars
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
| Furniture | A contemporary workplace built from rounded primitives: light-oak bench desks on black steel T-legs with a white pedestal and a felt privacy screen, slim monitors on arms, ergonomic chairs with a mesh back on a five-star base, open oak-and-steel shelving, low modular sofas on slim legs, tall charcoal planters with full crowns. Department back walls are fluted acoustic felt with a charcoal name plate in white type and oak ledges with small plants. |
| Materials | Mostly matte; black steel and chrome arms carry a little metalness. Light microcement floor, a rounded felt rug per department whose colour also dyes that department's felt screens and seat fabric (`DEPARTMENT_ZONES`). Rooms: slate felt (team), terrazzo (wardrobe), pale stone (reception), light oak (break room). Glass walls and the outer balustrade have slim black frames. |
| Light | Cool sky / neutral ground hemisphere light plus one soft, slightly warm directional sun with PCF shadows. Always bright enough to read, never moody. |
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
- **Spawn point (both floors):** a round pad with a terminal on the crossing of
  the centre aisle and the cross aisle nearest the floor's middle, the one plaza
  every route passes. `layout.arrival` (south of the terminal) is where anyone
  new to the floor appears.
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
  places agents without walking. Agents created while the office is open appear
  on the spawn pad in a column of light and walk to their desk.
- **The person's character** (`OfficePlayer.tsx`, `playerProfile.ts`): WASD /
  arrows (camera-relative, Shift runs) or click the floor to walk; E interacts
  with the nearest agent or checkpoint. Name and body are chosen in the
  wardrobe and stored per browser profile (not roster data).
- **Camera** (`OfficeCameraRig.tsx`): one orbit camera that glides after the
  character; zoom out and it is the overview. Everything stays clickable from
  afar, so walking is optional. A right-drag pan or a fly-to stops following;
  moving resumes it.
- **Checkpoints** (`OfficePanels.tsx`): spawn point → spawn a new Jarvis agent
  (existing create dialog; a host without one, like the IDE side panel, gets the
  office's own copy); reception → help, and create an agent too; agent board → list, show on map, open, agent management; team room →
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
  fifth of its height, reusing `AgentFollower`; the lead is drawn as the
  person's own pet (see *Jarvis is the person's pet* below).
- **Live monitors** (`LiveMonitors.tsx`, `useDeskChats.ts`, `deskChat.ts`): an
  agent seated at its own desk shows the tail of its chat (user lines, replies,
  running tools) on its monitor, refreshed by jittered polling of the existing
  session endpoint for seated agents only (at most six per tick). Clicking the
  screen dives the camera into it and then opens that agent's chat.
  Follow-up: a `limit` query on the session endpoint, so long chats are not
  fetched whole.
- **Pace**: the person walks at 2.0 m/s and sprints at 4.4 m/s (Shift).
  Working agents stay at their screen even when called.

## 5b. Coding floor (2026-09-29)

A second floor for the coding agents that run in the Agentic IDE's terminal
panes. It shares the office's engine, style and controls; only the people and
the floor plan differ.

- **Elevator**: the reception elevator is a checkpoint on every floor. Its
  call button opens the floor picker (see §5e; before the arcade floor it
  simply went up or down). The ride is a short door-like fade, and
  the person steps out at the elevator of the other floor. The floor survives
  a remount of the stage.
- **Floor plan** (`buildOfficeLayout(agents, { variant: "coding" })`): one
  department per IDE workspace; no lead office and no wardrobe (a quiet zone
  takes their place, built from existing furniture); reception with the
  elevator and the break room stay. The agents office layout is unchanged.
- **Spawn point** (2026-09-30): the coding floor's spawn point replaces the old
  lobby kiosk. Its panel is Mission Control's launcher (CLI, workspace, how
  many, optional first task, through the IDE's own `POST /terminals`); every new
  pane appears on the pad and walks to its desk. `SpawnPoint.tsx` builds pad,
  terminal and fittings in each floor's own materials (oak, bronze and a mint
  glow with the brass ghost downstairs; walnut, brass and amber with a brass
  `>_` upstairs); two gyroscope rings turn round the floor token.
- **Jarvis follows**: on the coding floor Jarvis flies (or walks) in "follow"
  mode beside and behind the person with a smoothed lag and never inside a
  wall, including in the elevator. In the agents office it keeps its
  lead-office behaviour.
- **Jarvis is the person's pet** (2026-10-02): Jarvis appears as the pet
  chosen in Settings → My Pets (`docs/pets.md`), in 3D, not always as Gigi.
  `companion/petCompanions.ts` maps a pet to its companion:
  Gigi keeps its hover model; the six other built-ins have authored Blender
  models (`scripts/art/build_pet_companions.py`, study
  `art/studies/jarvis-pet-companions`, one GLB per pet under
  `assets/society/companions/pets/`) and a gait of their own: Ember the
  dragon flies with beating wings, Miso the cat trots, Bolt the battery
  waddles, Brew the teapot hops with a rattling lid, Mochi bounces and Shelly
  the snail crawls. Pets that fly hover at head height with Gigi's halo and
  sparkles; pets on the floor (`gigiFlight.ts` `ground` mode) walk right
  behind the person's feet, take floor routes (`findPath`) on errands instead
  of flying over desks, and carry their nameplate low. Limbs move by
  `companion/petRig.ts`, phased by ground covered so feet do not slide;
  reduced motion stops every gait. A pet the person drew becomes a voxel
  figure extruded from its own idle frames (`voxelPet.ts`), facing the camera
  and floating low. The stage feeds the choice through
  `companion/companionPetStore.ts`, so a change in My Pets swaps the
  companion live.
- **Pane to character** (`codingFloor.ts`): every running coding-agent pane
  (Claude Code, Codex and the other agent CLIs; not plain shells, not archived
  panes) is one character. Its id is `pane:<workspace>:<history id or key>`,
  and its look (figure and palette) is random but stable, hashed from the
  pane's history id. Name comes from the pane's short title, department from
  its workspace.
- **States**: working, starting or pending → sits at its computer; asking →
  waits at its desk and waves; idle, done or exited → walks around and hangs
  out in the break room; an error keeps the agent at its desk. The exact
  mapping lives in `codingFloor.ts` and its tests.
- **Live monitors** (`TerminalMonitors.tsx`, `usePaneScreens.ts`,
  `terminalScreen.ts`): an agent seated at its desk shows its terminal live —
  dark background, monospace text, the last rows that fit, a cursor block and a
  title bar with its name and state dot. Screens come from the read-only
  `GET /api/agentic-ide/screens?pane=<workspace>:<key>` feed (at most eight
  panes per call; it never resizes, attaches to or writes into a terminal).
  Only seated agents near the camera are polled (six at most), on a jittered
  1.2–2 s schedule that pauses while the stage is asleep, and a monitor
  redraws only when its screen changed. Idle or absent agents show a dim
  screensaver.
- **Open session**: clicking a monitor (or E at the agent → "Open session")
  dives the camera into the screen and then opens the Agentic IDE with that
  pane focused (`codingNavigate.ts`).
- **IDE side-panel tab**: the coding floor is also the "Office" tab of the
  Agentic IDE's side panel (`components/agentic/sidePanel/OfficeTab.tsx`),
  added from the panel's "+" menu or its closed-panel rail. It lazy-loads the
  stage in a compact layout on the coding floor, inside its own error boundary,
  so a failed scene stays inside the tab. Bringing the tab forward widens a
  narrow panel to 520 px once; the user can drag it back. There a monitor click
  focuses the pane in the grid right next to it, and the office's "open the
  ledger" action brings the Agents tab forward.
- **Its own look** (`CodingFloorLook.tsx`, `CODING_SCENE` / `CODING_STUDIOS`
  in `officePalette.ts`): same plan as the agents office, none of its
  surfaces. A violet night instead of navy, a fine terrazzo floor instead of
  warm planks, and a glowing cyan rim under the slab edge. Every workspace
  department is furnished as a studio of its own — carpet pattern (grid,
  stripes, checker, dots, diagonal, zigzag) with a border, a painted sign wall,
  and desk and chair colours — cycled by the department's index. All of it is
  flat or outside the railing, so navigation is unchanged.

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
- **Sit at the desk (2026-09-29):** the executive chair is solid, so nobody walks
  through it. Walk up and press E, or click the chair, to sit (`leadSeat.ts`);
  any movement stands the character up. Clicking the monitors dives into them
  and opens the lead agent in the agents view, like any agent's desk screen.
- **Office dog (`OfficeDog.tsx`, `dogLife.ts`, `dogProps.tsx`):** a black-and-tan
  rottweiler figure with jointed legs, head, ears, tail and tongue. It has baskets in
  the lead office, the team room and the break room and lives in the room of its
  current basket: sleeps, roams, sniffs, sits, lies down. Now and then it walks (never
  jumps) to another basket. Pet it with E or a click: it sits, wags, shows hearts and
  follows the person inside its room for 30 s. Easter egg: the treat jar beside the
  break room's coffee bar hands out a bone; give it to the dog and it begs, catches
  it, spins twice in the air with gold sparks and chews it in its basket.
- Every piece stays inside its `FURNITURE_SIZE` footprint (props test) and the
  navigation tests still reach every seat, spot and checkpoint.

Awaiting the maintainer's visual review like the rest of the office.

## 5d. Contemporary look (2026-09-29)

The maintainer rejected the first look as a 1990s office and asked for a
genuinely modern one. The shared kit (`OfficeFurniture.tsx`,
`DeskInstances.tsx`), the floors and room surfaces (`OfficeScene.tsx`,
`OfficeRooms.tsx`) and the palette (`officePalette.ts`) were restyled as in §2;
the coding floor keeps its own terrazzo, night and studios, now in the same
contemporary kit with tone-on-tone patterns. Seat height, desk height, monitor
screen position and every footprint are unchanged, so poses, live screens and
navigation work as before. Still in the earlier style and waiting on the
maintainer's review of this reference before they follow: the lead suite, the
reception desk, the team room table and chairs, the lockers and
mirror, the coffee bar, the water cooler and the arcade.

## 5e. Arcade floor (2026-10-02)

A third floor on top of the building: a retro arcade hall with ten playable
cabinets. No agents work here; it is a place for the person (and their pet,
which follows like on the coding floor).

- **Elevator**: pressing the call button (click, or E at the doors) opens a
  floor picker (`ElevatorPanel.tsx`) that lists all three floors top first;
  a click, Enter on the focused floor or the floor's number key (0 agents,
  1 coding, 2 arcade) rides there, Escape closes it. The call button shows
  up, down or both arrows depending on the floor (`callDirection`).
- **Floor plan** (`arcade/arcadeFloorLayout.ts`, `buildArcadeLayout()`),
  reworked 2026-10-02 after the maintainer found the first open hall
  confusing: it now reads like the floors below. North strip: three walled
  game rooms (Classics: Pixel Raiders, Maze Muncher, City Defense, Paddle
  Duel; Puzzle corner: Block Drop, Neon Snake, Brick Breaker; Action zone:
  Asteroid Run, Desert Dash, Road Hopper), cabinets on each back wall facing
  the door. Middle: the open hall (pinballs, air hockey, dance floor, floor
  logo). South strip: the foyer with the elevator and token machines, the
  prize shop (counter, claw machines) and the snack bar. The doors of north
  and south rooms face each other on three lanes that stay clear. Cabinets
  are furniture of kind `retroCabinet` with the id `cabinet-<gameId>`; the
  person plays standing 0.85 m in front of one (`OfficePlayer`
  nearest-interactable, E opens it). Tests prove every play spot, counter
  and room is reachable from the elevator and that the lanes stay free.
- **Look** (`arcade/ArcadeHall.tsx`, `ArcadeHallProps.tsx`,
  `arcadeHallLook.ts`, `arcadeScreens.ts`): smoked-glass walls with a neon
  cap in each room's colour (never hidden or popping away at any camera
  angle; frames, glass and neon are three merged meshes), a lit name board
  over every door, each room's own floor and neon floor logo, a calm
  blacklight carpet in the hall. One shadow light plus hemisphere and
  ambient; neon is self-lit. Cabinets show two-frame attract screens
  flipped by a texture offset. The first version's live title-screen
  preview froze the frame for 0.2-0.6 s whenever the person walked up to a
  cabinet and was removed; frames at a cabinet now take 2-11 ms in a dev
  build (measured with `window.__officeGl.info` and timed `__officeStep`).
- **Games** (`arcade/games/*.ts`, contract in `arcade/retroGame.ts`, registry
  in `arcade/arcadeGames.ts`): Neon Snake, Brick Breaker, Paddle Duel, Block
  Drop, Maze Muncher, Desert Dash, Pixel Raiders, Road Hopper and City
  Defense are original Canvas 2D games (pure state + fixed 60 Hz step, no
  assets, no ROMs), code-split and loaded on first play. Asteroid Run, the
  break room's 3D game, has a cabinet here too.
- **Cabinet overlay** (`arcade/RetroArcadeOverlay.tsx`): a modal at the
  cabinet with marquee, crisp integer scaling, start / pause (P, blur) /
  game over, best score per game in localStorage, keyboard, gamepad, mouse
  where a game aims with it, and a touch pad on coarse pointers. Escape or E
  leaves.

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
