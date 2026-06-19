"""Single source of truth for the ``conversation_kind`` vocabulary.

A conversation in the Chats manager is either a typed **text** thread
(``ChatStore``) or a recorded **voice** session (``SessionStore``). This
vocabulary crosses Python → Pydantic → REST JSON → TypeScript, so it follows
the five-layer anti-drift pattern (``docs/anti-drift-three-layer.md``): this
frozenset is the source of truth, the Pydantic models use a ``Literal``
asserted against it, and a TS mirror + parity test guard against drift
(``tests/unit/state/test_conversation_kind_parity.py``).
"""
from __future__ import annotations

from typing import Literal, get_args

CONVERSATION_KIND_TEXT = "text"
CONVERSATION_KIND_VOICE = "voice"

#: All known conversation kinds. Extend here first when adding a new source.
CONVERSATION_KINDS: frozenset[str] = frozenset(
    {CONVERSATION_KIND_TEXT, CONVERSATION_KIND_VOICE}
)

#: Pydantic/typing layer. MUST stay in lockstep with :data:`CONVERSATION_KINDS`.
ConversationKind = Literal["text", "voice"]

# Runtime guard: fail loudly at import time if the Literal drifts from the SOT
# frozenset (cheap insurance against the BUG-008 multi-layer-enum-drift class).
assert set(get_args(ConversationKind)) == CONVERSATION_KINDS, (
    "ConversationKind Literal drifted from CONVERSATION_KINDS — "
    "update both layers (see docs/anti-drift-three-layer.md)."
)

__all__ = [
    "CONVERSATION_KINDS",
    "CONVERSATION_KIND_TEXT",
    "CONVERSATION_KIND_VOICE",
    "ConversationKind",
]
