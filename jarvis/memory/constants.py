"""Single source of truth for the ``messages.role`` vocabulary.

Why this module exists
======================

The recall store (``jarvis/memory/schema.sql``) persists conversation
turns with a ``role`` column. The set of permitted role values is
duplicated across five layers — Python tuple, Pydantic / typing
``Literal``, SQL ``CHECK`` constraint, SQL doc-comment, and the
recorder's runtime allowlist — and the layers must stay in lockstep.
This module is the canonical Python source the other layers import.

Drift between layers is the failure mode behind BUG-008 (recurred
three times in 2026-05) and the F6 / BUG-019 family of role-CHECK
issues. The
adoption checklist in ``docs/anti-drift-three-layer.md`` describes
the full pattern; this file plays the role that
``jarvis/sessions/constants.py`` plays for ``HangupReason``.

Role meanings
-------------

- ``user``          conversation turn from the user (voice or chat).
- ``assistant``     conversation turn from the LLM.
- ``system``        injected system / diagnostic message
                    (banner, error, brain self-test).
- ``tool``          tool-call result appended by the tool-use loop
                    (``jarvis/brain/tool_use_loop.py``).
- ``computer_use``  POAV desktop-action transcript line — persists the
                    Computer-Use action stream without re-introducing
                    the F6 CHECK regression (see
                    ``docs/anti-drift-three-layer.md``).
- ``announcement``  Mission-Manager / skill-runner announcement
                    surfaced through the bus and stored verbatim
                    for session-resume read-back.

What is intentionally NOT in this set
-------------------------------------

``preamble`` is the role attached to pre-ack UI bubbles emitted by
``jarvis/ui/web/server.py`` around the ``AnnouncementRequested``
handler. Pre-ack bubbles are display-only affordances and have no
business in the persisted recall log; ``MessageRecorder`` drops
them silently in ``message_recorder.py``. Adding ``preamble`` to
the CHECK would let those rows reach disk and inflate the recall
index without changing user-visible behaviour.

Phase-6 mission event-stream roles (e.g. ``milestone``) live in a
different table (``mission_events``) and are not part of this enum.
"""
from __future__ import annotations

import typing
from typing import Final

ROLE_USER: Final[str] = "user"
ROLE_ASSISTANT: Final[str] = "assistant"
ROLE_SYSTEM: Final[str] = "system"
ROLE_TOOL: Final[str] = "tool"
ROLE_COMPUTER_USE: Final[str] = "computer_use"
ROLE_ANNOUNCEMENT: Final[str] = "announcement"

ALLOWED_ROLES: Final[tuple[str, ...]] = (
    ROLE_USER,
    ROLE_ASSISTANT,
    ROLE_SYSTEM,
    ROLE_TOOL,
    ROLE_COMPUTER_USE,
    ROLE_ANNOUNCEMENT,
)
"""All ``role`` values the schema accepts. Order is intentionally
stable: the test ``test_role_constraint::test_literal_matches_tuple``
asserts ``typing.get_args(MessageRole) == ALLOWED_ROLES`` strictly,
which catches both additions and reorderings."""

MessageRole = typing.Literal[
    "user",
    "assistant",
    "system",
    "tool",
    "computer_use",
    "announcement",
]
"""Manual mirror of ``ALLOWED_ROLES``. ``typing.Literal`` does not
accept a tuple at definition time, so the values are repeated. The
runtime assertion below makes the duplication safe — any drift
between the two raises at import."""

if typing.get_args(MessageRole) != ALLOWED_ROLES:
    raise RuntimeError(
        "MessageRole Literal drifted from ALLOWED_ROLES — edit both in "
        "jarvis/memory/constants.py to keep them in lockstep. "
        f"Literal={typing.get_args(MessageRole)!r} "
        f"tuple={ALLOWED_ROLES!r}"
    )

ALLOWED_ROLES_FROZENSET: Final[frozenset[str]] = frozenset(ALLOWED_ROLES)
"""``frozenset`` mirror for O(1) membership checks at hot call sites
such as the recorder's drop gate. Built once at import; kept in
lockstep with ``ALLOWED_ROLES`` by sharing the same source tuple."""

__all__ = [
    "ALLOWED_ROLES",
    "ALLOWED_ROLES_FROZENSET",
    "MessageRole",
    "ROLE_ANNOUNCEMENT",
    "ROLE_ASSISTANT",
    "ROLE_COMPUTER_USE",
    "ROLE_SYSTEM",
    "ROLE_TOOL",
    "ROLE_USER",
]
