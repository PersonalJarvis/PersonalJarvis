"""Create an isolated native smoke configuration through Jarvis's atomic writer."""

from __future__ import annotations

import argparse
from pathlib import Path

from jarvis.core.config_writer import _WRITE_LOCK, _atomic_write


def write(path: Path, port: int) -> None:
    if not 1 <= port <= 65535:
        raise ValueError("invalid smoke port")
    with _WRITE_LOCK:
        _atomic_write(path, f"[ui]\nadmin_api_port = {port}\n# Native release smoke setting\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("port", type=int)
    args = parser.parse_args()
    write(args.path, args.port)


if __name__ == "__main__":
    main()
