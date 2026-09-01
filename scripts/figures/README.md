# scripts/figures — the character build

The build side of [`docs/agent-society/character-pipeline.md`](../../docs/agent-society/character-pipeline.md).

- `contract.json` — archetypes, bone sets, clip sets, sheet layout, budgets, build targets.
  The CI gate (`scripts/ci/check_society_figures.py`) reads the same file.
- `sources/<name>.json` — one recorded source per asset pack: URL, sha256 per file, license,
  bone map, clip map, per-character atlas-cell → palette-cell map, default palette.
- `glb_tools.py` — stdlib GLB reader/writer, pruning, PNG codec, clip sampling and the stride
  measurement. Shared by the build's finish step and the gate; no third-party imports.
- `build_figures.py` — runs INSIDE Blender (5.0+), headless:

```
blender -b --python scripts/figures/build_figures.py -- --target biped-medium
```

  Output lands in `jarvis/ui/web/frontend/src/assets/society/figures/`; every produced file is
  validated by the gate before the script exits 0.
- `cache/` — fetched sources and generated sheets; gitignored.

Adding a character from an existing source = a `targets` entry in `contract.json` plus a
`characters` entry (body meshes, cell map, palette) in the source file. No code.
