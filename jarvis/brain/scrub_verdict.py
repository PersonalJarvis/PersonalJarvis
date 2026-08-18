"""Whole-turn verdict for a ``scrub_for_voice`` result (non-streaming paths).

``scrub_for_voice`` ends with a residue guard: when a filter fired AND fewer
than ``MIN_MEANINGFUL_CHARS`` alphanumeric characters survive, it throws the
input away and returns the canned error phrase for ALL of it. That guard is
the last defence against a machine leak reaching TTS and must stay exactly as
it is — but it conflates two very different turns:

* a machine leak was cut out (tool JSON, stacktrace, raw repr, shell command)
  and what remains is debris — something really did go wrong, the error phrase
  is the honest thing to say;
* only harmless prose was cut out (filler opener, honorific, self-reference,
  jargon, markdown, an em dash) — nothing failed, the model simply said
  nothing of substance. Measured 2026-08-18: "Tolle Frage!", "Great
  question.", "As an AI." and "Sir," all came back as "Es trat ein Fehler
  auf." / "An error occurred.".

Speaking a failure that never happened is a lie, so the non-streaming voice
call sites need the second case separated out. The streaming paths already
do this: :func:`jarvis.realtime.scrub_gate._is_stream_safe_residue` owns the
action classification (blocking vs. non-blocking scrub actions), and this
module deliberately REUSES that one classifier instead of keeping a second
copy of the action sets in sync. Only the CONCLUSION differs: a stream waits
for the next delta, a completed turn stays silent.
"""

from __future__ import annotations

from jarvis.brain.output_filter import ScrubResult

# Private on purpose over there: the sets are an implementation detail of one
# shared verdict, not a public taxonomy. Importing the function (rather than
# copying the sets) keeps ONE place where a newly added scrub action has to be
# classified — an unclassified action still counts as blocking there, so this
# module inherits the fail-closed default too.
from jarvis.realtime.scrub_gate import _is_stream_safe_residue

__all__ = ["is_harmless_scrub_residue"]


def is_harmless_scrub_residue(result: ScrubResult) -> bool:
    """``True`` when the scrub emptied a turn without finding anything wrong.

    The caller is holding a :class:`ScrubResult` whose ``cleaned`` text is the
    generic error phrase. This returns ``True`` only when that phrase came from
    the post-scrub residue guard AND every filter that fired is a harmless
    prose transform. Then the honest outcome is to say nothing — never to
    claim an error.

    ``False`` covers both remaining cases and both keep the fallback phrase:
    a real leak was scrubbed (``removed_tool_json``, ``replaced_stacktrace``,
    ...), or a scrub action nobody has classified yet showed up.

    Never swallow this silently: every call site logs the dropped text and the
    actions behind it (contract §7, "no silent except").
    """
    return _is_stream_safe_residue(result)
