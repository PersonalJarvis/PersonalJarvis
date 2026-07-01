"""pywebview window configuration.

The window instance is managed by :class:`JarvisShell` — this module only
holds the data structure. That keeps `shell.py` testable without a
pywebview mock.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class WindowConfig:
    """Parameters for the main pywebview window."""
    title: str = "Jarvis"
    url: str = "http://127.0.0.1:47821"
    width: int = 1280
    height: int = 820
    min_width: int = 760
    min_height: int = 560
    start_hidden: bool = False
    background_color: str = "#0a0e14"     # Jarvis dark theme
    # Frameless is supported by pywebview, but we keep the standard frame
    # for the first version — window controls come from the OS.
    frameless: bool = False
    easy_drag: bool = False
    confirm_close: bool = False
