"""The local-models self-check: the health monitor behind the section badge.

Kept apart from :mod:`jarvis.brain.ollama_*` (which own the server, the
inventory and the roles). Nothing here runs at import time (AP-26); the
module imports its heavy neighbours lazily inside the function that needs
them.
"""

from __future__ import annotations

__all__: list[str] = []
