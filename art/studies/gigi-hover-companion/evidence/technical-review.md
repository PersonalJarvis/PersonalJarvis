# Reference checkpoint: Gigi hover companion

Tier: T2, existing browser/Three.js presentation surface. No backend interface,
provider, transport, credential, configuration-schema or startup change.
Windows browser was exercised; Linux/macOS and native WebView are unverified.

## Verified at this checkpoint

- Blender 5.0.1 loads the editable master and the existing manifest exporter
  successfully regenerates the GLB. 764,972 bytes; 31,704 triangles; 32 meshes;
  six materials; 37 nodes; two blink animation clips. Standard PBR constants,
  no texture/decoder fetches. All provisional companion asset budgets pass.
- 12 companion frontend tests pass: motion/frame rates, thin-wall sweep,
  local path around a wall, ceiling adjustment, invalid/unloaded recovery,
  player-relative anchor, state/mute handling, scoped visibility preference,
  initial-history suppression and observed task failure.
- Nine existing Mars input tests and ten controller tests pass with the
  companion helpers present. They do not certify the full game integration.
- Eleven Python tests pass across actual GLB/source presence/budget checks and
  the art-study tool. Ruff passes for the builder and new asset test.
- The frontend build passed for the first integrated reference; the final
  checkpoint rebuild is recorded in the commit handoff.
- Actual app view: existing Agents view, `world=mars` preview, existing
  Outpost export, real local backend read interfaces, separate owned GLB.
  Clicking Focus produced the genuine `runtime-close-v2.png` capture.
  The old lead remained present in the agent roster. Hide changed the visible
  control to Show. No microphone activation or task submission was performed.

## Measurement conditions

Host GPU: NVIDIA GeForce RTX 5070 Ti. CPU: AMD Ryzen 7 7800X3D.
Browser: connected Chrome. Full CSS viewport 1600 x 1000; scene approximately
1054 x 651 CSS pixels; perspective FOV 45 degrees. Browser zoom was 25%; CDP
capture coordinates were adjusted using Page.getLayoutMetrics. Screenshot
resampling is not a different physical model scale.

Close reference: player feet [268,58.08,68], companion bottom
[268.95,59.23,68.35], camera [267.90,59.83,70.15]. Distance to companion
bottom approximately 2.17 m; geometric projected height approximately 145 CSS
pixels (near the runtime marker's approximate 150 px reading, depending on
scene height). The model remains nominally 0.40 m tall; there is no zoom scale.
`runtime-normal.png` records the original occlusion defect before anchor
correction. `runtime-normal-v2.png` records the corrected separation but still
shows the earlier gaze direction; gaze was subsequently adjusted to expose the
face with brief player glances. A fresh normal-view capture remains required.

The scene's renderer counters include the entire world and shadow passes.
They are not companion-only draw-call or FPS measurements. A controlled
before/after frame-time and memory benchmark remains pending. Other concurrent
local workloads and browser scaling changes affected observation reliability.

## Open acceptance, not waived

- Current neighboring actor is the world owner's yellow capsule. A regular
  approved agent/rover comparison and final station/rover docking do not exist
  in this checkpoint. Coordinate against RUB-81 and the evolving Mars anchors.
- Normal/overview/front/rear/side/underside runtime captures, light/dark and
  reduced-motion live checks must be completed on the final model.
- Full live walking/running/turning/doors/ceilings/bridges/interior traversal,
  agent clearance and camera occlusion coverage remain incomplete. The bounded
  local planner is conservative and may stop without a provable route.
- Task state comes from the ordinary Mars snapshot; voice comes from the
  existing event store. Approval has a presentation state but no authoritative
  approval adapter in the current Mars API. No fake approval/success is emitted.
- No model/agent requests occur in the frame loop. Read-only snapshot polling
  is jittered and shares the existing query key; it creates no executor/audio
  session. Multi-client/no-replay behavior needs broader live validation.
- Missing model, WebGL loss, repeated reopen/resource release, persisted
  preference across reload, audio cancellation and context-isolation live
  scenarios are still pending. Browser connection became unavailable during
  verification; successful earlier screenshots are retained as limited evidence.
- Fine surface detail and additional authored expression/gesture sources need
  visual refinement. `gigi-mark.svg` and `icon-review.html` are unapproved
  simplified-mark proposals, not final platform icons or migrated assets.
- Full active-mark migration, platform optical corrections, monochrome
  variants, generator updates and external website publication remain pending.

No visual approval has been recorded. Neither this report nor passing tests
authorize broad replacement or completion of RUB-82/RUB-69.

## Independent-review corrections

The independent review identified four issues in the initial checkpoint:
uncertain work lost its blocked presentation, a first hidden-to-focus request
could be consumed before a pose existed, reduced-motion following could stall
between periodic replans, and the compressed Blender source retained local
authoring profile metadata that a compressed-byte scan did not reveal.

Corrections derive blocked presentation directly from current unknown/interrupted
commands, consume focus only after pose publication, and immediately replan when
a reduced-motion target changes. The actual frame callback has an automated
single-short-movement/demand-loop regression using real Three.js transforms.

The authoring recipe now replaces entire fixed-size path fields with neutral
bytes before assigning portable paths, saves uncompressed, verifies every byte
and reopens the source including UI state. The 2,479,697-byte saved source had
zero local profile-path matches and zero current profile-name matches in the
verification environment. Packed-image paths are included in the sanitizer.
Tests fail closed on compressed sources rather than claiming a plaintext scan.

The earlier source was already pushed on the feature branch. Updating this file
does not erase earlier Git objects; integrate the sanitized net diff into the
main development line instead of landing the earlier source as an intermediate
commit. Historical removal requires a separately coordinated repository action.

Corrective verification: 17 companion tests and 16 Python metadata/asset/art tests
pass. The regenerated GLB is 776,764 bytes; the earlier size above belongs to the
initial checkpoint. Existing visual captures are historical reference evidence;
fresh live verification of the corrected integration remains pending.
