"""The single vocabulary for "how did this dictation end".

Why this file exists at all
---------------------------
The outcome string is a value that crosses five layers — the speech pipeline
writes it, ``DictationCompleted`` carries it, the history sidecar stores it,
the REST layer serialises it and the UI renders a label for it. That is exactly
the shape of drift that has bitten this repo four times (BUG-008, AP-4): one
site learns a new value, the others keep an older list, and the UI ends up
rendering a raw identifier or nothing at all.

So the vocabulary is declared **once**, here, and every other layer imports it.
A cross-layer parity test pins the TypeScript mirror against this tuple.

What each value means
---------------------
``inserted``
    The text was typed or pasted into the focused window. The happy path.
``clipboard_only``
    Insertion was not possible, so the text was left on the clipboard. The
    user still has their words; they just have to paste them.
``unavailable``
    Neither insertion nor the clipboard worked (no desktop session, no
    clipboard backend). The transcript exists only in the history.
``chat``
    The dictation was routed into the chat instead of the desktop.
``empty``
    Transcription succeeded but returned nothing — silence, or speech too
    quiet to resolve. Not an error, but nothing to show for it either.
``cancelled``
    The user aborted before a transcript existed.
``failed``
    Transcription itself failed — a provider error, a missing key, a wedged
    engine. This is the value that used to be invisible: the pipeline
    swallowed the error and the dictation looked like plain silence.

``discarded`` is deliberately **not** in this list. It is a separate boolean
on the history entry, because an entry can be both ``inserted`` and discarded
by the user afterwards; folding the two into one string would make that state
unrepresentable.
"""

from __future__ import annotations

from typing import Final

#: Every outcome the pipeline may report, in rough order of desirability.
#: Mirrored in TypeScript as ``DICTATION_OUTCOMES`` and pinned by a parity test.
DICTATION_OUTCOMES: Final[tuple[str, ...]] = (
    "inserted",
    "clipboard_only",
    "unavailable",
    "chat",
    "empty",
    "cancelled",
    "failed",
)

#: Outcomes for which there is nothing usable to show the user, so keeping the
#: audio (when the user allows it) is what makes a later Restore possible.
#: The success outcomes are excluded on purpose: audio is the most sensitive
#: thing this application ever stores, so it is only ever kept when it buys
#: back something the user actually lost.
RECOVERABLE_OUTCOMES: Final[frozenset[str]] = frozenset(
    {"empty", "cancelled", "failed"}
)


def is_recoverable(outcome: str | None) -> bool:
    """``True`` when this outcome left the user with nothing to show for it.

    Tolerant by design: an unknown value from an older install reads as "not
    recoverable" rather than raising, because a vocabulary mismatch must never
    cost someone their dictation.
    """
    return str(outcome or "") in RECOVERABLE_OUTCOMES


__all__ = [
    "DICTATION_OUTCOMES",
    "RECOVERABLE_OUTCOMES",
    "is_recoverable",
]
