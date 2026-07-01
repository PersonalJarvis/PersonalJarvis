"""Native OS file drag-source — start an OS-level "copy this file" drag.

The desktop UI renders inside a WebView (pywebview: WebView2/Edge Chromium on
Windows, WKWebView on macOS, WebKitGTK on Linux). A WebView has **no built-in
way to drag a real file OUT** of the window — it is a long-standing, still-open
pywebview feature request (r0x0r/pywebview#877, #1192). So we start the native
drag ourselves: the frontend arms a JS ``mousedown`` handler that calls
``window.pywebview.api.begin_file_drag(path)`` (see :class:`DragBridge`), which
hands a **real file** to the OS drag loop. Because it is a real OS file (not a
browser blob / DownloadURL trick), EVERY drop target accepts it — Explorer /
Finder, any folder, a browser upload zone, a chat window.

Design rules honoured (CLOUD.md / §3 open-source universality):

* **Capability-gated, fail-closed.** An unsupported platform, a missing native
  dependency, or a bad path returns a status STRING and never raises into the UI
  thread. On a headless VPS this is simply never wired up.
* **Path safety.** Only an existing regular file inside an explicit allow-list of
  base directories may be dragged (defense in depth against a compromised
  renderer exfiltrating arbitrary files — AP-2 spirit). The caller passes the
  allow-list; ``None`` means "any existing regular file" (used only by the
  isolated feasibility spike, never in-app).

Status tokens returned by :func:`start_file_drag` (all strings, never raise):

* ``"ok:copy"`` / ``"ok:none"``  — drag ran; a target accepted it / it was cancelled.
* ``"noop:unsupported"``          — this OS has no implementation yet.
* ``"noop:no-deps"``             — the native dependency is missing.
* ``"noop:no-valid-paths"``      — nothing draggable survived validation.
* ``"error:<detail>"``           — an unexpected failure, already logged.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

log = logging.getLogger(__name__)

# Windows DROPEFFECT flags (objidl.h). COPY is what a "save this file elsewhere"
# gesture means; we never offer MOVE (the source file must stay in Downloads).
_DROPEFFECT_COPY = 1


def _validate_paths(
    paths: Sequence[str], allowed_base_dirs: Sequence[Path] | None
) -> list[str]:
    """Return the absolute, existing, regular-file paths that pass the allow-list.

    A path survives only if it resolves to an existing regular file and — when
    ``allowed_base_dirs`` is given — sits inside one of those directories. Any
    path that fails is dropped (and logged at DEBUG); an empty result makes the
    caller a no-op. ``allowed_base_dirs`` of ``None`` skips the containment check
    (spike-only).
    """
    out: list[str] = []
    bases: list[Path] | None = None
    if allowed_base_dirs is not None:
        bases = []
        for b in allowed_base_dirs:
            try:
                bases.append(Path(b).resolve())
            except OSError:
                continue
    for raw in paths:
        try:
            p = Path(raw).resolve()
        except OSError:
            log.debug("native_drag: unresolvable path %r — skipped", raw)
            continue
        if not p.is_file():
            log.debug("native_drag: not a regular file %s — skipped", p)
            continue
        if bases is not None and not any(
            p == base or base in p.parents for base in bases
        ):
            log.debug("native_drag: %s outside allow-list — skipped", p)
            continue
        out.append(str(p))
    return out


def start_file_drag(
    paths: Sequence[str],
    *,
    allowed_base_dirs: Sequence[Path] | None = None,
) -> str:
    """Start a native OS file-copy drag for *paths*. Blocks until the drag ends.

    Returns a status token (see module docstring); never raises. Dispatches to
    the per-OS implementation; unknown platforms are a logged no-op so a Linux
    desktop without the native seam still boots and simply offers no drag.
    """
    valid = _validate_paths(paths, allowed_base_dirs)
    if not valid:
        return "noop:no-valid-paths"
    try:
        if sys.platform == "win32":
            return _win_start_file_drag(valid)
        if sys.platform == "darwin":
            return _mac_start_file_drag(valid)
        if sys.platform.startswith("linux"):
            return _linux_start_file_drag(valid)
    except Exception as exc:  # noqa: BLE001 — a drag must never crash the UI thread
        log.warning("native_drag failed on %s: %s", sys.platform, exc, exc_info=True)
        return f"error:{type(exc).__name__}"
    return "noop:unsupported"


# --------------------------------------------------------------------------- #
# Windows — OLE DoDragDrop with a Shell IDataObject (CF_HDROP + virtual files). #
# --------------------------------------------------------------------------- #
def _win_start_file_drag(paths: list[str]) -> str:
    """Run an OLE drag on a dedicated STA thread and wait for it to finish.

    ``DoDragDrop`` runs its own modal message loop and must live on an
    OLE-initialised STA thread; the pywebview ``js_api`` call arrives on a worker
    thread, so we spin up a fresh STA thread per drag, build the Shell data
    object there (same apartment), run the drag, and join. The Shell data object
    gives us CF_HDROP **and** the virtual-file formats, so Explorer, folders and
    browser upload zones all accept the drop.
    """
    try:
        import pythoncom  # noqa: F401  # part of pywin32 ([desktop] extra)
        from win32com.shell import shell  # noqa: F401
    except Exception:  # noqa: BLE001 — pywin32 absent (e.g. slim/headless install)
        return "noop:no-deps"

    result: dict[str, str] = {"status": "error:no-result"}

    def _run() -> None:
        import pythoncom
        import win32con
        import winerror
        from win32com.server.util import wrap
        from win32com.shell import shell

        pythoncom.OleInitialize()  # STA + OLE for this thread
        try:
            data_object = _win_shell_data_object(paths)
            if data_object is None:
                result["status"] = "error:no-data-object"
                return

            class _DropSource:
                _public_methods_ = ["QueryContinueDrag", "GiveFeedback"]
                _com_interfaces_ = [pythoncom.IID_IDropSource]

                def QueryContinueDrag(self, escape_pressed, key_state):  # noqa: N802, ANN001
                    if escape_pressed:
                        return winerror.DRAGDROP_S_CANCEL
                    # Left button released -> commit the drop at the cursor.
                    if not (key_state & win32con.MK_LBUTTON):
                        return winerror.DRAGDROP_S_DROP
                    return winerror.S_OK  # keep dragging

                def GiveFeedback(self, effect):  # noqa: N802, ANN001
                    return winerror.DRAGDROP_S_USEDEFAULTCURSORS

            drop_source = wrap(_DropSource(), pythoncom.IID_IDropSource)
            effect = pythoncom.DoDragDrop(data_object, drop_source, _DROPEFFECT_COPY)
            result["status"] = "ok:copy" if effect else "ok:none"
        except Exception as exc:  # noqa: BLE001
            log.warning("Windows DoDragDrop failed: %s", exc, exc_info=True)
            result["status"] = f"error:{type(exc).__name__}"
        finally:
            pythoncom.OleUninitialize()

    t = threading.Thread(target=_run, name="native-file-drag", daemon=True)
    t.start()
    # Join with a generous ceiling: a real drag ends on mouse-up in seconds; the
    # ceiling only guards a wedged loop so the js_api call cannot hang forever.
    t.join(timeout=120.0)
    if t.is_alive():
        return "error:drag-timeout"
    return result["status"]


def _win_shell_data_object(paths: list[str]):  # noqa: ANN201 — pywin32 PyIDataObject
    """Build a Shell ``IDataObject`` for *paths* (files in the SAME folder).

    Uses the parent folder's ``IShellFolder`` + ``GetUIObjectOf`` so the OS
    supplies a full-featured data object (CF_HDROP + virtual file streams). All
    ``paths`` are assumed to share one parent directory (our callers drag a
    single downloaded file; multi-file drags from one folder also work). Returns
    ``None`` if the folder / children cannot be resolved.
    """
    import pythoncom
    from win32com.shell import shell

    parent_dir = os.path.dirname(paths[0])
    names = [os.path.basename(p) for p in paths]

    desktop = shell.SHGetDesktopFolder()
    _eaten, folder_pidl, _attr = desktop.ParseDisplayName(0, None, parent_dir)
    folder = desktop.BindToObject(folder_pidl, None, shell.IID_IShellFolder)

    child_pidls = []
    for name in names:
        _eaten, child_pidl, _attr = folder.ParseDisplayName(0, None, name)
        child_pidls.append(child_pidl)

    # pywin32's GetUIObjectOf returns ``(rgfReserved, interface)`` — a 2-tuple —
    # not the bare interface. Unwrap robustly: pick the member that quacks like an
    # IDataObject (has QueryGetData), so a future pywin32 that returns the object
    # directly also works.
    obj = folder.GetUIObjectOf(0, child_pidls, pythoncom.IID_IDataObject, 0)
    if isinstance(obj, tuple):
        for member in obj:
            if hasattr(member, "QueryGetData"):
                return member
        return None
    return obj


# --------------------------------------------------------------------------- #
# macOS / Linux — Phase 2 native seams (honest no-op until implemented).        #
# --------------------------------------------------------------------------- #
def _mac_start_file_drag(paths: list[str]) -> str:
    """macOS NSDraggingSession seam — implemented in Phase 2 (PyObjC)."""
    return "noop:unsupported"


def _linux_start_file_drag(paths: list[str]) -> str:
    """Linux GTK drag-source seam — implemented in Phase 2 (PyGObject)."""
    return "noop:unsupported"


# --------------------------------------------------------------------------- #
# pywebview JS bridge — exposed as ``window.pywebview.api``.                     #
# --------------------------------------------------------------------------- #
class DragBridge:
    """``js_api`` object the WebView calls to begin a native file drag.

    ``allowed_base_dirs`` locks the draggable set to real download/output
    locations so a path coming from the renderer can never point elsewhere.
    ``begin_file_drag`` blocks (on its own STA thread inside ``start_file_drag``)
    until the drag ends and returns the status token to the JS Promise.
    """

    def __init__(self, allowed_base_dirs: Sequence[Path] | None) -> None:
        self._allowed = list(allowed_base_dirs) if allowed_base_dirs else None

    def begin_file_drag(self, path: str) -> str:  # noqa: D401 — JS-facing name
        """Begin a native OS copy-drag of the single file *path*."""
        if not isinstance(path, str) or not path:
            return "noop:no-valid-paths"
        return start_file_drag([path], allowed_base_dirs=self._allowed)
