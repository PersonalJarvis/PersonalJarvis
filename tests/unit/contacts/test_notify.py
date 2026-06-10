"""Tests for the contact-change notification seam (jarvis/contacts/notify.py)."""
from __future__ import annotations

import pytest

from jarvis.contacts import notify


@pytest.fixture(autouse=True)
def _clean_sink():
    notify.clear_contact_change_sink()
    yield
    notify.clear_contact_change_sink()


def test_actions_vocabulary_is_frozen():
    assert notify.CONTACT_CHANGE_ACTIONS == ("created", "updated", "deleted")


def test_notify_without_sink_is_noop():
    notify.notify_contact_changed("created", "chris", "Chris")  # must not raise


def test_sink_receives_notification():
    seen: list[tuple[str, str, str]] = []
    notify.set_contact_change_sink(lambda a, s, n: seen.append((a, s, n)))
    notify.notify_contact_changed("created", "chris", "Chris")
    assert seen == [("created", "chris", "Chris")]


def test_sink_error_is_swallowed():
    def boom(action: str, slug: str, name: str) -> None:
        raise RuntimeError("sink exploded")

    notify.set_contact_change_sink(boom)
    notify.notify_contact_changed("updated", "chris", "Chris")  # must not raise


def test_unknown_action_is_dropped():
    seen: list[str] = []
    notify.set_contact_change_sink(lambda a, s, n: seen.append(a))
    notify.notify_contact_changed("renamed", "chris", "Chris")
    assert seen == []


def test_clear_sink_stops_delivery():
    seen: list[str] = []
    notify.set_contact_change_sink(lambda a, s, n: seen.append(a))
    notify.clear_contact_change_sink()
    notify.notify_contact_changed("created", "chris", "Chris")
    assert seen == []
