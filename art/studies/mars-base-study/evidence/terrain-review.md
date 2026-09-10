# Terrain study review

Stage: terrain and circulation blockout. Artistic acceptance: pending.
Production Mars runtime and final buildings: not implemented by this study.

## Product restoration

The rejected City selector, lazy import and runtime source folder were removed.
Saved City preference migrates to World. Existing World/Ledger preference handling
and Ledger fallback without WebGL remain. Original figures, recipes and authored
Blender assets were retained. The city study is explicitly retired.

The frontend passed TypeScript and Vite production builds. A clean build from
committed sources plus this change contains no CityStage chunk. Working files and
the live bundle belonging to other sessions were preserved. Browser inspection on
the running local application confirmed the rendered original world, switching to
Ledger and back, and World in both light and dark appearance. Dark appearance was
restored after inspection. No desktop restart was performed.

## Spatial proposal and limitations

`layout.json` defines seven labelled site reservations and five routing junctions.
The connected graph spans 430 by 365 m with a 24 m difference between site levels.
Ground routes total approximately 987 m; the bridge is 80 m. Designed ramp grade
is measured between flat pad edges, not centre-to-centre. See `layout-check.json`
for measured values and the separate checks of the triangulated surface.

`terrain-overview.png` and `terrain-plan.png` are Blender renders of the map only.
Plain site areas and route colours communicate reserved space and circulation;
they are not building designs, final materials or application screenshots.
The source is editable; the exported GLB stays inside the study.

The initial unconstrained grid let separately sampled route ribbons float up to
2.68 m above the actual terrain. The revised triangulation includes pad boundaries,
ground-route borders and ramp breaks in one mesh. Ground routes and pads are
material regions of that surface; only the bridge is separate. Its deck is trimmed
to the pad boundaries. There are no floating ground ribbons or pad cylinders.

The revised mesh passed 6,150 independent vertical ray samples against its actual
triangles, including road edges, pad boundaries and the original defect coordinates.
Maximum sampled deviation was 0.031 m against a 0.05 m limit, with no missed rays
or violations. The map contains 92,116 triangles including the bridge. These checks
establish sampled geometric consistency, not complete collision or movement proof.

Validation: 25 frontend tests for preference migration/WebGL handling and 31 Python
tests for the map helpers/art pipeline passed. The Blender build rejects a mesh
whose contact deviation exceeds the limit and stores the actual check separately
from the analytic graph report.

## Required next proof

Load the study in an isolated renderer, constrain the camera to the terrain and
operational envelope, and verify one real agent journey between Command and
Compute plus the bridge/ridge journey. Check ground contact on the exported mesh,
blocked routes, height changes, cancellation and visibility catch-up. Record actual
runtime views and movement. Integrated-GPU frame rate is still unmeasured.

Only after that map proof should one small finished architectural reference be
authored and reviewed in the runtime. No visual or asset-family rollout approval
is recorded here.
