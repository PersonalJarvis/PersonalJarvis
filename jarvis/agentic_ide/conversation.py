"""The turns the spoken instruction came out of, bounded for a prompt writer.

A voice instruction to a pane is almost never self-contained. It is the LAST
sentence of a conversation, and it refers back into that conversation with the
shorthand people use when the other side was listening: "points two and three",
"the second one you suggested", "do that for the wake path as well". The prompt
writer used to receive only the sentence, so a back-reference had nothing to
resolve against and travelled through into the finished brief — measured live
2026-07-29, a brief handed to two coding agents read "Ensure that points 2 and 3
from the current context are specifically incorporated". The agents never see
"the current context": that instruction is unresolvable, and the substance the
user actually named (which happened to be the whole task) reached nobody.

So the last few turns travel with the instruction. Two properties decide the
shape:

* **Bounded, and bounded per message.** This rides on a call the user waits
  through, and a long assistant answer is exactly where an unbounded window
  would blow up. The middle of an over-long message is dropped rather than its
  end — the tail is where an answer names its conclusions, and a
  back-reference points at those at least as often as at the opening.
* **Oldest first, and honest about the roles.** The writer has to be able to
  tell what the user asked from what Jarvis answered, because "points two and
  three" points into Jarvis's answer, not the user's question.

Whatever language the conversation happened in is kept verbatim. It is input
DATA for the writer, not output: the brief the writer produces from it is
English like every other artifact (CLAUDE.md §1).
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

#: How many messages travel with the instruction. Two full exchanges: enough to
#: carry the answer a back-reference points at plus the question that produced
#: it, short of pasting a whole call into every composition.
MAX_MESSAGES = 4

#: Per-message ceiling. A spoken answer that lists four options runs to roughly
#: this length, and that answer IS the thing "points two and three" refers to,
#: so the bound has to clear it rather than cut it in half.
MAX_MESSAGE_CHARS = 1000

_ROLE_LABELS = {"user": "The user", "assistant": "Jarvis"}


def _bounded(text: str) -> str:
    """``text`` within ``MAX_MESSAGE_CHARS``, keeping both ends when it is not."""
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= MAX_MESSAGE_CHARS:
        return cleaned
    half = MAX_MESSAGE_CHARS // 2
    return f"{cleaned[:half]} […] {cleaned[-half:]}"


def from_messages(
    messages: Iterable[object], *, exclude: str = ""
) -> tuple[tuple[str, str], ...]:
    """The recent conversation as ``(role, text)`` pairs, oldest first.

    Takes anything with ``role`` and ``content`` attributes — a
    ``BrainMessage``, a plain object, a stand-in in a test — because the history
    this reads from is provider-owned and its concrete type is not ours to pin.
    Anything that is not a plain user or assistant string is skipped: a tool
    result or an image block is not conversation the writer can use.

    ``exclude`` drops a trailing message equal to it. The instruction being
    composed is sometimes already appended to the history by the time this runs,
    and handing the writer the same sentence twice reads as the user having said
    it twice.
    """
    pairs: list[tuple[str, str]] = []
    for item in messages or ():
        role = getattr(item, "role", None)
        content = getattr(item, "content", None)
        if role not in _ROLE_LABELS or not isinstance(content, str):
            continue
        text = _bounded(content)
        if text:
            pairs.append((role, text))
    wanted = " ".join((exclude or "").split())
    if wanted and pairs and pairs[-1][1] == _bounded(wanted):
        pairs.pop()
    return tuple(pairs[-MAX_MESSAGES:])


def render(turns: Sequence[tuple[str, str]]) -> str:
    """The conversation laid out for a writer, or ``""`` when there is none."""
    lines = [
        f"{_ROLE_LABELS[role]}: {text}"
        for role, text in turns or ()
        if role in _ROLE_LABELS and str(text or "").strip()
    ]
    return "\n".join(lines)


__all__ = ["MAX_MESSAGES", "MAX_MESSAGE_CHARS", "from_messages", "render"]
