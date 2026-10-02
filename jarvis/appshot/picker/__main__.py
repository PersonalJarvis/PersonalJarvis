"""Sidecar entry: ``python -m jarvis.appshot.picker``.

Guards the PySide6 import so a host without the GUI stack exits with
``EXIT_NO_GUI`` and one English line on stderr instead of a traceback.
"""

from __future__ import annotations

import argparse
import sys

from jarvis.appshot.picker import EXIT_NO_GUI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis.appshot.picker")
    parser.add_argument("--hint", default="")
    args = parser.parse_args(argv)
    try:
        from jarvis.appshot.picker.renderer import run  # noqa: PLC0415
    except ImportError as exc:
        sys.stderr.write(
            f"appshot-picker: PySide6 unavailable ({exc}). Install the [desktop] "
            "extra to select an area for an appshot.\n"
        )
        return EXIT_NO_GUI
    return run(hint=args.hint)


if __name__ == "__main__":
    raise SystemExit(main())
