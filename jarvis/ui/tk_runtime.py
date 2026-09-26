"""Locate Tcl/Tk data for Windows launchers copied away from their interpreter."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def prepare_tk_runtime() -> None:
    """Keep Tk initialization tied to the Python runtime, not the launcher.

    A per-user branded pythonw copy can load Python and _tkinter while Tcl
    searches beside that copy for init.tcl. Use existing data from the loaded
    extension's installation. Explicit environment overrides remain intact.
    This creates no interpreter/window and is only called when opening a Tk
    surface; macOS, Linux and headless imports keep their native discovery.
    """
    if sys.platform != "win32":
        return

    import _tkinter

    roots = []
    extension_file = getattr(_tkinter, "__file__", None)
    if extension_file:
        roots.append(Path(extension_file).resolve().parent.parent)
    roots.append(Path(sys.base_prefix))
    for variable, library, version, marker in (
        ("TCL_LIBRARY", "tcl", _tkinter.TCL_VERSION, "init.tcl"),
        ("TK_LIBRARY", "tk", _tkinter.TK_VERSION, "tk.tcl"),
    ):
        if os.environ.get(variable):
            continue
        candidates = (
            root / folder / f"{library}{version}"
            for root in roots
            for folder in ("tcl", "lib")
        )
        for candidate in candidates:
            if (candidate / marker).is_file():
                os.environ[variable] = str(candidate)
                break
