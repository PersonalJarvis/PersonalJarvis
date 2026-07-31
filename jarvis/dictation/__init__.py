"""Dictation mode — speak into whatever text field currently has focus.

Three small, independently testable pieces:

* :mod:`jarvis.dictation.cleanup` — deterministic, per-language filler-sound
  removal. Regex only; no model call ever happens in here.
* :mod:`jarvis.dictation.insert` — put text into the focused field of the
  foreground application: clipboard first, paste chord, restore. Reports
  honestly instead of failing silently.
* :mod:`jarvis.dictation.history` — the local record of what was dictated
  (raw and cleaned), so a cleanup can always be audited after the fact.

On top of those sits the optional second stage:

* :mod:`jarvis.dictation.polish` — a generative formatting pass (punctuation,
  capitalization, false starts, spoken numbers) that the deterministic cleanup
  structurally cannot perform. It is gated behind ``[dictation].polish``, it is
  bounded by a hard latency ceiling, and every one of its failure paths returns
  the raw transcript unchanged. Its prompt contract, its drift guards and its
  provider surface live in :mod:`~jarvis.dictation.polish_prompt`,
  :mod:`~jarvis.dictation.polish_guards` and
  :mod:`~jarvis.dictation.polish_client`.

The capture itself is NOT here: it reuses the dictation lane that already lives
in :mod:`jarvis.speech.pipeline` (``start_dictation`` / ``_dictation_session``),
which in turn reuses the microphone and the configured STT provider. Nothing in
this package imports the speech pipeline — the dependency runs one way only.

Import-cleanliness: nothing here imports a platform-only package at module
scope, so ``import jarvis.dictation`` stays clean on a headless server.
"""

from __future__ import annotations

__all__ = [
    "cleanup",
    "history",
    "insert",
    "polish",
    "polish_client",
    "polish_guards",
    "polish_prompt",
]
