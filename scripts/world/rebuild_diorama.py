"""Rebuild the complete owned asset set inside Blender, without GUI or MCP.

blender -b --python scripts/world/rebuild_diorama.py -- [--buildings-only]
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/world"))
sys.path.insert(0, str(ROOT / "scripts/figures"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--buildings-only", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else [])
    kit = importlib.import_module("build_world_kit")
    pixel = importlib.import_module("pixel_kit")
    pixel.build_all(ROOT / "jarvis/ui/web/frontend/src/assets/society/world/kit", kit)
    if not args.buildings_only:
        figures = importlib.import_module("build_figures")
        sys.argv = [sys.argv[0]]
        figures.main()
    importlib.import_module("asset_inventory").write()


if __name__ == "__main__":
    main()
