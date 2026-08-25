"""The local-models setup assistant: test runner, benchmarks, tools and prompt.

Everything the "Help me set up" panel of the Local models section runs on,
kept apart from :mod:`jarvis.brain.ollama_*` (which own the server, the
inventory and the roles) and from :mod:`jarvis.agent_chat` (which owns the
chat surface). Nothing here runs at import time (AP-26); every module
imports its heavy neighbours lazily inside the function that needs them.
"""

from __future__ import annotations

__all__: list[str] = []
