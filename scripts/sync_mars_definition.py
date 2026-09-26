"""Copy or verify the frontend projection of the packaged canonical Mars layout."""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "jarvis/society/mars/definition.json"
TARGET = ROOT / "jarvis/ui/web/frontend/src/components/society/mars/worldDefinition.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    source = SOURCE.read_bytes()
    if args.check:
        if not TARGET.is_file() or TARGET.read_bytes() != source:
            print("Mars definition differs; run python scripts/sync_mars_definition.py")
            return 1
        print("Mars definition projection matches")
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_bytes(source)
    print("Updated frontend Mars definition projection")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
