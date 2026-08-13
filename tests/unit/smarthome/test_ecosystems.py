"""The reachability map is a PROMISE, so it is checked like one.

The map is the section's honest answer to "does this work with my stuff?", and
its whole value is that a reader can trust it. These tests pin the two ways it
could quietly stop being trustworthy: an entry that claims direct support with
no provider behind it, and a longevity word the rest of the app does not know.
"""

from __future__ import annotations

from jarvis.smarthome.ecosystems import ECOSYSTEMS, Reachability, Tier, get_ecosystem
from jarvis.smarthome.registry import default_providers

VALID_LONGEVITY = {"permanent", "self_renewing", "provider_limited"}


def test_ids_are_unique() -> None:
    ids = [eco.id for eco in ECOSYSTEMS]
    assert len(ids) == len(set(ids))


def test_direct_entries_have_a_provider_behind_them() -> None:
    """ "Direct" must mean Jarvis speaks it — not that we would like to."""
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


# ----------------------------------------------------------------------
# Curation — the list has to answer the question, not just contain it
# ----------------------------------------------------------------------


def test_exactly_one_entry_is_the_recommended_hub() -> None:
    """The hero slot is singular by design.

    For nearly every house "connect Home Assistant" IS the answer, and two
    recommendations would be none: the reader would have to compare them, which
    is the work the ranking exists to remove.
    """
    hubs = [eco for eco in ECOSYSTEMS if eco.tier is Tier.HUB]
    assert [eco.id for eco in hubs] == ["home_assistant"]


def test_the_default_screen_stays_short() -> None:
    """A wall of twenty equal cards is the thing this ranking replaced."""
    shown = [eco for eco in ECOSYSTEMS if eco.tier is not Tier.TECHNICAL]
    assert len(shown) <= 12, "too many entries compete for attention by default"
    assert len(shown) >= 6, "hiding the familiar names would be its own failure"


def test_the_household_names_are_not_hidden_behind_the_toggle() -> None:
    """These are what someone actually owns; burying them defeats the point."""
    for wanted in (
        "philips_hue",
        "google_nest",
        "google_home",
        "apple_home",
        "amazon_alexa",
        "samsung_smartthings",
        "ikea_dirigera",
        "sonos",
    ):
        eco = get_ecosystem(wanted)
        assert eco is not None
        assert eco.tier is Tier.POPULAR, f"{wanted} should be shown by default"


def test_protocols_are_available_but_out_of_the_way() -> None:
    for wanted in ("matter", "thread", "zigbee2mqtt", "knx"):
        eco = get_ecosystem(wanted)
        assert eco is not None
        assert eco.tier is Tier.TECHNICAL, f"{wanted} should sit behind the toggle"


def test_every_reachable_ecosystem_says_what_to_do() -> None:
    """A card that opens and offers no procedure is the dead button again."""
    for eco in ECOSYSTEMS:
        if eco.reachability is Reachability.UNAVAILABLE:
            # Nothing to do, by the vendor's design — inventing steps would send
            # people hunting for a menu that does not exist.
            assert eco.setup_steps == (), eco.id
        else:
            assert eco.setup_steps, f"{eco.id} has no steps to show"
            assert eco.docs_url, f"{eco.id} has nowhere to read more"


def test_every_entry_carries_a_brand_colour() -> None:
    """One flat grey across every card is what made the section read unfinished."""
    for eco in ECOSYSTEMS:
        assert len(eco.logo_color) == 6, eco.id
        int(eco.logo_color, 16)  # raises if it is not a hex colour
