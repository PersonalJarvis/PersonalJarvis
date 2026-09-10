"""Start a connector without starting the desktop app or any voice component."""

import argparse
import asyncio
import logging
from pathlib import Path

from .connector import run_connector


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hub", required=True, help="Secure hub origin (wss://host:port)")
    parser.add_argument(
        "--pair", action="store_true", help="Enter a new one-time code using a hidden prompt"
    )
    parser.add_argument(
        "--data-dir", type=Path, default=Path.home() / ".personaljarvis" / "connector"
    )
    parser.add_argument(
        "--desktop",
        choices=["none", "own", "attached", "both"],
        default="none",
        help="Explicitly enable isolated or visible desktop access",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_connector(args.hub, args.data_dir, pair=args.pair, desktop_mode=args.desktop))


if __name__ == "__main__":
    main()
