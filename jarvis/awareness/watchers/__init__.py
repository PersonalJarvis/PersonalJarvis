"""Awareness watchers (Phase A1) — maintain the L1 live frame.

Watchers are the background actors that keep ``AwarenessState`` up to
date: ``WindowFocusWatcher`` (Win32 hook on foreground changes),
``IdleDetector`` (GetLastInputInfo polling). Started by
``AwarenessManager`` during bootstrap, stopped on shutdown.

Re-exports will be added in Wave 4 once all watchers exist.
"""
from __future__ import annotations
