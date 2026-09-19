# Recipe-derived agent portraits

Status: design contract for the new Mars character family. Visual approval is
pending. This document does not approve a character style or production rollout.

## Why the current shortcut fails

The compact avatar already calls `faceCrop` with the saved `FigureRecipe`, so
the basic identity link exists. Its source art is the limitation: the current
Blockhead base has 324 triangles, and the existing character sheets are 128 px
with nearest sampling. Enlarging that head, smoothing its edges, or drawing an
unrelated vector face cannot produce a detailed, matching portrait. The
separate generic vector portrait was reviewed and removed.

## One identity, two camera distances

The approved new full-body character is the source of truth. Its close-detail
head, materials, rig, hair and face equipment must also support a portrait
camera. Agent creation passes the same base/model, palette and part selections
to the world viewer and portrait renderer. A change to glasses, helmet, hair,
skin or clothing updates both previews in the same editing frame. The person
does not need a second set of face controls or an image model.

The asset contract to implement with the new character family is:

- A documented portrait focus anchor and frontal axis on the character rig.
- A close-detail head whose face and materials hold up at 256 px; the world
  may use lower detail at distance, but transitions preserve identity.
- Named material regions that map to the existing palette cells, plus explicit
  head/face equipment attachments mapped from the saved part IDs.
- A capability marker for portrait-ready assets. Legacy figures and imported
  models without the required anchor/material contract use an honest existing
  head crop or a person-selected image, never a fabricated matching likeness.
- An offline, fingerprinted asset build. End users need no Blender install,
  image generation service, additional account or network call.

The browser renders the configured model at portrait distance with colour
management and antialiasing, captures a 256 px square and downsamples for the
34 px roster. It caches by the complete visual recipe and releases its WebGL
context. Upload remains an optional override. A renderer or WebGL failure
must not block agent creation, app boot or headless operation.

This is a **T3 asset/interface contract** when implemented: the new metadata,
palette/material mapping and fallback behavior need Windows, macOS and Linux
coverage, per-family contract tests, OS parity documentation and fresh-install
verification under the repository rules.

## Visual gate and dependencies

The first representative figure must be finished in the new Mars art pipeline
before portrait rollout. Review the full-body character and its portrait
together in the actual application: normal world distance, close inspection,
front/side/back, and the same configuration at 34, 88 and 256 px. Check at
least base, palette, glasses/headgear and one role variant; include light and
dark app appearances. Record asset hashes, hardware, viewport and measured
render cost. The user approves that specific runtime reference before the
remaining families are produced.

RUB-71 owns the unresolved worker visual direction, RUB-79 the reproducible
asset pipeline, RUB-81 the new characters and rigs, and RUB-88 the integrated
visual acceptance gate. The current pixel figures are temporary compatibility
assets, not the visual reference for this contract.
