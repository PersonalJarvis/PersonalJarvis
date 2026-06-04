"""pywebview-Shell für die Desktop-App.

**Warum eine eigene Schicht zwischen pywebview und __main__?** pywebview ist
blocking-callback-driven und hat Main-Thread-Requirement. Der Rest von Jarvis
ist async und thread-agnostisch. Diese Schicht kapselt die Thread-Grenzen
hinter einer API die andere Layer (Tray, WebSocket, Single-Instance)
ansteuern können ohne pywebview-Interna zu kennen.
"""
from __future__ import annotations

from .runtime_check import WebView2CheckResult, check_webview2
from .shell import JarvisShell
from .single_instance import InstanceClaim, SingleInstance
from .window import WindowConfig

__all__ = [
    "JarvisShell",
    "WindowConfig",
    "SingleInstance",
    "InstanceClaim",
    "check_webview2",
    "WebView2CheckResult",
]
