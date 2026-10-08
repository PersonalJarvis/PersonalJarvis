# components/society — Agent Society frontend

The Jarvis Agents section. Agents v2 (MASTERPLAN §2.10): one click creates an
agent (`data.ts` `useQuickCreateAgent`) and opens its one endless chat
(`chat/AgentChatPanel.tsx`); delegations, waiting messages and routine runs
render as folded cards there (`chat/ChatActivity.tsx`). Applied identity
changes update the profile without adding a notice to the chat.

Folders, as planned in `docs/agent-society/MASTERPLAN.md` (§4, §9):

- `world/` — the isometric retro pixel open-island world (three + @react-three/fiber v8 + drei,
  orthographic dimetric camera, ~320×180 nearest-filtered render target / `RenderPixelatedPass`),
  checkpoint places, low-poly pixel-textured walkers, the client-side choreography queue.
- `card/` — the agent model card: rotating low-poly GLB figure on the left, spec sheet right.
- `ledger/` — the data-dense board tab (successor of the DepartureBoard) and the declared fallback
  for no-WebGL, reduced-motion, and headless contexts.
- `feed/` — society message feed and bounded room transcripts.

Branding: the world viewport carries its OWN bright video-game art direction (MASTERPLAN §4.3) —
only the app chrome around the viewport uses the app's theme tokens.

Binding frontend rules: every canvas mounts through `useWebglSurface` (AP-32 context-loss
recovery + context budget); rAF pauses via IntersectionObserver, never `document.hidden`; the whole
section is a lazy route chunk; light AND dark mode via theme tokens; one-viewer layout doctrine
(no page scroll, overflow into drawers); all strings through the hand-formatted locale files.
