"""Jarvis' own self-learning loop (``docs/jarvis-learning.md``).

* ``notebook``  USER.md and MEMORY.md of the lead identity, plus the cached
                prompt snapshot every Jarvis prompt builder appends
* ``review``    the background reviewer: prompt, model call, and the pure
                evidence check that decides what may be written
* ``guard``     refuses credentials, injected instructions, invisible text
* ``loop``      collects finished voice and chat turns and fires the reviews

Nothing here runs on the boot critical path (AP-26) or the voice path (AP-9):
``start_learning`` only subscribes, and every review is a background task.
"""

from __future__ import annotations

from jarvis.memory.learning.notebook import snapshot_block

__all__ = ["snapshot_block"]
