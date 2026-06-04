"""pywebview-Window-Konfiguration.

Die Window-Instanz wird vom :class:`JarvisShell` verwaltet — dieses Modul hält
nur die Daten-Struktur. So bleibt `shell.py` testbar ohne pywebview-Mock.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class WindowConfig:
    """Parameter für das pywebview-Hauptfenster."""
    title: str = "Jarvis"
    url: str = "http://127.0.0.1:47821"
    width: int = 1280
    height: int = 820
    min_width: int = 760
    min_height: int = 560
    start_hidden: bool = False
    background_color: str = "#0a0e14"     # Jarvis Dark-Theme
    # Frameless ist pywebview-unterstützt, wir lassen es aber Standard-Frame
    # für die erste Version — Fensterkontrollen kommen vom OS.
    frameless: bool = False
    easy_drag: bool = False
    confirm_close: bool = False
