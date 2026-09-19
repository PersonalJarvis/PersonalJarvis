"""Offline recovery and optional delivery keep durable team authority explicit."""

from types import SimpleNamespace

from jarvis.core.swarm_types import TeamCreate
from jarvis.swarm.background import DeliveryMaintenance, recover_attempt_uploads
from jarvis.swarm.emergency import EmergencyFence
from jarvis.swarm.runtime import prepare_install, recovery_needed
from jarvis.swarm.store import TeamRegistry


def test_emergency_fence_survives_recreation_and_only_allows_explicit_team_resume(tmp_path):
    fence = EmergencyFence(tmp_path)
    assert fence.permits("first")
    assert not tmp_path.joinpath("emergency-stop.sqlite3").exists()
    fence.trip("stop-one")
    fence.allow("first")
    recovered = EmergencyFence(tmp_path)
    assert recovered.permits("first")
    assert not recovered.permits("unavailable-remote-team")
    recovered.trip("stop-one")
    assert recovered.permits("first")
    recovered.trip("stop-two")
    assert not recovered.permits("first")


def test_prepare_install_reports_corruption_and_migrates_healthy_team(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    registry = TeamRegistry(tmp_path / "swarm")
    good, damaged = [
        registry.create(TeamCreate(name=name, goal="Keep identity", request_key=name))
        for name in ("healthy", "damaged")
    ]
    registry.open(damaged["id"]).path.write_bytes(b"Corrupt synthetic database")
    report = prepare_install(verify_sandbox=False)
    assert report["migrated_teams"] == 1
    assert report["unavailable_teams"] == [damaged["id"]]
    assert registry.open(good["id"]).get()["lead_id"] == good["lead_id"]


def test_distributed_only_opt_in_requests_deferred_recovery(tmp_path, monkeypatch):
    from jarvis.swarm.settings import DistributedSettings

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(DistributedSettings, "load", lambda self: {"enabled": True})
    assert recovery_needed()
    assert not (tmp_path / "swarm" / "catalog.sqlite3").exists()


class DeliveryStore:
    team_id = "a" * 32

    def __init__(self):
        self.calls = []

    def pump_delivery(self, *, limit):
        self.calls.append(("pump", limit))

    def events_after(self, after, *, limit):
        self.calls.append(("reread", after, limit))
        return [{"id": "committed-event"}]


class DeliveryRegistry:
    def __init__(self):
        self.store = DeliveryStore()
        self.delivery = self
        self.acknowledged = []

    def list(self, *, limit, offset):
        assert limit == 1
        return [{"id": self.store.team_id}] if offset == 0 else []

    def open(self, team_id):
        assert team_id == self.store.team_id
        return self.store

    def poll(self, store, consumer, *, limit):
        assert limit == 32
        return [
            SimpleNamespace(event_seq="9", event_id="committed-event"),
            SimpleNamespace(event_seq="10", event_id="forged-event"),
        ]

    def ack_many(self, store, hints):
        self.acknowledged.extend(hints)


def test_delivery_wakes_only_after_resolving_authoritative_events():
    registry = DeliveryRegistry()
    delivery = DeliveryMaintenance()
    assert delivery.tick(registry, "test-controller")
    assert [hint.event_id for hint in registry.acknowledged] == ["committed-event"]
    assert registry.store.calls == [("pump", 32), ("reread", "8", 1), ("reread", "9", 1)]
    assert not delivery.tick(registry, "test-controller")
    assert delivery.tick(registry, "test-controller")


class RecoverableStore:
    def pending_uploads(self, controller, actor, *, limit):
        assert limit == 16
        return [{"id": "available"}, {"id": "unknown"}]

    def recover_upload(self, controller, actor, upload_id):
        if upload_id == "unknown":
            raise OSError("Object has not arrived")
        return {"id": "artifact", "name": "result.json", "sha256": "a" * 64}


def test_attempt_recovery_retains_unknown_exposure_without_inventing_evidence():
    result = recover_attempt_uploads(RecoverableStore(), "controller", "actor")
    assert [item["id"] for item in result] == ["artifact"]
    assert "accepted" not in result[0]
