"""Real paths for files and folders dropped ONTO the desktop WebView.

A web page never learns where a dropped folder lives: the browser hands it a
``File`` with a name and nothing else, by design. In a plain browser tab that is
the end of it and the UI falls back to searching for the name. Inside the
desktop shell we can do better, because the host process sees the native drop
and knows the full path — pywebview exposes it as ``pywebviewFullPath`` on every
file of a ``drop`` event delivered to a Python handler, on Windows (WebView2),
macOS (WKWebView) and Linux (WebKitGTK) alike.

So this module registers exactly such a handler on each window's ``window``
object and, whenever it fires, tells the page: it dispatches a
``jarvis-native-drop`` DOM event whose ``detail`` carries the resolved paths
(and the names they belong to). Any component that handles drops can wait a
moment for that event and use the exact path instead of guessing — the folder
picker in the Agentic IDE does. Outside the shell the event never comes and the
component simply keeps its browser fallback.

Registered on ``window``, not ``body``: pywebview serialises the whole event
including ``currentTarget``, and for ``window`` that is one short string where
``body`` would be the entire document.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

log = logging.getLogger(__name__)

# The DOM event the page listens for. Kept in sync with the frontend helper
# (src/lib/nativeDrop.ts).
EVENT_NAME = "jarvis-native-drop"


def dropped_paths(event: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """The full paths and names of the files in a serialised ``drop`` event.

    Pure, so it can be tested without a window. Files pywebview could not
    resolve (no ``pywebviewFullPath``) are skipped rather than reported as an
    empty path — an empty path is not a hint the page can act on.
    """
    data_transfer = event.get("dataTransfer")
    files = data_transfer.get("files") if isinstance(data_transfer, Mapping) else None
    paths: list[str] = []
    names: list[str] = []
    for item in files or []:
        if not isinstance(item, Mapping):
            continue
        path = item.get("pywebviewFullPath")
        if not isinstance(path, str) or not path:
            continue
        paths.append(path)
        name = item.get("name")
        names.append(name if isinstance(name, str) else "")
    return paths, names


def announce_script(paths: list[str], names: list[str]) -> str:
    """The JavaScript that tells the page what was dropped."""
    detail = json.dumps({"paths": paths, "names": names})
    return f"window.dispatchEvent(new CustomEvent({json.dumps(EVENT_NAME)}, {{detail: {detail}}}));"


def register_native_drop(window: Any) -> bool:
    """Wire the drop handler into ``window``'s CURRENT page.

    Call from the window's ``loaded`` hook — pywebview forgets DOM handlers on
    every load, so this must run again each time the page (re)loads. Never
    raises: a shell without the DOM API (or a stub window in tests) just does
    not get the feature, and the browser fallback still stands.
    """
    dom = getattr(window, "dom", None)
    if dom is None:
        log.debug("native_drop: this window has no DOM bridge — browser fallback stays")
        return False

    def _on_drop(event: Mapping[str, Any]) -> None:
        try:
            paths, names = dropped_paths(event)
            if not paths:
                return
            window.evaluate_js(announce_script(paths, names))
        except Exception:  # noqa: BLE001 — a drop must never take the shell down
            log.exception("native_drop: could not hand the dropped paths to the page")

    try:
        dom.window.on("drop", _on_drop)
    except Exception as exc:  # noqa: BLE001 — pywebview internals vary by backend
        log.warning("native_drop: drop bridge unavailable in this window (%s)", exc)
        return False
    log.debug("native_drop: drop bridge registered")
    return True


__all__ = ["EVENT_NAME", "announce_script", "dropped_paths", "register_native_drop"]
