# components/society — Agent Society frontend (scaffold)

Not built yet. Reserved home for the society surfaces planned in
`docs/agent-society/MASTERPLAN.md` (§4, §9):

- `world/` — the isometric retro pixel world (three + @react-three/fiber v8 + drei, orthographic
  dimetric camera, ~320×180 nearest-filtered render target / `RenderPixelatedPass`), checkpoints,
  skin-format voxel walkers, the client-side choreography queue.
- `card/` — the agent model card: rotating `skinview3d` figure on the left, spec sheet on the right.
- `ledger/` — the data-dense board tab (successor of the DepartureBoard) and the declared fallback
  for no-WebGL, reduced-motion, and headless contexts.
- `feed/` — society message feed and bounded room transcripts.

Branding: the world viewport carries its OWN bright video-game art direction (MASTERPLAN §4.3) —
never Ink & Paper monochrome and never the Cursor-derived design doc; only the app chrome around
the viewport uses the app's theme tokens.

Binding frontend rules: every canvas mounts through `useWebglSurface` (AP-32 context-loss
recovery + context budget); rAF pauses via IntersectionObserver, never `document.hidden`; the whole
section is a lazy route chunk; light AND dark mode via theme tokens; one-viewer layout doctrine
(no page scroll, overflow into drawers); all strings through the hand-formatted locale files.
