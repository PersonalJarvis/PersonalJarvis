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
``paste_sent``
    A user-recorded paste shortcut was sent to the focused window and no error
    came back — but Jarvis does not paste, it asks the application in front to
    paste, and an application that does not bind that shortcut simply ignores
    it. There is nothing to read back, so this is as far as honesty reaches:
    the keystroke went out, the result is unknown, and the text is left on the
    clipboard instead of being restored away. Only ever produced for a custom
    chord; the curated ones report ``inserted``.
``clipboard_only``
    Insertion was not possible, so the text was left on the clipboard. The
    user still has their words; they just have to paste them.
``unavailable``
    Neither insertion nor the clipboard worked (no desktop session, no
    clipboard backend). The transcript exists only in the history.
``chat``
    The dictation was handed to Jarvis's own window instead of being typed into
    the desktop — which is what happens whenever Jarvis itself is the
    application in front (``insert.resolve_target``). The UI decides where it
    lands from there: the focused field, a terminal pane, or the chat composer
    as the fallback (``lib/dictationTarget.ts``). The name is historical — it
    once meant "the chat box and nothing else", which is exactly why a dictation
    into any OTHER part of the app used to disappear.
``partial``
    Text was produced and delivered, but part of the recording never
    transcribed at all — a provider failure that outlived every retry, on audio
    nothing ever read again. This value exists because its absence was a lie
    the user could see: a 37.7-second dictation whose provider answered 429
    arrived as three words, reported ``inserted``, and had its audio destroyed
    because a success is not recoverable. Reporting the delivery outcome of the
    surviving fragment answers "where did those three words go" and hides the
    only question that mattered — "where are the other thirty seconds". So the
    loss outranks the delivery: ``method`` and ``detail`` still say what
    happened to the part that made it, and the outcome says the part that did
    not. It is deliberately NOT keyed on "a transcription error was seen at
    some point" — a failure on the last tick whose audio then fell under the
    minimum segment size leaves a stale error on a COMPLETE transcript, and
    calling that partial would mislead in the opposite direction.
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
    "paste_sent",
    "clipboard_only",
    "unavailable",
    "chat",
    "partial",
    "empty",
    "cancelled",
    "failed",
)

#: Outcomes where the user's words REACHED them: the field they were looking
#: at (``inserted``), the app's own input (``chat``), the app the paste chord
#: went to (``paste_sent``), or the part of the recording that survived
#: (``partial``). ``cancelled`` counts too — the user called it off themselves,
#: and nothing failed.
#:
#: This is the set a surface must never put a FAILURE mark on, and the Jarvis
#: Bar is why it exists at all: the bar carries no text (its
#: ``show_listening_transcript`` is a documented no-op), so the red cross is
#: not a caveat standing next to an explanation — it IS the whole message.
#: Every outcome that carried a ``detail`` sentence used to raise that cross,
#: which meant a dictation that pasted perfectly and only wanted to add a
#: footnote ("the shortcut was sent", "some seconds were lost") reported itself
#: to the user as a failure. Reported on Windows, 2026-08-09.
#:
#: Deliberately NOT the complement of "has something to say": ``partial`` is in
#: here and still owes the user a sentence. The two questions are separate —
#: *did the words arrive* decides the mark, *is there anything to add* decides
#: how long the sentence stays up.
DELIVERED_OUTCOMES: Final[frozenset[str]] = frozenset(
    {"inserted", "chat", "paste_sent", "partial", "cancelled"}
)

#: Outcomes where the user is missing words the recording could still give
#: back, so keeping the audio (when they allow it) is what makes a later
#: Restore possible. The plain success outcomes are excluded on purpose: audio
#: is the most sensitive thing this application ever stores, so it is only ever
#: kept when it buys back something the user actually lost.
#:
#: ``partial`` is in here even though it DID deliver text, and that is the
#: whole point of the value: the delivered fragment is not the dictation, the
#: rest of it exists only in the audio, and treating the fragment as a success
#: is what deleted the recording out from under the user.
RECOVERABLE_OUTCOMES: Final[frozenset[str]] = frozenset(
    {"partial", "empty", "cancelled", "failed"}
)


def was_delivered(outcome: str | None) -> bool:
    """``True`` when the dictation's words reached the user.

    Fail-CLOSED, the opposite of :func:`is_recoverable`: an unknown value from
    a newer install reads as "not delivered". A surface may only ever suppress
    its failure mark on an outcome it actually understands — the alternative is
    a future failure outcome that ships silently.
    """
    return str(outcome or "") in DELIVERED_OUTCOMES


def is_recoverable(outcome: str | None) -> bool:
    """``True`` when this outcome left the user missing words the audio holds.

    Tolerant by design: an unknown value from an older install reads as "not
    recoverable" rather than raising, because a vocabulary mismatch must never
    cost someone their dictation.
    """
    return str(outcome or "") in RECOVERABLE_OUTCOMES


__all__ = [
    "DELIVERED_OUTCOMES",
    "DICTATION_OUTCOMES",
    "RECOVERABLE_OUTCOMES",
    "is_recoverable",
    "was_delivered",
]
