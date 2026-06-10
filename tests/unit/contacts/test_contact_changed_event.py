"""ContactChanged event contract: frozen, defaults, action vocabulary parity."""
from __future__ import annotations

import dataclasses

import pytest

from jarvis.contacts.notify import CONTACT_CHANGE_ACTIONS
from jarvis.core.events import ContactChanged, Event


def test_contact_changed_is_frozen_event_with_trace():
    evt = ContactChanged(action="created", slug="chris", name="Chris")
    assert isinstance(evt, Event)
    assert evt.trace_id is not None
    assert evt.timestamp_ns > 0
    with pytest.raises(dataclasses.FrozenInstanceError):
        evt.slug = "other"  # type: ignore[misc]


def test_contact_changed_action_vocabulary_documented():
    # The docstring names the owning vocabulary so the two sites cannot drift
    # silently — and the canonical actions construct cleanly.
    for action in CONTACT_CHANGE_ACTIONS:
        assert ContactChanged(action=action, slug="s", name="N").action == action
