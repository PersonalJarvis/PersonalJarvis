# Gigi hover companion

## Authorized outcome

Implement the user-supplied companion brief in full: editable Blender master,
optimized GLB, optional in-world assistant, real task/audio presentation,
collision-aware following and navigation, and a consistent family of active
Gigi marks and platform icons. The specific runtime model requires user review
before broad brand replacement. This study is not approval of the Mars world,
worker design or any replacement of stored agent identities.

## Reference interpretation

The user supplied the black/yellow multi-view character sheet for this work.
It is an art-direction input, not a runtime texture. No external stock assets
are used in the model. Preserve rounded graphite housing, yellow eyes with
pupils, a small mouth, sensor pods, small arms and a continuous toothed hem.
Resolve contradictory body-symbol tiles by omitting all body emblems. The
separate Jarvis wordmark is outside this redesign. Rear service panel and
underside emitter are authored consistently from the same closed geometry.

## Scale, material and budget targets

- Nominal body height: 0.40 metres; glTF Y up, +Z forward, bottom-centred pivot.
- Model and animation names are stable, prefixed `Gigi.`. Blender master uses
  -Y forward/Z up; the manifest exporter performs the axis conversion once.
- Graphite alloy with roughness, fitted dark face lens, subdued metal trim and
  yellow PBR emissive accents. No Blender-only shader is assumed to export.
- Review at standard player view, close focus and world overview. Report
  actual viewport, FOV, distance and projected body height from that renderer.
- Provisional desktop asset budget: 1 MB GLB, 40 draw calls, 40k triangles,
  no external texture fetches. These are companion budgets, not world totals.
- Desktop target: current Windows renderer plus portable browser implementation;
  Linux/macOS runtime and resource tests remain required and unverified.
- Reduced motion removes ambient hover/blink; deliberate following stays usable.

## Ownership and integration

Gigi work owns `scripts/art/build_gigi_companion.py`, this study and
`components/society/companion/`. The Mars task owns the new world and
`MarsScene.tsx`/`MarsWorldStage.tsx`; local wiring is submitted as a separate
integration patch against its checkpoint, not written into its working tree.
World scale, colliders, ground, navigation/vehicle anchors and player pose are
injected. The existing assistant/chat/audio stack owns logical execution.

RUB-82 tracks Gigi branding alongside independent signage. RUB-81 owns normal
workers; its current capsule is only a scale proxy, never an approved figure.
RUB-69 owns the Outpost and final world integration.

## Full delivery sequence and pending acceptance

1. Inventory active and historical uses, preserve recipes and unrelated changes.
2. Author master, closed geometry, expressions and reproducible early export.
3. Show the actual runtime alongside a regular worker, at the Outpost and station,
   and at normal/close/overview distances. Compare small platform icon proposals.
4. Finish follow/run/turn, door/ceiling/bridge/interior, approach/guide/dock,
   collision, recall, user/agent attention and rover transitions.
5. Map authoritative audio/task/approval/error/completion states; verify mute,
   interruption and cancellation. Do not derive success from animation.
6. Prove optional presence, persistence, world/context isolation, two clients,
   no duplicate task/audio processes, reopen/resync, missing model and WebGL loss.
7. After specific visual approval, replace active Gigi assets and their recipes,
   including icon sizes 16/24/32/48, native variants, web marks and marketing uses.
8. Verify light/dark appearance, real performance before/after, required platform
   checks, reproducible sources, scoped commits and integration evidence.

No step is complete merely because this checklist or a placeholder exists.
