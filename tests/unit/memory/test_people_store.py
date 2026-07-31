"""Unit tests for `jarvis.memory.people.PersonStore`."""
from __future__ import annotations

from jarvis.memory.people import PersonStore
from jarvis.memory.workspace import Workspace, person_slug


# ======================================================================
# get_or_create
# ======================================================================

class TestGetOrCreate:
    def test_creates_file_for_new_person(
        self, workspace: Workspace, person_store: PersonStore
    ) -> None:
        person = person_store.get_or_create("Casey", relationship="colleague")
        assert person.path.exists()
        assert person.path.stem == person_slug("Casey")
        # Relationship was set from the template
        assert person.relationship == "colleague"

    def test_same_name_returns_same_file_no_duplicate(
        self, person_store: PersonStore
    ) -> None:
        """A second get_or_create call creates NO new file."""
        p1 = person_store.get_or_create("Casey", relationship="colleague")
        p2 = person_store.get_or_create("Casey", relationship="colleague")
        assert p1.path == p2.path
        # Only one file in people/
        assert len(person_store.list_all()) == 1

    def test_umlauts_in_name_become_slug(self, person_store: PersonStore) -> None:
        p = person_store.get_or_create("Casey Müller", relationship="colleague")  # i18n-allow: German umlaut name, matched by the slug-normalization logic under test
        assert p.path.stem == "casey_mueller"


# ======================================================================
# find_by_alias
# ======================================================================

class TestFindByAlias:
    def test_finds_by_exact_slug(self, person_store: PersonStore) -> None:
        person_store.get_or_create("Casey", relationship="colleague")
        found = person_store.find_by_alias("Casey")
        assert found is not None
        assert found.name == "Casey"

    def test_finds_by_case_insensitive_name(self, person_store: PersonStore) -> None:
        person_store.get_or_create("Casey", relationship="colleague")
        found = person_store.find_by_alias("CASEY")
        assert found is not None
        assert found.name == "Casey"

    def test_finds_by_alias_field(self, person_store: PersonStore) -> None:
        """If a person has a nickname in the `aliases` field, find_by_alias must match it."""
        person = person_store.get_or_create("Casey", relationship="colleague")
        assert person.add_alias("Lora") is True
        person.save()

        # Frischer Store — liest vom Disk
        fresh_store = PersonStore(workspace=person_store.workspace)
        found = fresh_store.find_by_alias("Lora")
        assert found is not None
        assert found.name == "Casey"

    def test_returns_none_for_unknown_name(self, person_store: PersonStore) -> None:
        person_store.get_or_create("Casey", relationship="colleague")
        assert person_store.find_by_alias("Unknown") is None

    def test_find_works_on_empty_store(self, person_store: PersonStore) -> None:
        assert person_store.find_by_alias("Whoever") is None


# ======================================================================
# list_all / render_for_prompt
# ======================================================================

class TestListAll:
    def test_lists_all_persons(self, person_store: PersonStore) -> None:
        person_store.get_or_create("Casey", relationship="colleague")
        person_store.get_or_create("Paul", relationship="kollege")
        person_store.get_or_create("Anna", relationship="schwester")
        all_people = person_store.list_all()
        names = sorted(p.name for p in all_people)
        assert names == ["Anna", "Casey", "Paul"]

    def test_empty_store_returns_empty_list(self, person_store: PersonStore) -> None:
        assert person_store.list_all() == []


class TestRenderForPrompt:
    def test_renders_listing_when_people_present(self, person_store: PersonStore) -> None:
        person_store.get_or_create("Casey", relationship="colleague")
        out = person_store.render_for_prompt()
        assert "Casey" in out
        assert "colleague" in out

    def test_renders_empty_string_when_no_people(self, person_store: PersonStore) -> None:
        assert person_store.render_for_prompt() == ""
