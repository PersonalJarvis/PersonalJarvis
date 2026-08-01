"""Full-app tests for the /api/ultrawiki/identity REST surface.

Pattern follows the Explore route tests: a real ``WebServer`` (so the mount,
the OpenAPI metadata and the danger flags are the production ones) with a
hand-wired ``UltraWikiService`` on ``app.state.ultrawiki``.

Offline discipline: no embedding factory, no pipeline, no network. The address
book is the REAL ``ContactStore`` pointed at a tmp directory through
``LOCALAPPDATA`` — so the seed route exercises its production path (which
constructs its own ``ContactStore``) instead of a stand-in that could drift
from it.

The interesting property under test is not the happy path but the CONTRACT the
identity layer promises: similar names are PROPOSED and never merged, a
confirmed merge is exactly reversible, and a decision that was already made
fails loudly instead of silently succeeding twice.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.server import WebServer
from jarvis.ultrawiki import service as uw_service_mod
from jarvis.ultrawiki.service import UltraWikiService

#: Identity routes that MUST carry the x-jarvis-dangerous OpenAPI extra.
DANGEROUS_ROUTES = (
    ("/api/ultrawiki/identity/seed", "post"),
    ("/api/ultrawiki/identity/queue/{queue_id}/confirm", "post"),
    ("/api/ultrawiki/identity/queue/{queue_id}/reject", "post"),
    ("/api/ultrawiki/identity/merges/{merge_id}/unmerge", "post"),
)


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    toml_path = tmp_path / "jarvis.toml"
    toml_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("JARVIS_CONFIG", str(toml_path))
    # user_data_dir() reads LOCALAPPDATA on every platform once it is set, so
    # the real ContactStore the seed route builds lands under tmp — the test
    # can never read (or write) the machine's actual address book.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))

    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    cfg.memory.data_dir = str(tmp_path / "data")
    cfg.ultrawiki.enabled = True

    server = WebServer(cfg, bus=EventBus())
    service = UltraWikiService(cfg, embedding_backend_factory=lambda: None)
    server.app.state.ultrawiki = service
    uw_service_mod.clear_jobs()
    with TestClient(server.app) as client:
        yield SimpleNamespace(
            client=client, service=service, server=server, cfg=cfg, tmp=tmp_path
        )
        client.portal.call(service.shutdown)
    uw_service_mod.clear_jobs()


@pytest.fixture
def address_book(env):
    """Two contacts whose names are similar enough to be PROPOSED, not merged.

    "Viktoria Novak" and "Viktor Novak" share no e-mail, phone or contact slug,
    so the layer has nothing deterministic to merge on — exactly the case the
    confirmation queue exists for.
    """
    from jarvis.contacts.store import ContactStore

    store = ContactStore()
    store.put(
        name="Viktoria Novak",
        aliases=["Viki"],
        emails=["viktoria@example.com"],
        phones=["+49 151 2345 6789"],
    )
    store.put(name="Viktor Novak", phones=["+49 151 9999 0000"])
    return store


def seed(env) -> dict:
    response = env.client.post("/api/ultrawiki/identity/seed")
    assert response.status_code == 200, response.text
    return response.json()


def people(env, **params) -> list[dict]:
    response = env.client.get("/api/ultrawiki/identity/people", params=params)
    assert response.status_code == 200, response.text
    return response.json()["people"]


def queue(env, **params) -> list[dict]:
    response = env.client.get("/api/ultrawiki/identity/queue", params=params)
    assert response.status_code == 200, response.text
    return response.json()["proposals"]


# ---------------------------------------------------------------------------
# Seeding + the People list
# ---------------------------------------------------------------------------


def test_seeding_fills_the_people_list_and_repeats_harmlessly(env, address_book):
    body = seed(env)

    assert body["report"]["created"] == 2
    assert body["report"]["merged"] == 0
    assert {person["display_name"] for person in people(env)} == {
        "Viktoria Novak",
        "Viktor Novak",
    }

    again = seed(env)

    # Idempotent by contract: a second pass links what exists, creates nothing.
    assert again["report"]["created"] == 0
    assert again["report"]["linked"] == 2
    assert len(people(env)) == 2


def test_people_can_be_filtered_by_name_or_by_identifier(env, address_book):
    seed(env)

    by_name = people(env, q="viktoria")
    by_email = people(env, q="viktoria@example.com")
    by_phone = people(env, q="9999")

    assert [p["display_name"] for p in by_name] == ["Viktoria Novak"]
    assert [p["display_name"] for p in by_email] == ["Viktoria Novak"]
    assert [p["display_name"] for p in by_phone] == ["Viktor Novak"]


def test_every_list_carries_the_counters_that_explain_an_empty_one(env, address_book):
    empty = env.client.get("/api/ultrawiki/identity/people").json()

    # Nothing seeded yet: the counters say so instead of leaving an empty list
    # indistinguishable from "your filter matched nobody".
    assert empty["people"] == []
    assert empty["counts"]["people"] == 0

    seed(env)
    filtered = env.client.get(
        "/api/ultrawiki/identity/people", params={"q": "nobody-by-this-name"}
    ).json()

    assert filtered["people"] == []
    assert filtered["counts"]["people"] == 2
    assert filtered["counts"]["pending_confirmations"] == 1


def test_person_profile_carries_every_identifier_and_the_open_proposal(
    env, address_book
):
    seed(env)
    viktoria = next(p for p in people(env) if p["display_name"] == "Viktoria Novak")

    response = env.client.get(f"/api/ultrawiki/identity/people/{viktoria['id']}")

    assert response.status_code == 200, response.text
    profile = response.json()["person"]
    assert response.json()["forwarded"] is False
    assert profile["emails"] == ["viktoria@example.com"]
    assert profile["phones"] == ["+4915123456789"]
    assert "Viki" in profile["names"]
    assert len(profile["pending_proposals"]) == 1


def test_unknown_person_is_a_404_not_an_empty_profile(env, address_book):
    seed(env)

    assert env.client.get("/api/ultrawiki/identity/people/424242").status_code == 404


# ---------------------------------------------------------------------------
# The confirmation queue
# ---------------------------------------------------------------------------


def test_similar_names_are_proposed_and_never_merged_on_their_own(env, address_book):
    seed(env)

    proposals = queue(env)

    assert len(proposals) == 1
    assert {proposals[0]["left"]["display_name"], proposals[0]["right"]["display_name"]} == {
        "Viktoria Novak",
        "Viktor Novak",
    }
    assert proposals[0]["status"] == "pending"
    # Proposed is not merged: both people are still their own entity, and the
    # audit trail is empty because nothing was fused.
    assert len(people(env)) == 2
    assert env.client.get("/api/ultrawiki/identity/merges").json()["merges"] == []


def test_an_unknown_queue_status_is_refused_rather_than_widened(env, address_book):
    seed(env)

    response = env.client.get(
        "/api/ultrawiki/identity/queue", params={"status": "maybe"}
    )

    assert response.status_code == 400
    assert "maybe" in response.json()["detail"]


def test_status_all_lists_decided_proposals_too(env, address_book):
    seed(env)
    proposal_id = queue(env)[0]["id"]

    rejected = env.client.post(
        f"/api/ultrawiki/identity/queue/{proposal_id}/reject"
    )

    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    assert queue(env) == []
    assert [p["status"] for p in queue(env, status="all")] == ["rejected"]
    # Rejecting keeps them apart — it never merges anything.
    assert len(people(env)) == 2


# ---------------------------------------------------------------------------
# Confirm → merge → unmerge, the reversibility contract
# ---------------------------------------------------------------------------


def test_confirmed_merge_round_trips_through_unmerge(env, address_book):
    seed(env)
    before = {p["id"]: p["display_name"] for p in people(env)}
    proposal_id = queue(env)[0]["id"]

    confirmed = env.client.post(
        f"/api/ultrawiki/identity/queue/{proposal_id}/confirm"
    )

    assert confirmed.status_code == 200, confirmed.text
    merge_id = confirmed.json()["merge_id"]
    assert merge_id > 0
    assert len(people(env)) == 1
    assert queue(env) == []

    merges = env.client.get("/api/ultrawiki/identity/merges").json()["merges"]
    assert [m["id"] for m in merges] == [merge_id]
    assert merges[0]["undone_at"] is None

    undone = env.client.post(f"/api/ultrawiki/identity/merges/{merge_id}/unmerge")

    assert undone.status_code == 200, undone.text
    assert undone.json()["status"] == "undone"
    assert {p["id"]: p["display_name"] for p in people(env)} == before
    assert env.client.get("/api/ultrawiki/identity/merges").json()["merges"][0][
        "undone_at"
    ]


def test_a_merged_away_id_forwards_instead_of_dead_ending(env, address_book):
    seed(env)
    proposal_id = queue(env)[0]["id"]
    confirmed = env.client.post(
        f"/api/ultrawiki/identity/queue/{proposal_id}/confirm"
    ).json()
    survivor = people(env)[0]["id"]
    merges = env.client.get("/api/ultrawiki/identity/merges").json()["merges"]
    loser = next(m["loser_id"] for m in merges if m["id"] == confirmed["merge_id"])

    response = env.client.get(f"/api/ultrawiki/identity/people/{loser}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["forwarded"] is True
    assert body["person"]["id"] == survivor
    assert body["person"]["requested_id"] == loser


def test_merges_can_be_scoped_to_one_person(env, address_book):
    seed(env)
    proposal_id = queue(env)[0]["id"]
    merge_id = env.client.post(
        f"/api/ultrawiki/identity/queue/{proposal_id}/confirm"
    ).json()["merge_id"]
    survivor = people(env)[0]["id"]

    scoped = env.client.get(
        "/api/ultrawiki/identity/merges", params={"entity_id": survivor}
    ).json()
    unrelated = env.client.get(
        "/api/ultrawiki/identity/merges", params={"entity_id": 999_999}
    ).json()

    assert [m["id"] for m in scoped["merges"]] == [merge_id]
    assert unrelated["merges"] == []


# ---------------------------------------------------------------------------
# Refusals — a decision already made must fail loudly, not silently repeat
# ---------------------------------------------------------------------------


def test_deciding_twice_is_a_409_with_the_honest_reason(env, address_book):
    seed(env)
    proposal_id = queue(env)[0]["id"]
    env.client.post(f"/api/ultrawiki/identity/queue/{proposal_id}/confirm")

    again = env.client.post(f"/api/ultrawiki/identity/queue/{proposal_id}/confirm")
    rejecting_an_applied_one = env.client.post(
        f"/api/ultrawiki/identity/queue/{proposal_id}/reject"
    )

    assert again.status_code == 409
    assert "already" in again.json()["detail"]
    assert rejecting_an_applied_one.status_code == 409


def test_undoing_an_unknown_or_already_undone_merge_is_a_409(env, address_book):
    seed(env)
    proposal_id = queue(env)[0]["id"]
    merge_id = env.client.post(
        f"/api/ultrawiki/identity/queue/{proposal_id}/confirm"
    ).json()["merge_id"]
    env.client.post(f"/api/ultrawiki/identity/merges/{merge_id}/unmerge")

    again = env.client.post(f"/api/ultrawiki/identity/merges/{merge_id}/unmerge")
    unknown = env.client.post("/api/ultrawiki/identity/merges/424242/unmerge")

    assert again.status_code == 409
    assert unknown.status_code == 409
    assert "424242" in unknown.json()["detail"]


def test_unmerging_makes_the_split_stick(env, address_book):
    """The pair must not be re-proposed by the same evidence after an undo."""
    seed(env)
    proposal_id = queue(env)[0]["id"]
    merge_id = env.client.post(
        f"/api/ultrawiki/identity/queue/{proposal_id}/confirm"
    ).json()["merge_id"]
    env.client.post(f"/api/ultrawiki/identity/merges/{merge_id}/unmerge")

    seed(env)  # re-running the import must not fuse them again

    assert len(people(env)) == 2
    assert queue(env) == []


# ---------------------------------------------------------------------------
# Contract metadata + mode discipline
# ---------------------------------------------------------------------------


def test_destructive_identity_routes_declare_the_danger_flag(env):
    schema = env.server.app.openapi()

    for path, method in DANGEROUS_ROUTES:
        operation = schema["paths"][path][method]
        assert operation.get("x-jarvis-dangerous") is True, f"{method} {path}"


def test_identity_routes_are_tagged_for_the_cli(env):
    schema = env.server.app.openapi()

    for path in (
        "/api/ultrawiki/identity/people",
        "/api/ultrawiki/identity/queue",
        "/api/ultrawiki/identity/merges",
    ):
        assert schema["paths"][path]["get"]["tags"] == ["ultrawiki"]


def test_identity_answers_409_while_ultra_mode_is_off(env):
    env.cfg.ultrawiki.enabled = False

    for path in (
        "/api/ultrawiki/identity/people",
        "/api/ultrawiki/identity/queue",
        "/api/ultrawiki/identity/merges",
    ):
        assert env.client.get(path).status_code == 409, path
    assert env.client.post("/api/ultrawiki/identity/seed").status_code == 409
