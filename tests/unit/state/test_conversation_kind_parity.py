"""Parity guard for the conversation_kind vocabulary (5-layer anti-drift).

Mirrors the BUG-008 defense: the Pydantic ``Literal`` and the frozenset SOT
must never drift apart, and the REST response models must use the SOT type.
"""
from __future__ import annotations

from typing import get_args

from jarvis.state.conversation_constants import (
    CONVERSATION_KIND_TEXT,
    CONVERSATION_KIND_VOICE,
    CONVERSATION_KINDS,
    ConversationKind,
)
from jarvis.ui.web.chats_routes import ConversationSummary


def test_literal_matches_frozenset() -> None:
    assert set(get_args(ConversationKind)) == CONVERSATION_KINDS


def test_known_kinds_are_text_and_voice() -> None:
    assert CONVERSATION_KINDS == {"text", "voice"}
    assert CONVERSATION_KIND_TEXT == "text"
    assert CONVERSATION_KIND_VOICE == "voice"


def test_response_model_uses_the_sot_type() -> None:
    # The summary model's ``kind`` field annotation must be the SOT Literal,
    # so a new kind added to the frozenset surfaces a type error here.
    anno = ConversationSummary.model_fields["kind"].annotation
    assert set(get_args(anno)) == CONVERSATION_KINDS
