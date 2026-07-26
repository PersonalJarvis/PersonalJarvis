"""The platform export guide — a catalog, so the tests guard its promises.

Nothing here executes, so there is no behaviour to test. What CAN go wrong is
the catalog quietly becoming untrue: an entry that only makes sense inside one
legal regime, a claim that we read a format we do not, a category nobody
renders, or an entry so vague it does not save anyone the search it exists to
replace.
"""

from __future__ import annotations

from jarvis.ultrawiki.connectors.export_import import EXPORT_FORMATS
from jarvis.ultrawiki.platform_guide import (
    CATEGORIES,
    PLATFORMS,
    as_dict,
    get_platform,
    list_platforms,
    search_platforms,
)

#: Words that would make an entry useless outside one jurisdiction. The guide
#: is built on the platform's OWN export button, which ships worldwide; a
#: statutory route may only ever appear as an extra, in `legal_route`.
_JURISDICTION_WORDS = (
    "gdpr",
    "dsgvo",
    "ccpa",
    "cpra",
    "lgpd",
    "article 20",
    "art. 20",
    "eu law",
    "european union",
    "data protection act",
)


class TestUniversality:
    def test_no_primary_route_depends_on_one_jurisdiction(self):
        """The constraint the whole design rests on.

        A person in São Paulo, Lagos or Jakarta clicks the same button as one
        in Berlin. An instruction phrased as a legal right would be useless to
        most of the people reading it.
        """
        for entry in PLATFORMS:
            primary = " ".join(
                (entry.where, entry.contains, entry.reads, entry.caveat)
            ).lower()
            for word in _JURISDICTION_WORDS:
                assert word not in primary, (
                    f"{entry.id} states a jurisdiction-specific route as the "
                    f"primary path ({word!r}); that belongs in legal_route"
                )

    def test_every_entry_names_a_concrete_place_to_click(self):
        """"Somewhere in settings" is the problem this catalog solves.

        A handful of entries cover a CLASS of service rather than one company
        (every bank has its own site), so the requirement is a named control
        to look for, not a literal menu path only one vendor could have.
        """
        controls = (
            "→",
            "Settings",
            "settings",
            ".com",
            "app",
            "Export",
            "Download",
            "Request",
        )
        for entry in PLATFORMS:
            assert len(entry.where) > 25, f"{entry.id} does not say where to go"
            assert any(marker in entry.where for marker in controls), (
                f"{entry.id} names no control to look for"
            )


class TestHonesty:
    def test_every_entry_says_what_we_can_actually_read(self):
        for entry in PLATFORMS:
            assert entry.reads.strip(), f"{entry.id} does not say what imports"
            assert entry.formats, f"{entry.id} names no format"

    def test_platforms_we_cannot_read_say_so_plainly(self):
        """Silence here is the expensive kind: someone exports 40 GB first."""
        signal = get_platform("signal")
        assert signal is not None
        assert "not yet" in signal.reads.lower()

        microsoft = get_platform("microsoft")
        assert microsoft is not None
        assert ".pst" in microsoft.reads

    def test_the_formats_we_claim_to_read_are_ones_the_importer_knows(self):
        """A claim of 'fully' has to correspond to a real parser."""
        readable_hints = {fmt.lower() for fmt in EXPORT_FORMATS} | {
            "zip",
            "tgz",
            "tar.gz",
            "any",
            "mbox",
            "icalendar",
            "vcard",
            "js/json",
            "camt/mt940",
            "gpx",
            "xml",
            "txt",
            "vtt",
            "mp4",
            "enex",
            "backup",
            "pst",
        }
        for entry in PLATFORMS:
            for fmt in entry.formats:
                token = fmt.lower().lstrip(".")
                assert token in readable_hints, (
                    f"{entry.id} names the format {fmt!r}, which is neither a "
                    "format the importer knows nor a documented exception"
                )

    def test_sensitive_categories_carry_a_warning(self):
        bank = get_platform("banking")
        assert bank is not None
        assert "sensitive" in bank.caveat.lower()


class TestCatalogShape:
    def test_ids_are_unique_and_url_safe(self):
        ids = [entry.id for entry in PLATFORMS]
        assert len(ids) == len(set(ids))
        for entry_id in ids:
            assert entry_id == entry_id.lower()
            assert " " not in entry_id

    def test_every_entry_sits_in_a_declared_category(self):
        """A category the UI does not render means an entry nobody sees."""
        for entry in PLATFORMS:
            assert entry.category in CATEGORIES, f"{entry.id}: {entry.category}"

    def test_every_declared_category_actually_has_entries(self):
        for category in CATEGORIES:
            assert list_platforms(category), f"{category} is empty"

    def test_the_catalog_covers_the_platforms_people_actually_ask_for(self):
        must_have = {
            "whatsapp",
            "google",
            "apple",
            "instagram",
            "facebook",
            "x-twitter",
            "tiktok",
            "spotify",
            "amazon",
            "linkedin",
        }
        assert must_have <= {entry.id for entry in PLATFORMS}


class TestLookup:
    def test_an_unknown_id_is_none_rather_than_an_error(self):
        assert get_platform("myspace") is None
        assert get_platform("") is None

    def test_lookup_is_case_and_whitespace_forgiving(self):
        assert get_platform("  WhatsApp  ") is not None

    def test_an_unknown_category_yields_nothing_rather_than_everything(self):
        """Falling back to "all" would silently answer a different question."""
        assert list_platforms("nonsense") == ()

    def test_search_puts_name_matches_before_description_matches(self):
        results = search_platforms("google")
        assert results[0].id == "google"

    def test_search_finds_a_platform_by_what_it_holds(self):
        ids = {entry.id for entry in search_platforms("photos")}
        assert "google" in ids or "apple" in ids

    def test_an_empty_query_returns_the_whole_catalog(self):
        assert search_platforms("   ") == PLATFORMS


class TestPayload:
    def test_the_api_payload_carries_every_field_the_ui_renders(self):
        payload = as_dict(get_platform("whatsapp"))
        assert set(payload) == {
            "id",
            "label",
            "category",
            "where",
            "contains",
            "formats",
            "wait",
            "reads",
            "caveat",
            "legal_route",
            "brand",
        }
        assert isinstance(payload["formats"], list)
