# Authored city integration review

Date: 2026-09-10. Tier: T2, existing frontend/WebGL surface and local presentation state. The renderer is shared across Windows, macOS and Linux; Windows/Chrome was exercised here, other OS/device coverage remains unverified. No backend tool-execution, credential or provider contract was changed.

## Implemented city

- City is the default WebGL Agents view. Ledger remains the no-WebGL fallback; the legacy map remains selectable. Versioned preference migration preserves an existing ledger choice.
- Five authored districts: Central, Terminal, Knowledge, Workshop and Communications. Every district has a metro stop, an entrance, interior routes and 30 physical work locations. A smooth closed metro ring connects them. Streets, a promenade, lifts and an elevated crossing use the same coordinates as navigation.
- The operable city is 1800 by 1200 metres. Camera targets/zoom are bounded. Instanced authored skyline modules and continuing streets extend into distance fog to create an open-ended urban backdrop; this is not an infinite buildable or simulated world.
- Terminal architecture includes physical TERMINAL / COMPUTE signage, the console symbol, monitors, keyboards, chairs and server racks. Building cutaways expose work floors. Clicking the Terminal workspace control opens the existing `agentic-ide` surface; individual real agent actions open their existing agent cards. There is no invented mapping from society agents to IDE terminal call-signs.
- The metro is authored in Blender with a cabin, seating, bogies, corrected outward-facing cab surfaces and named sliding doors. Passenger inspection opens a cutaway; ordinary viewing retains the full vehicle.
- Agents walk, queue, board through the actual open door, ride, disembark, enter the destination and use an allocated workplace while their real work state is active. The train reserves 24 passenger positions. Full workplaces have a separate retrying queue. Trips are physical and independent of actual task execution.
- Versioned plot adjustments validate footprints, access and occupancy, update navigation and persist live layouts. Demo/sample changes stay in their own sessions. Live journeys survive ordinary section switches; pending requests and offline sample responses do not erase them. A full document reload starts a new simulation session.
- One monotonic clock drives foreground and background updates. Active catch-up is budgeted without dropping elapsed travel time. Actual figures stop animation when simplified. Daylight runs on a 30-minute cycle with pause/manual time and a live reduced-motion listener.

## Every asset has an individual render

`../render-gallery/index.html` is the clickable gallery. `coverage.json` records source and PNG hashes; `summary.json` confirms 108/108 assets and no missing renders: 95 existing assets and 13 newly authored city assets. There are 110 PNGs including two additional detail views, plus six contact sheets.

The two animation-only files are explicitly displayed as skeleton visualizations. Hidden Blender importer helpers were excluded from render framing. Existing catalog assets were individually rendered and checked; this is not a claim that all 95 were remodeled. Existing recipes, imports and catalog files remain intact.

The editable `.blend` source and manifest exports are retained. Blender raycasts confirm all 30 Terminal foot anchors at local Y=0 and the metro floor at local Y=0.08. Independent checks confirm outward cab normals at both vehicle ends.

## Verification

- 49 frontend tests passed: 33 journey/layout cases, three shared-clock cases, four city reconciliation/sample/pending-data cases, two session cases, one reduced-motion case, five WebGL lifecycle cases and one instanced-resource disposal regression.
- Art-pipeline pytest checks passed (10 cases). Authoring/gallery scripts passed Ruff; gallery source/image hashes and all 13 GLBs were checked.
- Isolated production TypeScript and frontend builds passed. The shared tree temporarily had an unrelated `AgentRoutinesList.test.tsx` type error; only the city namespace is composed into isolated locale files, so unrelated unfinished translations are not included in this change.
- Independent code review approved behavior, geometry normal fixes, lifecycle, sample disclosure, clipped-roof picking, session persistence and the disposal fix.
- Real Localhost loaded the integrated city with its five real roster entries. Demo mode then exercised physical transport and working at the Terminal. No real command was submitted to make the demo appear active.
- A browser observation recorded boarding at simulation time 26.5 seconds and a later observation recorded work at the destination at time 190.775. `city-v2/journey-stages.json` contains the observations.
- `city-v2/terminal-working.png` and the actual-canvas recording `terminal-work.webm` show eight agents using allocated computers. `metro-ride.webm` shows the actual moving metro with passengers aboard. Extracted video frames were inspected; these are runtime recordings, not reconstructed animations.
- The Terminal control opened the real workspace setup/restore surface. A controlled return used the same document and retained the passenger position while simulation time advanced from 30.2 to 32.5 seconds: `city-v2/workspace-roundtrip.json`. An earlier long check was interrupted by an external bundle replacement and is not used as persistence proof.

## Defects found and fixed during runtime review

StrictMode replay released a still-live WebGL context; release is now deferred and reclaimed only for the same reattached canvas. An unstable translation callback caused a reconciliation loop; actor identity now depends on the translated string, with a regression guard. Shader-clipped roofs intercepted picking; above-plane hits now pass through. R3F's `dispose={null}` shadowed the native instance disposer; cleanup now invokes the native prototype method and verifies its disposal event. Larger overview distances now adjust the camera near plane to avoid depth fighting.

## Limits and remaining work

The reviewed machine uses an NVIDIA RTX 5070 Ti. A verified 30-demo-agent check at a 1366 by 768 browser viewport (820 by 573 city canvas, reduced motion off) sampled 45 frames after warmup: mean 8.02 ms, median 7.0 ms, p95 7.2 ms. The renderer reported 153 draw calls and 1,469,202 triangles. See `city-v2/performance-1366.json`. This supports responsive rendering on this machine; it is not an integrated-GPU certification. Native-size observations vary. Other OSes and large populations beyond the current test scenarios need device coverage. The metro is one operating train on a preplanned ring, not a complete railway-management simulator. City editing covers existing plots, not free road/rail construction. Existing figure families have not all been redesigned into new future-role meshes.

The user authorized continued city integration after inspecting the first prototype and requested new metro/terminal art plus individual renders. Final artistic acceptance of this revised city remains a human review; no validator or agent-authored file grants it.
