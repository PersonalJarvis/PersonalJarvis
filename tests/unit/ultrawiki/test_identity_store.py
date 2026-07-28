"""The identity layer against a real (temporary) store — P5's gate.

Everything here runs offline against a fresh SQLite file: no network, no
credentials, no model. The centrepiece is :func:`test_golden_set_resolves_with_
zero_wrong_merges`, which drives a deliberately nasty fixture set — the same
person arriving three times in three formats, two different people who look
alike, a nickname, and the speech-to-text near-name variants observed in the
live vault — and then proves the two properties the design stands or falls on:

1. every automatic merge was justified by a UNIQUE identifier, and
2. every uncertain case is sitting in the confirmation queue instead.
"""

from __future__ import annotations

import pytest

from jarvis.contacts.store import ContactStore
from jarvis.ultrawiki.identity import (
    EntityKind,
    IdentifierKind,
    IdentityError,
    MatchTier,
    QueueStatus,
    ResolutionKind,
)
from jarvis.ultrawiki.identity_store import seed_from_contacts
from jarvis.ultrawiki.store import PostgresStore, UltraStore


@pytest.fixture
async def store(tmp_path):
    instance = UltraStore(tmp_path / "ultrawiki.db")
    await instance.open()
    yield instance
    await instance.close()


async def identifier_pairs(store: UltraStore, entity_id: int) -> set[tuple[str, str]]:
    profile = await store.get_person(entity_id)
    assert profile is not None
    return {(item["kind"], item["value"]) for item in profile["identifiers"]}


async def snapshot(store: UltraStore) -> list[tuple[str, set[tuple[str, str]]]]:
    """Everything an unmerge has to restore: who exists and what they own."""
    out = []
    for person in await store.list_people(kind=None, limit=500):
        out.append(
            (person["display_name"], await identifier_pairs(store, person["id"]))
        )
    return sorted(out, key=lambda entry: entry[0])


async def queue_pairs(store: UltraStore) -> set[frozenset[str]]:
    return {
        frozenset({entry["left"]["display_name"], entry["right"]["display_name"]})
        for entry in await store.list_confirm_queue(limit=200)
    }


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


async def test_identity_migration_is_idempotent(tmp_path):
    """Re-opening an existing store must be a no-op, including for a database
    that was created before the identity tables existed."""
    path = tmp_path / "ultrawiki.db"
    first = UltraStore(path)
    await first.open()
    entity_id = await first.upsert_entity(display_name="Viktoria Novak")
    await first.close()

    second = UltraStore(path)
    await second.open()
    assert await second.get_entity(entity_id) is not None
    assert (await second.identity_counts())["people"] == 1
    await second.close()


async def test_postgres_mirrors_the_identity_ddl():
    """The two backends run the same logical schema (design doc 01)."""
    ddl = "\n".join(PostgresStore.ddl_statements())
    for table in ("uw_entities", "uw_identifiers", "uw_confirm_queue", "uw_merge_log"):
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in ddl
    assert PostgresStore._IDENTITY_PARAM == "%s"
    assert PostgresStore("postgresql://x")._id_sql("WHERE a = ? AND b = ?") == (
        "WHERE a = %s AND b = %s"
    )


# ---------------------------------------------------------------------------
# The golden set — P5's gate
# ---------------------------------------------------------------------------


async def test_golden_set_resolves_with_zero_wrong_merges(store):
    same_person = [
        # One person, three sources, three spellings and two phone formats.
        {
            "name": "Viktoria Novak",
            "emails": ["viktoria@example.com"],
            "phones": ["+49 151 2345 6789"],
            "contact_slug": "viktoria-novak",
        },
        {"name": "V. Novak", "emails": ["VIKTORIA@Example.com"]},
        {"name": "Novak, Viktoria", "phones": ["0049-151-23456789"]},
    ]
    distinct_people = [
        # A brother: same surname, similar given name, own phone number.
        {"name": "Viktor Novak", "phones": ["+49 151 9999 0000"]},
        # A nickname with no other evidence at all.
        {"name": "Viki"},
        # Two people who merely share a surname.
        {"name": "John Smith", "emails": ["john@example.com"]},
        {"name": "Jane Smith", "emails": ["jane@example.com"]},
    ]

    resolved = []
    for observation in same_person:
        resolved.append(await store.resolve_identity(**observation))
    distinct_ids = []
    for observation in distinct_people:
        distinct_ids.append((await store.resolve_identity(**observation)).entity_id)

    # 1 — the three views of one person collapsed onto ONE entity, and every
    #     collapse was deterministic.
    assert {item.entity_id for item in resolved} == {resolved[0].entity_id}
    assert resolved[0].kind is ResolutionKind.CREATED
    assert resolved[1].kind is ResolutionKind.DETERMINISTIC
    assert resolved[2].kind is ResolutionKind.DETERMINISTIC

    # 2 — the four other people are four separate entities, none of them fused
    #     with anybody.
    assert len(set(distinct_ids)) == 4
    assert resolved[0].entity_id not in distinct_ids
    assert len(await store.list_people(limit=100)) == 5

    # 3 — NO merge was ever justified by anything but a unique identifier.
    for entry in await store.list_merge_log(limit=100):
        assert entry["tier"] == str(MatchTier.DETERMINISTIC)
        assert entry["evidence"]
        assert all(
            item["kind"] in {"email", "phone", "contact"} for item in entry["evidence"]
        )

    # 4 — the uncertain cases are queued, and only those.
    pairs = await queue_pairs(store)
    assert frozenset({"Viktoria Novak", "Viktor Novak"}) in pairs
    assert frozenset({"Viktoria Novak", "Viki"}) in pairs
    assert frozenset({"John Smith", "Jane Smith"}) not in pairs


async def test_speech_to_text_variants_queue_and_never_merge(store):
    """The live vault's actual failure mode: one topic arrives as several
    near-identical names. They must stay separate until a human says otherwise."""
    variants = ["agentic-i", "gentic-ide", "Agentic IDE", "Ultra Wiki", "ultra-wiki"]
    for name in variants:
        await store.resolve_identity(name=name, kind=EntityKind.TOPIC)

    assert await store.list_merge_log(limit=50) == []
    assert len(await store.list_people(kind=EntityKind.TOPIC, limit=50)) == len(variants)
    pairs = await queue_pairs(store)
    assert frozenset({"agentic-i", "gentic-ide"}) in pairs
    assert frozenset({"Ultra Wiki", "ultra-wiki"}) in pairs


async def test_case_only_variant_anchors_instead_of_creating_a_twin(store):
    """Identical after case folding is a LOOKUP, not a merge: nothing is
    destroyed and no audit row is written."""
    first = await store.resolve_identity(name="ultra-wiki", kind=EntityKind.TOPIC)
    again = await store.resolve_identity(name="Ultra-Wiki", kind=EntityKind.TOPIC)
    assert again.entity_id == first.entity_id
    assert again.kind is ResolutionKind.NAME_ANCHOR
    assert await store.list_merge_log(limit=10) == []


async def test_ambiguous_name_links_to_nobody_and_proposes_instead(store):
    """A name pointing at several live entities resolves to NONE of them —
    guessing is how a personal memory quietly poisons itself."""
    left = await store.resolve_identity(
        name="Alex Winter", emails=["alex.w@example.com"]
    )
    right = await store.resolve_identity(name="A. Winter", phones=["+49 151 7777 000"])
    # The second identity legitimately acquires the same display name later.
    await store.add_identifier(right.entity_id, IdentifierKind.NAME, "Alex Winter")

    ambiguous = await store.resolve_identity(name="Alex Winter")
    assert ambiguous.entity_id is None
    assert ambiguous.kind is ResolutionKind.AMBIGUOUS
    assert set(ambiguous.ambiguous) == {left.entity_id, right.entity_id}
    assert await store.list_merge_log(limit=10) == []
    assert len(await store.list_confirm_queue()) == 1


async def test_create_false_never_writes_anything(store):
    unresolved = await store.resolve_identity(name="Nobody Known", create=False)
    assert unresolved.entity_id is None
    assert unresolved.kind is ResolutionKind.UNRESOLVED
    assert await store.list_people(limit=10) == []


async def test_observation_without_a_name_is_not_given_one(store):
    """An entity labelled after an e-mail must not carry that address as a
    'name' — every other address at the domain would look like a near-name."""
    resolution = await store.resolve_identity(emails=["someone@example.com"])
    profile = await store.get_person(resolution.entity_id)
    assert profile["names"] == []
    assert profile["emails"] == ["someone@example.com"]


# ---------------------------------------------------------------------------
# Merge, unmerge, and the durability of a decision
# ---------------------------------------------------------------------------


async def test_confirmed_merge_round_trips_through_unmerge(store):
    left = await store.resolve_identity(
        name="Chris Meyer", emails=["chris@example.com"]
    )
    right = await store.resolve_identity(
        name="Christoph Meyer", phones=["+49 151 0000 111"]
    )
    before = await snapshot(store)
    proposal = (await store.list_confirm_queue())[0]

    merge_id = await store.confirm_merge(proposal["id"])
    assert merge_id > 0
    assert len(await store.list_people(limit=10)) == 1
    # A stale link to the merged-away id still lands on the survivor.
    assert await store.resolve_entity_id(right.entity_id) == left.entity_id
    survivor = await store.get_person(right.entity_id)
    assert survivor["id"] == left.entity_id
    assert survivor["requested_id"] == right.entity_id
    assert ("phone", "+491510000111") in await identifier_pairs(store, left.entity_id)

    assert await store.unmerge(merge_id) is True
    assert await snapshot(store) == before
    assert [entry["undone_at"] for entry in await store.list_merge_log(limit=10)] != [
        None
    ]


async def test_unmerge_makes_the_split_stick_against_deterministic_evidence(store):
    """Undoing a merge is a decision, not a suggestion. A later observation
    carrying both identifiers must not silently fuse the pair again — a shared
    mailbox or a family phone is exactly how that would keep happening."""
    left = await store.resolve_identity(name="Sam Fox", emails=["family@example.com"])
    right = await store.resolve_identity(name="Robin Fox", phones=["+49 151 1234 000"])
    merge_id = await store.merge_entities(
        left.entity_id, right.entity_id, tier=MatchTier.DETERMINISTIC
    )
    await store.unmerge(merge_id)

    again = await store.resolve_identity(
        name="Sam Fox", emails=["family@example.com"], phones=["+49 151 1234 000"]
    )
    assert again.merged == ()
    assert len(await store.list_people(limit=10)) == 2
    decided = await store.list_confirm_queue(status=QueueStatus.REJECTED)
    assert len(decided) == 1


async def test_rejecting_a_proposal_is_permanent(store):
    await store.resolve_identity(name="agentic-i", kind=EntityKind.TOPIC)
    await store.resolve_identity(name="gentic-ide", kind=EntityKind.TOPIC)
    proposal = (await store.list_confirm_queue())[0]
    assert await store.reject_merge(proposal["id"]) is True

    assert await store.list_confirm_queue() == []
    # Re-observing the same weak evidence must not ask a second time.
    await store.resolve_identity(name="gentic-ides", kind=EntityKind.TOPIC)
    reopened = [
        entry
        for entry in await store.list_confirm_queue()
        if {entry["left"]["display_name"], entry["right"]["display_name"]}
        == {"agentic-i", "gentic-ide"}
    ]
    assert reopened == []


async def test_double_decisions_are_refused_honestly(store):
    await store.resolve_identity(name="agentic-i", kind=EntityKind.TOPIC)
    await store.resolve_identity(name="gentic-ide", kind=EntityKind.TOPIC)
    proposal = (await store.list_confirm_queue())[0]
    merge_id = await store.confirm_merge(proposal["id"])

    with pytest.raises(IdentityError):
        await store.confirm_merge(proposal["id"])
    with pytest.raises(IdentityError):
        await store.reject_merge(proposal["id"])
    await store.unmerge(merge_id)
    with pytest.raises(IdentityError):
        await store.unmerge(merge_id)
    with pytest.raises(IdentityError):
        await store.unmerge(9999)


async def test_merge_winner_is_deterministic_not_arbitrary(store):
    """Re-running an import must converge on the same graph, so the survivor
    is chosen by rule: a curated address-book record first, then the older id."""
    inferred = await store.resolve_identity(name="Dana Reed", phones=["+49 151 5555 0"])
    seeded = await store.resolve_identity(
        name="D. Reed", emails=["dana@example.com"], contact_slug="dana-reed"
    )
    await store.set_entity_source_ref(seeded.entity_id, "contacts:dana-reed")
    assert seeded.entity_id != inferred.entity_id

    # One later observation carries BOTH unique identifiers, so the two are
    # provably one person — and the curated record is the survivor even though
    # it is the younger row.
    joined = await store.resolve_identity(
        name="Dana Reed",
        phones=["+49 151 5555 0"],
        emails=["dana@example.com"],
    )
    assert joined.merged
    assert joined.entity_id == seeded.entity_id
    assert await store.resolve_entity_id(inferred.entity_id) == seeded.entity_id


async def test_merge_moves_identifiers_and_keeps_the_audit_trail(store):
    left = await store.resolve_identity(name="Lee Park", emails=["lee@example.com"])
    right = await store.resolve_identity(name="Lee Parker", phones=["+49 151 4444 0"])
    merge_id = await store.merge_entities(
        left.entity_id,
        right.entity_id,
        tier=MatchTier.DETERMINISTIC,
        reason="test merge",
    )
    entry = (await store.list_merge_log(entity_id=left.entity_id))[0]
    assert entry["id"] == merge_id
    assert entry["winner_id"] == left.entity_id
    assert entry["loser_id"] == right.entity_id
    assert entry["reason"] == "test merge"
    assert entry["undone_at"] is None

    profile = await store.get_person(left.entity_id)
    assert {item["id"] for item in profile["merged_from"]} == {right.entity_id}
    assert ("name", "lee parker") in await identifier_pairs(store, left.entity_id)


async def test_merge_is_a_no_op_on_one_entity(store):
    only = await store.resolve_identity(name="Solo Person")
    assert await store.merge_entities(only.entity_id, only.entity_id) == 0


# ---------------------------------------------------------------------------
# Identifier attachment
# ---------------------------------------------------------------------------


async def test_shared_email_merges_but_shared_name_only_proposes(store):
    first = await store.resolve_identity(name="Kim Alvarez")
    second = await store.resolve_identity(name="Kim Alvarez Jr")

    proposed = await store.add_identifier(
        second.entity_id, IdentifierKind.NAME, "Kim Alvarez"
    )
    assert proposed.merged == ()
    assert proposed.queued
    assert len(await store.list_people(limit=10)) == 2

    merged = await store.add_identifier(
        second.entity_id, IdentifierKind.EMAIL, "kim@example.com"
    )
    assert merged.merged == ()
    merged_now = await store.add_identifier(
        first.entity_id, IdentifierKind.EMAIL, "kim@example.com"
    )
    assert merged_now.merged
    assert len(await store.list_people(limit=10)) == 1


async def test_unusable_identifiers_are_dropped_not_stored(store):
    person = await store.resolve_identity(name="Robin Stone")
    result = await store.add_identifier(person.entity_id, IdentifierKind.EMAIL, "junk")
    assert result.identifier_id is None
    assert ("email", "junk") not in await identifier_pairs(store, person.entity_id)
    with pytest.raises(IdentityError):
        await store.add_identifier(person.entity_id, "nonsense", "value")
    with pytest.raises(IdentityError):
        await store.add_identifier(9999, IdentifierKind.NAME, "Ghost")


async def test_repeated_observation_of_one_person_is_idempotent(store):
    payload = {
        "name": "Nina Bauer",
        "emails": ["nina@example.com"],
        "phones": ["+49 151 3333 0"],
    }
    first = await store.resolve_identity(**payload)
    before = await identifier_pairs(store, first.entity_id)
    for _ in range(3):
        again = await store.resolve_identity(**payload)
        assert again.entity_id == first.entity_id
        assert again.created is False
    assert await identifier_pairs(store, first.entity_id) == before
    assert len(await store.list_people(limit=10)) == 1


# ---------------------------------------------------------------------------
# Contacts seeding
# ---------------------------------------------------------------------------


def build_contacts(tmp_path) -> ContactStore:
    contacts = ContactStore(base_dir=tmp_path / "contacts")
    contacts.put(
        name="Viktoria Novak",
        aliases=["Viki"],
        emails=["viktoria@example.com"],
        phones=["+49 151 2345 6789"],
    )
    contacts.put(name="John Smith", emails=["john@example.com"])
    contacts.put(name="No Contact Details")
    return contacts


async def test_seeding_from_contacts_is_idempotent(store, tmp_path):
    contacts = build_contacts(tmp_path)

    first = await seed_from_contacts(store, contact_store=contacts)
    assert first.created == 3
    assert first.linked == 0
    assert first.merged == 0
    people = await store.list_people(limit=10)
    assert {person["display_name"] for person in people} == {
        "Viktoria Novak",
        "John Smith",
        "No Contact Details",
    }
    assert all(person["source_ref"].startswith("contacts:") for person in people)

    second = await seed_from_contacts(store, contact_store=contacts)
    assert second.created == 0
    assert second.linked == 3
    assert second.identifiers_added == 0
    assert len(await store.list_people(limit=10)) == 3


async def test_seeding_merges_with_what_the_corpus_already_knew(store, tmp_path):
    """A contact whose phone number already appeared during ingest joins that
    entity deterministically instead of becoming a twin."""
    ingested = await store.resolve_identity(
        name="V. Novak", phones=["0049-151-23456789"]
    )
    contacts = build_contacts(tmp_path)

    report = await seed_from_contacts(store, contact_store=contacts)
    assert report.created == 2  # the third contact matched the ingested entity
    assert report.linked == 1
    profile = await store.get_person(ingested.entity_id)
    assert "viktoria@example.com" in profile["emails"]
    assert profile["contacts"] == [contacts.list_all()[2].slug]
    assert "viki" in [name.lower() for name in profile["names"]]


async def test_seeded_alias_becomes_a_searchable_identifier(store, tmp_path):
    await seed_from_contacts(store, contact_store=build_contacts(tmp_path))
    found = await store.list_people(query="viki", limit=10)
    assert [person["display_name"] for person in found] == ["Viktoria Novak"]


async def test_people_list_filters_and_pages(store):
    for name in ("Anna Lang", "Boris Lang", "Cleo Marsh"):
        await store.resolve_identity(name=name)
    assert len(await store.list_people(limit=2)) == 2
    assert len(await store.list_people(limit=2, offset=2)) == 1
    assert {person["display_name"] for person in await store.list_people(query="lang")} == {
        "Anna Lang",
        "Boris Lang",
    }
    assert await store.list_people(query="100%") == []


async def test_counts_report_the_live_graph(store):
    await store.resolve_identity(name="agentic-i", kind=EntityKind.TOPIC)
    await store.resolve_identity(name="gentic-ide", kind=EntityKind.TOPIC)
    await store.resolve_identity(name="Pat Nolan", emails=["pat@example.com"])
    counts = await store.identity_counts()
    assert counts["entities"] == 3
    assert counts["people"] == 1
    assert counts["pending_confirmations"] == 1
    assert counts["merges"] == 0


# ---------------------------------------------------------------------------
# The three ways a decision used to be lost (P5 review, findings 2/3/6)
# ---------------------------------------------------------------------------


async def test_a_rejection_survives_later_merges_on_both_sides(store):
    """The gate the whole layer is measured by: zero wrong auto-merges.

    Rejecting A/B and then letting B merge into C on a shared mailbox, and A
    meet C on a shared phone number, used to fuse A and B again through the
    back door — no queue row, no prompt, and the pair key of the two ids in
    hand never mentioned. A rejection has to hold over the merge closure.
    """
    third = await store.upsert_entity(display_name="Gamma Three")
    left = await store.upsert_entity(display_name="Alpha One")
    right = await store.upsert_entity(display_name="Beta Two")
    await store.unmerge(await store.merge_entities(left, right))
    assert await store.list_confirm_queue(status=QueueStatus.REJECTED)

    await store.add_identifier(right, IdentifierKind.EMAIL, "shared@example.com")
    await store.add_identifier(third, IdentifierKind.EMAIL, "shared@example.com")
    await store.add_identifier(third, IdentifierKind.PHONE, "+49 30 1234567")
    await store.add_identifier(left, IdentifierKind.PHONE, "+49 30 1234567")

    assert await store.resolve_entity_id(left) != await store.resolve_entity_id(right)


async def test_a_rejection_survives_as_a_proposal_too(store):
    """Both halves of the same promise: never re-fuse it, never re-ask it."""
    third = await store.upsert_entity(display_name="Gamma Three")
    left = await store.upsert_entity(display_name="Alpha One")
    right = await store.upsert_entity(display_name="Beta Two")
    await store.unmerge(await store.merge_entities(left, right))
    await store.add_identifier(right, IdentifierKind.EMAIL, "shared@example.com")
    await store.add_identifier(third, IdentifierKind.EMAIL, "shared@example.com")

    # `right` now answers as `third`; proposing left/third is proposing the
    # settled pair under a different id.
    assert await store.resolve_entity_id(right) == third
    result = await store.add_identifier(left, IdentifierKind.NAME, "Beta Two")
    assert result.queued == ()
    assert await store.list_confirm_queue(limit=50) == []


async def test_a_chain_of_merges_undoes_byte_identically(store):
    """Reversibility is claimed for the audit trail, not for one merge.

    Lower -> Middle -> Top used to undo out of order: unmerging the FIRST
    merge put the identifier back on Lower while the second undo then handed
    it to Middle, which never owned it. The undo is refused unless the corpus
    is unwound last-in-first-out.
    """
    lower = await store.upsert_entity(display_name="Lower")
    middle = await store.upsert_entity(display_name="Middle")
    top = await store.upsert_entity(display_name="Top")
    await store.add_identifier(lower, IdentifierKind.EMAIL, "lower@example.com")
    before = await snapshot(store)

    first = await store.merge_entities(middle, lower)
    second = await store.merge_entities(top, middle)
    assert (await store.get_person(top))["emails"] == ["lower@example.com"]

    with pytest.raises(IdentityError, match="shadowed"):
        await store.unmerge(first)

    assert await store.unmerge(second) is True
    assert await store.unmerge(first) is True
    assert await snapshot(store) == before
    assert (await store.get_person(lower))["emails"] == ["lower@example.com"]
    assert (await store.get_person(middle))["emails"] == []


async def test_add_identifier_never_writes_to_an_entity_the_caller_did_not_name(store):
    """A refused merge left the handle on the winner and still reported ok:
    the caller's own entity gained nothing while the REST layer said it had."""
    other = await store.upsert_entity(display_name="Beta Two")
    asked = await store.upsert_entity(display_name="Alpha One")
    await store.unmerge(await store.merge_entities(other, asked))

    await store.add_identifier(other, IdentifierKind.EMAIL, "shared@example.com")
    result = await store.add_identifier(asked, IdentifierKind.EMAIL, "shared@example.com")

    assert result.entity_id == asked
    assert result.merged == ()
    assert (await store.get_person(asked))["emails"] == ["shared@example.com"]
    assert (await store.get_person(other))["emails"] == ["shared@example.com"]


# ---------------------------------------------------------------------------
# Kinds are separate namespaces (P5 review, finding 4)
# ---------------------------------------------------------------------------


async def test_an_identical_name_of_another_kind_is_a_different_entity(store):
    """A town and a person can share a name. Anchoring the town onto the
    person made every later event of that town belong to a human being."""
    person = await store.resolve_identity(name="Marlow", kind=EntityKind.PERSON)
    place = await store.resolve_identity(name="Marlow", kind=EntityKind.PLACE)

    assert place.entity_id != person.entity_id
    assert place.kind is ResolutionKind.CREATED
    assert (await store.get_entity(place.entity_id))["kind"] == str(EntityKind.PLACE)


async def test_a_shared_identifier_never_fuses_across_kinds(store):
    """An address printed on a company page and in a colleague's signature is
    evidence about a mailbox, not about identity."""
    person = await store.resolve_identity(
        name="Ada Hart", emails=["info@example.com"], kind=EntityKind.PERSON
    )
    org = await store.resolve_identity(
        name="Example GmbH", emails=["info@example.com"], kind=EntityKind.ORG
    )
    assert org.entity_id != person.entity_id
    assert org.merged == ()
    assert len(await store.list_people(kind=None, limit=10)) == 2


async def test_near_names_of_different_kinds_are_never_proposed(store):
    await store.resolve_identity(name="Viktoria Novak", kind=EntityKind.PERSON)
    await store.resolve_identity(name="Viktoria Novaks", kind=EntityKind.PLACE)
    assert await store.list_confirm_queue(limit=20) == []


async def test_a_cross_kind_merge_is_refused_honestly(store):
    person = await store.resolve_identity(name="Ada Hart", kind=EntityKind.PERSON)
    place = await store.resolve_identity(name="Porto Verde", kind=EntityKind.PLACE)
    with pytest.raises(IdentityError, match="different kinds"):
        await store.merge_entities(person.entity_id, place.entity_id)
    assert len(await store.list_people(kind=None, limit=10)) == 2
