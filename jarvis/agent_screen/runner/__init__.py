"""The in-session runner: the half of an agent screen that lives INSIDE it.

Capture and synthetic input only ever reach the session the calling process
belongs to. So the host cannot drive an isolated screen directly — it starts
a small runner in that session and speaks :mod:`jarvis.agent_screen.wire` to
it. This package holds the Python runner (used for virtual X displays and for
the diagnostic attached screen); the Windows Sandbox guest has no Python and
uses the PowerShell runner shipped alongside it.
"""

from jarvis.agent_screen.runner.server import ScreenServer, serve

__all__ = ["ScreenServer", "serve"]
