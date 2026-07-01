"""pywebview shell for the desktop app.

**Why a separate layer between pywebview and __main__?** pywebview is
blocking-callback-driven and has a main-thread requirement. The rest of Jarvis
is async and thread-agnostic. This layer encapsulates the thread boundaries
behind an API other layers (tray, WebSocket, single-instance)
can drive without knowing pywebview internals.
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
