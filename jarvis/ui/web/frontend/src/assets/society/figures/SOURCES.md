# Figure assets — provenance ledger

One row per shipped file (docs/agent-society/character-pipeline.md §11, gate check 15). Every
GLB here is produced by `scripts/figures/build_figures.py` from the recorded source; nothing is
hand-exported. Sources are fetched by URL into `scripts/figures/cache/` and verified by sha256
before the build touches them (`scripts/figures/sources/*.json`).

| File | Source | License | Source sha256 | What the build changed |
|---|---|---|---|---|
| `biped-medium.glb` | KayKit Character Pack: Adventurers 1.0 — `Rogue.glb` (Kay Lousberg, kaylousberg.com) | CC0-1.0 | `e825437cd4d2ee9c1960b517a74a69101e33eb409ae7fa8cedc7134a998fbb7d` | kept the six body meshes, joined them into one; dropped 18 IK/control bones; renamed 23 deform bones to the contract; kept 9 of 76 clips, renamed, root XZ zeroed, loops closed; faces re-UV'd onto the 16-cell palette strip of a generated 128×128 sheet; `FWD` marker; extras |

Licenses recorded at fetch time (2026-09-01): CC0 — free for personal, educational and commercial
use, no attribution required. Credit is given anyway: KayKit by Kay Lousberg.
