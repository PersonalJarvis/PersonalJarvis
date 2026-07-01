"""Native OS file drag-source — drag a REAL file out of the desktop WebView.

The desktop UI runs inside a WebView (pywebview: WebView2 on Windows, WKWebView
on macOS, WebKitGTK on Linux). A WebView cannot drag a real file OUT of the
window via HTML5 drag-and-drop, and pywebview ships no drag-out API
(r0x0r/pywebview#877, #1192). So we start the native OS drag ourselves.

**The one thing that makes this work (and the reason a naive attempt fails):**
``DoDragDrop`` runs a modal OLE loop that grabs the mouse and reads mouse
messages from the *calling thread's* queue — so it must run on the WebView2 host
**UI thread** while the physical mouse button is still down. pywebview's
``js_api`` bridge runs exposed methods on a *worker* thread, so starting the drag
there never takes over the in-progress press. Instead the frontend posts a raw
``window.chrome.webview.postMessage([TAG, {files}])`` on ``mousedown``; WebView2
delivers that to ``EdgeChrome.on_script_notify`` **on the UI thread**, and we run
``DoDragDrop`` there. This is the technique shipped by Tiefsee4 (hbl917070) and
ported to pywebview by FeralFox — verified against WebView2Feedback#2313.

Cross-platform, capability-gated, fail-closed to a logged no-op:
* Windows: monkeypatch ``EdgeChrome.on_script_notify`` → WinForms ``DataObject``
  (which packs CF_HDROP for us) → ``webview.DoDragDrop(..., Copy)``.
* macOS / Linux: Phase-2 seams (NSFilePromiseProvider / GTK drag-source) — honest
  no-op until wired.

Path safety (AP-2 spirit): a path posted by the renderer is dragged only if it
resolves to an existing regular file inside an explicit allow-list of base dirs
(the caller passes ``~/Downloads`` etc.), so a compromised page cannot exfiltrate
arbitrary files.
"""
from __future__ import annotations

import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

log = logging.getLogger(__name__)

# Discriminator the frontend puts as the first element of its postMessage array.
# Kept in sync with the frontend helper (src/lib/nativeDrag.ts).
MESSAGE_TAG = "jarvis-file-drag"

# Windows DROPEFFECT: COPY (never MOVE — the source download must stay put).
_DROPEFFECT_COPY = 1


def _resolve_bases(allowed_base_dirs: Sequence[Path] | None) -> list[Path] | None:
    if allowed_base_dirs is None:
        return None
    bases: list[Path] = []
    for b in allowed_base_dirs:
        try:
            bases.append(Path(b).resolve())
        except OSError:
            continue
    return bases


def _validate_paths(paths: Sequence[str], bases: list[Path] | None) -> list[str]:
    """Return the absolute existing regular files that pass the allow-list.

    A path survives only if it resolves to an existing regular file and — when
    ``bases`` is given — sits inside one of them. Returned as native OS path
    strings (back-slashes on Windows) for the drag data object.
    """
    out: list[str] = []
    for raw in paths:
        try:
            p = Path(raw).resolve()
        except OSError:
            continue
        if not p.is_file():
            log.debug("native_drag: not a regular file: %r", raw)
            continue
        if bases is not None and not any(
            p == base or base in p.parents for base in bases
        ):
            log.debug("native_drag: %s outside allow-list — skipped", p)
            continue
        out.append(str(p))
    return out


def install_native_drag(allowed_base_dirs: Sequence[Path] | None = None) -> bool:
    """Wire native file drag-out into the desktop WebView. Call once before
    ``webview.start()``. Returns True if a drag source was installed for this OS.

    Windows-only for now; macOS/Linux return False (honest no-op) until their
    seams land. Never raises — a missing dependency / unexpected pywebview
    internal just disables the feature and logs.
    """
    if sys.platform == "win32":
        return _install_windows_drag(allowed_base_dirs)
    # Phase 2: _install_macos_drag / _install_linux_drag.
    log.info("native_drag: no drag source for platform %s yet", sys.platform)
    return False


def _install_windows_drag(allowed_base_dirs: Sequence[Path] | None) -> bool:
    """Monkeypatch pywebview's WebView2 message handler to start an OLE file drag
    on the UI thread when the frontend posts a ``[MESSAGE_TAG, {files}]`` message.
    """
    try:
        import clr

        clr.AddReference("System.Windows.Forms")
        clr.AddReference("System.Collections")
        import System.Windows.Forms as WinForms  # noqa: N813
        import webview.platforms.edgechromium as ec
        from System import Array
    except Exception as exc:  # noqa: BLE001 — .NET/pywebview internals absent
        log.warning("native_drag: WebView2 drag bridge unavailable (%s)", exc)
        return False

    if getattr(ec.EdgeChrome, "_jarvis_drag_installed", False):
        return True  # idempotent across restarts / duplicate calls

    bases = _resolve_bases(allowed_base_dirs)
    original = ec.EdgeChrome.on_script_notify

    def _patched_on_script_notify(self, sender, args):  # noqa: ANN001, ANN202
        # Decide FIRST whether this is our drag message; anything else (including a
        # parse error) falls through UNTOUCHED to pywebview's original handler so
        # the js_api bridge / drop-in feature keep working.
        try:
            data = json.loads(args.get_WebMessageAsJson())
            is_drag = (
                isinstance(data, list)
                and len(data) >= 2
                and data[0] == MESSAGE_TAG
                and isinstance(data[1], dict)
            )
        except Exception:  # noqa: BLE001 — not JSON / not ours
            is_drag = False

        if not is_drag:
            return original(self, sender, args)

        # Our drag message — handled on the UI thread (this callback). Any failure
        # is contained here; we never re-dispatch to the original for a drag msg.
        try:
            raw_files = data[1].get("files", [])
            paths = _validate_paths([str(p) for p in raw_files], bases)
            if paths:
                obj = WinForms.DataObject(
                    WinForms.DataFormats.FileDrop, Array[str](paths)
                )
                # THE fix: DoDragDrop on the WebView2 UI thread, mouse still down.
                self.webview.DoDragDrop(obj, WinForms.DragDropEffects.Copy)
        except Exception:  # noqa: BLE001 — a drag must never crash the UI thread
            log.exception("native_drag: DoDragDrop failed")
        return None

    ec.EdgeChrome.on_script_notify = _patched_on_script_notify
    ec.EdgeChrome._jarvis_drag_installed = True
    log.info("native_drag: WebView2 file drag-out installed")
    return True
