# Agent office map

Status: **first map built 2026-09-28, awaiting the maintainer's visual review.**
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

## 4. Layout model

`officeLayout.ts` is pure and tested:

- Departments = provider families (`providerLabel`; empty means Jarvis' own
  brain). At most six; the rest fold into "Other". The floor always shows at
  least four; spare ones are "Open space" with free desks.
- Each department is benches of back-to-back desks, four per row, eight per
  bench, one to four benches.
- The lead tier sits in a glass office at the back; a lounge fills the front.
- Seating is by arrival (`createdMs`, then id), so a refetch never re-seats
  anyone; run state changes only repaint monitors and rings.

## 5. Plan

1. **First map (done):** floor, departments, lead office, lounge, seated
   figures, state rings/monitors/pills, HUD counts, camera, click-to-open.
2. **Review:** the maintainer looks at the map in the app. Capture feedback on
   look, camera and department grouping before any further art.
3. **Life:** optional short walks (desk ↔ lounge when idle for long), typing
   animation while working, a "waiting for you" bubble. Driven only by real
   state changes, never by LLM calls.
4. **Detail pass:** a coffee corner, meeting room for group chats, per-department
   decor, name boards with provider logos.
5. **Scale and performance:** instanced desks for large rosters, measured frame
   times on a laptop GPU and a low-end integrated GPU.

## 6. Verification so far

- Unit tests: `officeLayout.test.ts` (grouping, stable seating, no overlap,
  empty roster, camera framing), `JarvisAgentsView.test.tsx`.
- Runtime: checked in Chrome against the live roster (ten agents, one lead)
  through a temporary dev preview: rendering, click on figure and on pill.
- Not yet measured: frame time and device coverage; macOS/Linux WebViews.
