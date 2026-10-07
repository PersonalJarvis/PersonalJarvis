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
    # Accepted and ignored: a main process started before the picker lost its
    # banner still passes it until the app restarts.
    parser.add_argument("--hint", default="")
    # The toolbar's tooltip language ([ui].language); English when absent.
    parser.add_argument("--lang", default="en")
    args = parser.parse_args(argv)
    try:
        from jarvis.appshot.picker.renderer import run  # noqa: PLC0415
    except ImportError as exc:
        sys.stderr.write(
            f"appshot-picker: PySide6 unavailable ({exc}). Install the [desktop] "
            "extra to select an area for an appshot.\n"
        )
        return EXIT_NO_GUI
    return run(args.lang)


if __name__ == "__main__":
    raise SystemExit(main())
