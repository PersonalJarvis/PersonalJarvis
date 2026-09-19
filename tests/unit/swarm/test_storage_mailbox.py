"""Durable addressed peer delivery with fairness and bounded backpressure."""

import pytest

from jarvis.core.swarm_types import PeerMessage
from jarvis.swarm.store import SwarmAccessError, SwarmConflictError
from tests.fakes.swarm_storage import running_team


def message(key, recipients=None, **kwargs):
    return PeerMessage(
        intent="SHARE_FINDING",
        task_id="work",
        summary="Useful peer data",
        request_key=key,
        recipients=recipients or [],
        **kwargs,
    )


def test_redelivery_ack_dedup_and_sender_forgery_resistance(tmp_path):
    fixture = running_team(tmp_path)
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    lead = fixture.store.actor_for(fixture.controller, fixture.team["lead_id"])
    sent = fixture.store.send_message(actor, message("finding"))
    assert sent["sender_id"] == actor.agent_id
    assert sent["authority"] == "peer_data"
    assert fixture.store.send_message(actor, message("finding")) == sent
    assert fixture.store.messages(lead)[0]["delivery_count"] == 1
    assert fixture.store.messages(lead) == []
    fixture.clock.now += 31
    assert fixture.store.messages(lead)[0]["delivery_count"] == 2
    assert fixture.store.ack_message(lead, sent["id"])
    assert fixture.store.ack_message(lead, sent["id"])
    fixture.clock.now += 31
    assert fixture.store.messages(lead) == []
    with pytest.raises(SwarmAccessError):
        fixture.store.ack_message(actor, sent["id"])
    assert fixture.store.records("reputation") == []


def test_sender_round_robin_prevents_priority_monopoly(tmp_path):
    fixture = running_team(tmp_path)
    fast = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    other = fixture.store.add_agent(fixture.controller, "Other")
    lead = fixture.store.actor_for(fixture.controller, fixture.team["lead_id"])
    for index in range(10):
        fixture.store.send_message(fast, message(str(index), priority=9))
    fixture.store.send_message(other, message("one", priority=1))
    assert {item["sender_id"] for item in fixture.store.messages(lead, limit=2)} == {
        fast.agent_id,
        other.agent_id,
    }


def test_rate_limit_and_changed_idempotency_payload_rejected(tmp_path):
    fixture = running_team(tmp_path)
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    for index in range(30):
        fixture.store.send_message(actor, message(str(index)))
    with pytest.raises(SwarmConflictError, match="rate"):
        fixture.store.send_message(actor, message("overflow"))
    with pytest.raises(SwarmConflictError, match="different"):
        fixture.store.send_message(actor, message("0", priority=0))
    fixture.clock.now += 61
    fixture.store.send_message(actor, message("later"))


def test_topic_fanout_and_subscription_limits_are_bounded(tmp_path):
    fixture = running_team(tmp_path, limits={"worker_limit": "30"})
    for index in range(17):
        subscriber = fixture.store.add_agent(fixture.controller, str(index))
        fixture.store.subscribe(subscriber, "topic")
    with pytest.raises(SwarmConflictError, match="fan-out"):
        fixture.store.send_message(fixture.member, message("broadcast", topic="topic"))
    for index in range(32):
        fixture.store.subscribe(fixture.member, str(index))
    fixture.store.subscribe(fixture.member, "0")
    with pytest.raises(SwarmConflictError, match="subscriptions"):
        fixture.store.subscribe(fixture.member, "overflow")


def test_expired_messages_do_not_redeliver_and_retention_preserves_evidence(tmp_path):
    fixture = running_team(tmp_path)
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    lead = fixture.store.actor_for(fixture.controller, fixture.team["lead_id"])
    artifact = fixture.store.write_artifact(actor, "Finding", "data", "artifact")
    sent = fixture.store.send_message(
        actor, message("expiring", deadline=fixture.clock.now + 10, evidence=[artifact["id"]])
    )
    fixture.store.messages(lead)
    fixture.store.ack_message(lead, sent["id"])
    fixture.clock.now += 11
    assert fixture.store.messages(lead) == []
    assert fixture.store.retain(fixture.controller, fixture.clock.now)["expired_messages"] == "1"
    assert fixture.store.read_artifact(actor, artifact["id"])["content"] == "data"
