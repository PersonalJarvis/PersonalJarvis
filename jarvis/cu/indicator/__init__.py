"""Computer-Use screen indicator — the yellow "Jarvis is controlling this
computer" glow border (Codex-style) plus the Esc-to-cancel hint.

Architecture (2026-07, replaces the removed OS-Level edge-glow):

- ``renderer`` + ``__main__`` — a minimal PySide6 sidecar process
  (``python -m jarvis.cu.indicator``) that draws a pulsing gold glow along
  every monitor's edges and an "Esc to cancel" pill. Spawned lazily per
  Computer-Use mission, killed when the last mission ends. PySide6 is
  imported ONLY inside the sidecar, never in the main process.
- ``controller`` — main-process glue: subscribes to
  ``CUControlStarted``/``CUControlEnded``, refcounts concurrent missions,
  arms the global Escape hotkey while a mission runs, and cancels the
  CU-scoped tokens on Escape.
- ``protocol`` — the JSON-lines stdin/stdout vocabulary between the two.
- ``capture_guard`` — hides the border for the split second of CU's own
  frame grabs on platforms without a capture-exclusion API (non-Windows).
- ``self_input`` — suppression stamps so a synthetic Esc typed BY Jarvis
  never cancels Jarvis.

Headless / Wayland / missing-PySide6 hosts: the controller never spawns
the sidecar and logs one English line — a quiet no-op, never a crash.
"""

from __future__ import annotations

__all__: list[str] = []
