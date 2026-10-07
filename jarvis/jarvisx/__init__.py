"""Jarvis X — the built-in screenshot and screen-recording tool.

A plain capture tool in the spirit of the macOS screenshot utilities: region,
window and full-screen screenshots plus region / full-screen recordings, each
on its own global shortcut. Captures are saved to a folder, indexed in a small
local library, optionally copied to the clipboard, and announced with the same
flash-and-fly-into-the-corner card the appshots use. Clicking the card opens
the annotation editor window.

Jarvis X never involves the assistant: no Screen Context privacy pipeline, no
delivery into a conversation. Nothing here is imported at boot (AP-26); the
shortcuts are armed after the app is ready. See ``docs/jarvisx.md``.

Modules:

- ``paths`` / ``store`` — the save folder and the item index.
- ``geometry`` — pure coordinate math (HiDPI mapping, card layout, timing).
- ``capture`` — region / window / monitor grabs (mss, native window capture).
- ``recorder`` — the screen recorder and its encoder probe (PyAV).
- ``clipboard`` — put an image on the system clipboard.
- ``overlay`` — the PySide6 sidecar: selection overlay, cards, recording pill.
- ``service`` — the one orchestrator every trigger goes through.
- ``hotkeys`` — the global shortcuts, re-armed live on a settings change.
"""
