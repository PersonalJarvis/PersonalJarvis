"""The reachability map is a PROMISE, so it is checked like one.

The map is the section's honest answer to "does this work with my stuff?", and
its whole value is that a reader can trust it. These tests pin the two ways it
could quietly stop being trustworthy: an entry that claims direct support with
no provider behind it, and a longevity word the rest of the app does not know.
"""

from __future__ import annotations

from jarvis.smarthome.ecosystems import ECOSYSTEMS, Reachability, get_ecosystem
from jarvis.smarthome.registry import default_providers

VALID_LONGEVITY = {"permanent", "self_renewing", "provider_limited"}


def test_ids_are_unique() -> None:
    ids = [eco.id for eco in ECOSYSTEMS]
    assert len(ids) == len(set(ids))


def test_direct_entries_have_a_provider_behind_them() -> None:
    """"Direct" must mean Jarvis speaks it — not that we would like to."""
    provider_ids = {provider.id for provider in default_providers()}
    for eco in ECOSYSTEMS:
        if eco.reachability is Reachability.DIRECT:
            assert eco.id in provider_ids, f"{eco.id} claims direct support with no provider"


def test_longevity_uses_the_marketplace_vocabulary() -> None:
    """One word set across both surfaces, or the two will disagree on a card."""
    for eco in ECOSYSTEMS:
        assert eco.longevity in VALID_LONGEVITY, eco.id


def test_every_entry_says_what_to_do_or_why_not() -> None:
    for eco in ECOSYSTEMS:
        assert eco.note.strip(), f"{eco.id} has no note"
        assert eco.display_name.strip()
        assert eco.logo_slug.strip()


def test_unreachable_entries_declare_no_connection_method() -> None:
    """Listing a connection kind for something unreachable would be misleading."""
    for eco in ECOSYSTEMS:
        if eco.reachability is Reachability.UNAVAILABLE:
            assert str(eco.connection) == "none", eco.id


def test_the_ecosystems_people_ask_about_are_all_answered() -> None:
    """Absence reads as "not considered"; every big name gets a verdict."""
    for wanted in (
        "home_assistant",
        "matter",
        "thread",
        "philips_hue",
        "google_nest",
        "google_home",
        "apple_home",
        "amazon_alexa",
        "samsung_smartthings",
        "tuya",
        "zigbee2mqtt",
        "knx",
    ):
        assert get_ecosystem(wanted) is not None, f"{wanted} is missing from the map"
