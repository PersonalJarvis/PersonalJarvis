"""Cross-platform drag-drop target for the floating overlay (bar + mascot).

The desktop power-user extra that lets a user drop files / text **directly onto
the always-on-top Tk bar or mascot** (not just the in-app web dock). It follows
the AD-6 seam shape used across ``jarvis/overlay/``: a ``DropTarget`` Protocol, a
real implementation, a graceful null no-op, and a ``make_drop_target()`` factory
that never raises.

Cross-platform by construction: the real implementation drives the **tkdnd** Tcl
extension via ``tkinterdnd2`` (bundled binaries for Windows / macOS / Linux,
verified loading + registering on Windows and Linux), registered onto the
existing Tk root **without replacing it** (``_require`` on the live root —
compatible with the cached-root constraint, BUG-031). Where ``tkinterdnd2`` / the
``tkdnd`` binary fails to load (headless €5-VPS, an arch without a bundled binary,
or — on Linux — the X11 runtime libs the tkdnd ``.so`` links against are absent),
``register`` returns ``False`` and the factory/overlay degrade to a logged no-op
— the web dock (``POST /api/chat/drop``) still carries the feature on every OS,
so this extra is purely additive.

**Linux runtime note (verified 2026-06-22):** the bundled ``linux-x64``
``libtkdnd2.9.5.so`` links against X11 libs (``libXcursor.so.1`` et al.). A real
Linux *desktop* running X11 has these; a bare ``python:3.11-slim`` does not, so
``register`` returns ``False`` there (correctly — a headless box has no overlay
anyway). Distro packages: ``libxcursor1 libxrender1 libxext6`` (+ ``python3-tk``).

Import-cleanliness (HN-7): nothing here imports ``tkinterdnd2`` / Tk at module
scope, so ``import jarvis.overlay.drop_target`` stays clean on a headless host.
The dropped content is delivered to an ``on_drop(paths, text)`` callback; wiring
that callback to the brain intake (``jarvis/brain/drop_context.ingest_drop``)
lives in the desktop bridge, off this module.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)

#: ``on_drop`` is called with (file_paths, dragged_text). Exactly one is
#: populated per drop: a file drop → (paths, ""), a text/URL drop → ([], text).
OnDrop = Callable[[list[str], str], None]


@runtime_checkable
class DropTarget(Protocol):
    """Registers OS drag-drop on a Tk widget. ``register`` never raises."""

    def register(self, widget: Any, on_drop: OnDrop) -> bool:
        """Wire ``on_drop`` to drops on ``widget``; True if actually registered."""
        ...


def _parse_dnd_files(data: str) -> list[str]:
    """Parse a tkdnd ``<<Drop>>`` file payload into a list of paths.

    tkdnd hands a Tcl list: space-separated paths, with any path that contains a
    space wrapped in ``{}``. e.g. ``{C:/a b/one.txt} C:/c/two.png``.
    """
    s = data.strip()
    if not s:
        return []
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        while i < n and s[i].isspace():
            i += 1
        if i >= n:
            break
        if s[i] == "{":
            j = s.find("}", i + 1)
            if j == -1:
                out.append(s[i + 1 :].strip())
                break
            out.append(s[i + 1 : j])
            i = j + 1
        else:
            j = i
            while j < n and not s[j].isspace():
                j += 1
            out.append(s[i:j])
            i = j
    return [p for p in out if p]


class NullDropTarget:
    """No-op target — overlay drop is unavailable on this host (logged once)."""

    def register(self, widget: Any, on_drop: OnDrop) -> bool:  # noqa: ARG002
        log.debug("Overlay drop target unavailable (tkdnd absent) — no-op.")
        return False


class TkDnDDropTarget:
    """Drives the cross-platform ``tkdnd`` extension via ``tkinterdnd2`` (AD-6).

    Registered onto the live Tk root in place (``_require``), so it composes with
    the overlay's cached-root lifecycle. Any failure (missing binary, Tcl error)
    degrades to ``False`` — the overlay keeps working, drop is simply off.
    """

    def register(self, widget: Any, on_drop: OnDrop) -> bool:
        try:
            import os

            from tkinterdnd2 import COPY, DND_FILES, DND_TEXT, TkinterDnD  # lazy (HN-7)

            root = widget.winfo_toplevel()
            # Load the tkdnd Tcl package into the EXISTING root — no new Tk root
            # (BUG-031 cached-root constraint). Idempotent across overlays.
            TkinterDnD._require(root)

            # Remember the resting opacity so the hover highlight can dim back.
            try:
                rest_alpha = float(root.wm_attributes("-alpha"))
            except Exception:  # noqa: BLE001
                rest_alpha = 0.6

            def _highlight(on: bool) -> None:
                # Visible drag feedback (window-level only — never fights the
                # renderer): the pill goes fully opaque while a file hovers, so
                # the user SEES exactly where the (small, frameless) drop zone is.
                try:
                    root.wm_attributes("-alpha", 1.0 if on else rest_alpha)
                except Exception:  # noqa: BLE001, S110 — cosmetic; never crash a drop
                    pass

            def _accept(event: Any) -> str:
                # DropEnter/DropPosition MUST return the accepted action, else
                # tkdnd rejects the drag and the OS shows the "no-drop" cursor.
                # The log line is the key live diagnostic that the OS is
                # delivering drag events to this frameless/topmost/color-key window.
                action = str(getattr(event, "action", "") or COPY)
                _highlight(True)
                log.info("overlay DRAG over bar (tkdnd delivering) action=%s", action)
                return action

            def _leave(_event: Any) -> None:
                _highlight(False)

            def _on_drop(event: Any) -> None:
                _highlight(False)
                try:
                    data = str(getattr(event, "data", "") or "")
                    paths = _parse_dnd_files(data)
                    real = [p for p in paths if os.path.exists(p)]
                    log.info(
                        "overlay DROP received: %d token(s), %d real file(s)",
                        len(paths), len(real),
                    )
                    if real:
                        on_drop(real, "")
                    elif data.strip():
                        on_drop([], data.strip())
                except Exception:  # noqa: BLE001 — a drop callback must never crash Tk.
                    log.debug("overlay drop handler failed", exc_info=True)

            widget.drop_target_register(DND_FILES, DND_TEXT)
            # Catch-all ``<<Drop>>`` (robust across tkdnd versions / window types;
            # the type-specific ``<<Drop:DND_Files>>`` can silently not fire on a
            # frameless color-key window). DropEnter/DropPosition return the action
            # so the OS accepts the drag (turns the no-drop cursor into "copy").
            widget.dnd_bind("<<Drop>>", _on_drop)
            widget.dnd_bind("<<DropEnter>>", _accept)
            widget.dnd_bind("<<DropPosition>>", _accept)
            widget.dnd_bind("<<DropLeave>>", _leave)
            log.info("Overlay drop target registered (tkdnd).")
            return True
        except Exception:  # noqa: BLE001 — AD-6: registration must never crash the overlay.
            log.debug(
                "Overlay drop target registration failed; drop disabled this "
                "session (the overlay itself is unaffected).",
                exc_info=True,
            )
            return False


def make_drop_target() -> DropTarget:
    """Select the overlay drop backend for this host. **Never raises** (AD-6).

    Returns :class:`TkDnDDropTarget` when ``tkinterdnd2`` is importable, else the
    :class:`NullDropTarget` floor. Uses ``find_spec`` so ``tkinterdnd2`` is not
    imported at module scope (HN-7) and a missing package is a clean no-op.
    """
    try:
        import importlib.util

        if importlib.util.find_spec("tkinterdnd2") is not None:
            return TkDnDDropTarget()
    except Exception:  # noqa: BLE001 — the factory is the safe seam.
        log.debug("tkinterdnd2 probe failed; overlay drop disabled.", exc_info=True)
    return NullDropTarget()


__all__ = [
    "DropTarget",
    "NullDropTarget",
    "TkDnDDropTarget",
    "make_drop_target",
]
