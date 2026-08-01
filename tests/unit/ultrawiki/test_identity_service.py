"""The identity facade on ``UltraWikiService`` — the surface later phases call.

Offline, tmp store, a real (temporary) contacts directory instead of a mock:
the point is that the People view, the CLI and the brain tools can drive the
whole confirm/reject/unmerge cycle through the service without reaching into
the store.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.contacts.store import ContactStore
from jarvis.ultrawiki.identity import IdentityError
from jarvis.ultrawiki.service import UltraWikiService


def make_cfg(tmp_path) -> SimpleNamespace:
    """UltraWiki disabled: the store opens, the ingest worker stays asleep."""
    return SimpleNamespace(
        ultrawiki=SimpleNamespace(
            enabled=False,
            db_backend="sqlite",
            embedding_provider="",
            embedding_model="",
            distill_provider="",
            distill_model="",
            rerank_provider="",
            ollama_endpoint="",
        ),
        memory=SimpleNamespace(data_dir=str(tmp_path)),
        brain=SimpleNamespace(primary=""),
    )


@pytest.fixture
async def service(tmp_path):
    instance = UltraWikiService(make_cfg(tmp_path))
    await instance.ensure_started()
    yield instance
    await instance.shutdown()


@pytest.fixture
def contacts(tmp_path) -> ContactStore:
    store = ContactStore(base_dir=tmp_path / "contacts")
    store.put(
        name="Viktoria Novak",
        aliases=["Viki"],
        emails=["viktoria@example.com"],
        phones=["+49 151 2345 6789"],
    )
    store.put(name="Viktor Novak", phones=["+49 151 9999 0000"])
    return store


async def test_seed_then_list_people(service, contacts):
    report = await service.seed_identities(contact_store=contacts)
    assert report["created"] == 2
    assert report["merged"] == 0

    people = await service.list_people()
    assert {person["display_name"] for person in people} == {
        "Viktoria Novak",
        "Viktor Novak",
    }
    profile = await service.person_profile(people[0]["id"])
    assert profile["id"] == people[0]["id"]
    assert profile["identifiers"]

    # A second pass changes nothing (idempotent by contract).
    again = await service.seed_identities(contact_store=contacts)
    assert again["created"] == 0
    assert again["linked"] == 2


async def test_queue_confirm_and_unmerge_through_the_service(service, contacts):
    await service.seed_identities(contact_store=contacts)
    queue = await service.identity_queue()
    assert len(queue) == 1, "the two similar names must be PROPOSED, not merged"

    confirmed = await service.confirm_identity_merge(queue[0]["id"])
    assert confirmed["merge_id"] > 0
    assert len(await service.list_people()) == 1
    assert await service.identity_queue() == []

    undone = await service.unmerge_identity(confirmed["merge_id"])
    assert undone["status"] == "undone"
    assert len(await service.list_people()) == 2

    log = await service.identity_merge_log()
    assert log[0]["id"] == confirmed["merge_id"]
    assert log[0]["undone_at"] is not None


async def test_rejecting_through_the_service_empties_the_queue(service, contacts):
    await service.seed_identities(contact_store=contacts)
    queue = await service.identity_queue()
    result = await service.reject_identity_merge(queue[0]["id"])
    assert result["status"] == "rejected"
    assert await service.identity_queue() == []
    assert len(await service.list_people()) == 2


async def test_unknown_ids_fail_honestly(service):
    assert await service.person_profile(4242) is None
    with pytest.raises(IdentityError):
        await service.confirm_identity_merge(4242)
    with pytest.raises(IdentityError):
        await service.unmerge_identity(4242)
