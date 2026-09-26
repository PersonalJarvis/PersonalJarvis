"""Optional-adapter configuration, SQL binding and object-boundary contracts."""

import hashlib
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.swarm.distributed import (
    DistributedConfig,
    DistributedSecrets,
    create_distributed_registry,
)
from jarvis.swarm.distributed.database import Row, translate
from jarvis.swarm.distributed.objects import S3Objects
from jarvis.swarm.store import SwarmAccessError, SwarmConflictError, SwarmStoreError


def config(**changes):
    settings = dict(
        postgres_dsn="postgresql://swarm@db.example.test/swarm",
        redis_url="rediss://redis.example.test/0",
        s3_endpoint_url="https://s3.example.test",
        s3_bucket="swarm-test-bucket",
        namespace="test_swarm",
    )
    return DistributedConfig(**(settings | changes))


def secrets():
    # These values identify fakes and cannot authenticate to any real service.
    return DistributedSecrets("fixture-pg", "fixture-redis", "fixture-access", "fixture-secret")


class ConditionalConflict(Exception):
    response = {"ResponseMetadata": {"HTTPStatusCode": 412}}


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.requests = []
        self.bodies = []

    def head_bucket(self, **kwargs):
        return {}

    def put_object(self, **kwargs):
        self.requests.append(kwargs)
        key = (kwargs["Bucket"], kwargs["Key"])
        if key in self.objects and kwargs.get("IfNoneMatch") == "*":
            raise ConditionalConflict()
        body = kwargs["Body"]
        self.objects[key] = body.read() if callable(getattr(body, "read", None)) else bytes(body)
        return {"VersionId": "fixture-version"}

    def get_object(self, **kwargs):
        self.requests.append(kwargs)
        data = self.objects[(kwargs["Bucket"], kwargs["Key"])]
        body = io.BytesIO(data)
        self.bodies.append(body)
        return {"Body": body, "ContentLength": len(data), "VersionId": "fixture-version"}

    def close(self):
        pass  # Fake has no resources.


def test_constructing_registry_opens_no_drivers_or_connections():
    registry = create_distributed_registry(config(), secrets())
    assert registry.database._pool is None
    assert registry.objects._client is None
    assert registry.delivery._client is None
    assert "fixture" not in repr(secrets())


@pytest.mark.parametrize(
    "change",
    [
        {"postgres_dsn": "postgresql://swarm:password@db.example.test/swarm"},
        {"postgres_dsn": "postgresql://swarm@db.example.test/swarm?sslmode=disable"},
        {"postgres_dsn": "postgresql://swarm@db.example.test/swarm?password=bad"},
        {"redis_url": "redis://redis.example.test/0"},
        {"s3_endpoint_url": "http://s3.example.test"},
        {"namespace": "scope; DROP SCHEMA public"},
        {"s3_bucket": "../other"},
        {"max_connections": 1000},
    ],
)
def test_configuration_refuses_credential_leakage_injection_and_unverified_tls(change):
    with pytest.raises(ValueError):
        config(**change)


def test_binding_preserves_literal_question_marks_and_percent():
    assert translate("SELECT record FROM tasks WHERE id=? AND title='50% ? isn''t a bind'") == (
        "SELECT record FROM tasks WHERE id=%s AND title='50%% ? isn''t a bind'"
    )
    assert "::bigint" in translate("SELECT max(json_extract(record,'$.level')) FROM agents")
    assert "ON CONFLICT DO NOTHING" in translate(
        "INSERT OR IGNORE INTO subscriptions VALUES (?,?,?)"
    )
    with pytest.raises(SwarmStoreError):
        translate("PRAGMA user_version")
    row = Row(["record", "id"], [{"team_id": "x"}, "task"])
    assert json.loads(row[0]) == {"team_id": "x"}
    assert dict(row)["id"] == "task"


def test_s3_immutable_team_prefix_version_and_body_cleanup():
    fake = FakeS3()
    objects = S3Objects(config(), secrets(), client=fake)
    data = b"verified artifact"
    digest = hashlib.sha256(data).hexdigest()
    team = "a" * 32
    meta = objects.put(team, digest, data)
    assert meta == objects.put(team, digest, data)
    record = dict(team_id=team, object_key=digest, sha256=digest, size_bytes=str(len(data)), **meta)
    assert objects.read(team, record) == data
    assert all(body.closed for body in fake.bodies)
    assert fake.requests[-1]["VersionId"] == "fixture-version"
    assert f"/{team}/objects/" in fake.requests[-1]["Key"]
    with pytest.raises(SwarmAccessError):
        objects.read("b" * 32, record)
    with pytest.raises(SwarmAccessError):
        objects.key(team, "../elsewhere")
    with pytest.raises(SwarmConflictError):
        objects.put(team, digest, b"changed")
    fake.objects[(objects.config.s3_bucket, objects.key(team, digest))] = b"changed"
    with pytest.raises(SwarmStoreError, match="size/hash"):
        objects.read(team, record)


def test_s3_closes_oversized_bodies_without_reading_them():
    fake = FakeS3()
    objects = S3Objects(config(), secrets(), client=fake)
    team, digest = "b" * 32, "c" * 64
    fake.objects[(objects.config.s3_bucket, objects.key(team, digest))] = b"x" * 10_000_001
    with pytest.raises(SwarmStoreError, match="bounded"):
        objects.read(
            team, dict(team_id=team, object_key=digest, sha256=digest, size_bytes="10000001")
        )
    assert fake.bodies[-1].closed


def test_explicit_driver_install_uses_only_fixed_packages_and_hidden_utf8_process(monkeypatch):
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
    from jarvis.swarm.distributed import install

    readiness = iter(
        [
            {"available": False, "can_install": True},
            {"available": True, "can_install": True},
        ]
    )
    calls = []

    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(install, "driver_readiness", lambda: next(readiness))
    monkeypatch.setattr(install.subprocess, "run", run)
    assert install.ensure_dependencies()["available"]
    arguments, options = calls[0]
    assert arguments[-len(install.PACKAGES) :] == list(install.PACKAGES)
    assert "--only-binary=:all:" in arguments
    assert options["creationflags"] == NO_WINDOW_CREATIONFLAGS
    assert options["encoding"] == "utf-8"
    assert "shell" not in options


def test_missing_frozen_drivers_never_launch_app_binary_as_pip(monkeypatch):
    from jarvis.swarm.distributed import install

    monkeypatch.setattr(
        install,
        "driver_readiness",
        lambda: {
            "available": False,
            "can_install": False,
        },
    )

    def forbidden(*args, **kwargs):
        pytest.fail("Frozen application was launched as a Python interpreter")

    monkeypatch.setattr(install.subprocess, "run", forbidden)
    with pytest.raises(SwarmStoreError, match="updated build"):
        install.ensure_dependencies()


def test_postgres_remote_tls_uses_bundled_certificate_roots(monkeypatch):
    from jarvis.swarm.distributed.database import PostgresDatabase

    captured = {}

    class FakePool:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "psycopg_pool", SimpleNamespace(ConnectionPool=FakePool))
    PostgresDatabase(config(), secrets()).pool()
    assert captured["kwargs"]["sslmode"] == "verify-full"
    assert Path(captured["kwargs"]["sslrootcert"]).is_file()
