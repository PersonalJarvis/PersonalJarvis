"""Did the spoken readback name a different pane than the result it renders?

A trusted action result is handed to the live model as a RENDERING ORDER: say
this, in your own voice, keeping every material fact. The order has always
carried an identifier-fidelity clause — "never swap in a name from the user's
request that the result itself does not contain" — and the model has always
been free to ignore it. Twice now it did:

* 2026-08-12 — result opened T5 and T6, the voice said "ich habe T2
  angewiesen"; T2 was the pane the user had asked about.
* 2026-08-13 — result opened T5 (a fresh pane, because the call-sign in the
  user's sentence was garbled), the voice said "ich habe T1 den Auftrag
  erteilt". The user only found out by looking at the screen.

Both are the same shape, and it is the worst one a voice assistant has: the
action is reported as the user WANTED it, so a wrong action sounds exactly
like a right one and there is nothing to notice. ``_delegate_result_prompt``
says in its own docstring that if the class resurfaces, the deterministic fix
belongs at the readback boundary rather than in more prompt wording. This is
that check.

**Deliberately narrow.** It fires only when the result names a live pane, the
rendering names a live pane, and the rendering names one the result does not.
That is the failure verbatim. A rendering that names no pane at all is not a
swap — paraphrasing "T5 is open" as "the new terminal is running" is a
perfectly good reading, and treating it as a lie would make the check fire
constantly on correct behaviour. A rendering that names the SAME pane plus
prose is likewise clean. False positives here cost the user an unnecessary spoken correction, so
the bar is set where only a contradiction can clear it.

Spoken forms are folded into call-signs first (``names.canonical_positions``),
so "Terminal eins" is compared as "T1" rather than missed. Everything fails
OPEN: any fault answers "no swap", because a broken check must never invent a
correction on a live call.
"""
from __future__ import annotations

import re
from collections.abc import Sequence

__all__ = ["swapped_call_signs"]


def _canonical(text: str, roster: list[str]) -> str:
    """``text`` with every spoken pane position rewritten as its call-sign."""
    from jarvis.agentic_ide.names import canonical_positions

    return canonical_positions(text, roster)


def _named(text: str, roster: Sequence[str]) -> set[str]:
    """Which of ``roster`` the text names, matched whole-word."""
    found: set[str] = set()
    for name in roster:
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE):
            found.add(name)
    return found


def swapped_call_signs(
    result: str, rendering: str, *, roster: Sequence[str]
) -> tuple[str, ...]:
    """Panes the SPOKEN rendering claims that the trusted result never names.

    ``result`` is the deterministic sentence the action layer produced,
    ``rendering`` what the model actually said, and ``roster`` the workspace's
    live call-signs. Returns the offending names sorted, or ``()`` when the
    rendering is consistent with the result — which includes every case where
    one of the three inputs is missing, empty, or unparseable.
    """
    names = [str(n).strip() for n in roster if str(n or "").strip()]
    if not names or not str(result or "").strip() or not str(rendering or "").strip():
        return ()
    try:
        stated = _named(_canonical(result, names), names)
        if not stated:
            # The result names no pane, so the rendering cannot contradict it
            # about one. Guarding here and not only below keeps a result about
            # something else entirely (a file, a setting) out of this check.
            return ()
        spoken = _named(_canonical(rendering, names), names)
    except Exception:  # noqa: BLE001 - a faulty check must never fire
        return ()
    return tuple(sorted(spoken - stated))
