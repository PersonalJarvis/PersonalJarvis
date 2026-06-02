"""AI Pointer — understand what the user's mouse cursor points at.

The package resolves the on-screen element under the cursor via the OS
accessibility tree (not blind screenshots) and attaches it to a brain turn
only when the utterance signals a deictic pointing intent. See
docs/plans/ai-pointer/DESIGN.md.
"""

from __future__ import annotations

from jarvis.pointer.intent import is_pointing_intent

__all__ = ["is_pointing_intent"]
