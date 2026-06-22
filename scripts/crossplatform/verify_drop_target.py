"""Cross-platform drag-drop verification — runs on Windows, macOS AND Linux.

Proves, on the host it runs on, that the overlay drag-drop mechanism actually
works at the OS level:

  1. ``tkinterdnd2`` loads this OS's bundled ``tkdnd`` binary, so the factory
     returns the real :class:`TkDnDDropTarget` (not the null fallback).
  2. The path parser handles this OS's path style.
  3. ``register`` succeeds on a real Tk window (overrideredirect + color-key,
     the live-overlay window type).
  4. A ``<<Drop>>`` event runs the full chain through the process-global bridge
     to the registered handler.

Run in CI on a real ``macos-latest`` runner this is the autonomous macOS proof
that cannot be produced on a Windows dev box (no macOS container exists). Exits
non-zero on any failure so a CI step goes red.

On Linux a display is required (the workflow wraps this in ``xvfb-run``) plus the
X11 runtime libs the tkdnd ``.so`` links against (``libxcursor1`` …).
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tkinter as tk

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, relpath))
    assert spec and spec.loader, f"cannot load {relpath}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    dt = _load("dt_verify", "jarvis/overlay/drop_target.py")
    db = _load("db_verify", "jarvis/overlay/drop_bridge.py")

    print(f"platform: {sys.platform}  python: {sys.version.split()[0]}")

    # 1. the factory must pick the REAL target (tkdnd binary loaded for this OS)
    target = dt.make_drop_target()
    tname = type(target).__name__
    print(f"factory -> {tname}")
    if tname != "TkDnDDropTarget":
        print("FAIL: tkdnd did not load on this OS (got the null fallback).")
        return 2

    # 2. the path parser
    parsed = dt._parse_dnd_files("{/a b/x.txt} /c/y.png")
    print(f"parser  -> {parsed}")
    assert parsed == ["/a b/x.txt", "/c/y.png"], parsed

    # 3. + 4. register on a real bar-like Tk window, then fire the drop chain
    got: list[tuple] = []
    db.set_drop_handler(lambda paths, text: got.append((paths, text)))
    root = tk.Tk()
    root.overrideredirect(True)
    try:
        root.wm_attributes("-transparentcolor", "#ff00ff")
    except tk.TclError:
        pass  # macOS/Linux may not support color-key; the window is still valid
    canvas = tk.Canvas(root, width=220, height=44, highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    root.update_idletasks()
    root.update()

    registered = target.register(canvas, db.dispatch_drop)
    print(f"register -> {registered}")
    if not registered:
        # Surface the swallowed tkdnd error so a CI failure is diagnosable
        # (register() must never raise in production, so it logs at DEBUG).
        print("register returned False — raw tkdnd diagnostic:")
        try:
            import tkinter as _tk

            from tkinterdnd2 import DND_FILES, TkinterDnD

            print(f"  Tcl/Tk patchlevel: {root.tk.call('info', 'patchlevel')}")
            TkinterDnD._require(root)
            print("  _require OK; trying drop_target_register ...")
            canvas.drop_target_register(DND_FILES)
            print("  drop_target_register OK (unexpected success)")
        except Exception as exc:  # noqa: BLE001 — diagnostic only
            import traceback

            traceback.print_exc()
            print(f"  RAW tkdnd error: {type(exc).__name__}: {exc}")
        print("FAIL: tkdnd registration failed on this OS.")
        root.destroy()
        return 3

    canvas.event_generate("<<Drop>>", data="/etc/hostname")
    root.update()
    root.destroy()
    db.set_drop_handler(None)

    print(f"drop chain fired -> handler got {len(got)} call(s): {got}")
    if not got:
        print("FAIL: the <<Drop>> chain did not reach the handler.")
        return 4

    print("OK: drag-drop works on this OS (load + parse + register + chain).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
