"""Distributed setup never persists or returns raw credentials."""

import json
import sqlite3

import pytest

from jarvis.swarm.settings import DistributedSettings, DistributedSetup
from tests.fakes.swarm_settings import SecretVault


def setup(**changes):
    return DistributedSetup.model_validate(
        dict(
            {
                "enabled": True,
                "postgres_dsn": "postgresql://worker:synthetic-pg-secret@db.example.test/swarm",
                "redis_url": "rediss://worker:synthetic-redis-secret@cache.example.test/0",
                "s3_endpoint": "https://objects.example.test",
                "s3_region": "auto",
                "s3_bucket": "swarm-results",
                "s3_access_key_id": "synthetic-access-id",
                "s3_secret_access_key": "synthetic-object-secret",
                "max_concurrency": 80,
            },
            **changes,
        )
    )


@pytest.fixture
def settings(tmp_path):
    vault = SecretVault()
    return (
        DistributedSettings(tmp_path, read_secret=vault.read, write_secret=vault.write),
        vault,
    )


def test_reading_unconfigured_setup_creates_no_database(settings):
    store, vault = settings
    assert store.public()["enabled"] is False
    assert store.public()["object_credentials_configured"] is False
    assert not store.path.exists()
    assert not vault.writes


def test_credentials_are_write_only_and_absent_from_sqlite(settings):
    store, vault = settings
    result = store.save(setup())
    record = store.load()
    with sqlite3.connect(store.path) as connection:
        dump = "\n".join(connection.iterdump())
    public = json.dumps(result)
    for secret in (
        "synthetic-pg-secret",
        "synthetic-redis-secret",
        "synthetic-access-id",
        "synthetic-object-secret",
    ):
        assert secret not in dump
        assert secret not in public
        assert secret.encode() not in store.path.read_bytes()
        assert secret in vault.values.values()
    assert record["postgres_dsn"] == "postgresql://worker@db.example.test/swarm"
    assert record["redis_url"] == "rediss://worker@cache.example.test/0"
    assert "postgres_dsn" not in result and "redis_url" not in result
    assert result["object_credentials_configured"] is True
    assert result["max_concurrency"] == 80
    config, secrets = store.resolved()
    assert config.s3_bucket == "swarm-results"
    assert secrets.postgres_password == "synthetic-pg-secret"  # noqa: S105 - synthetic fixture
    assert secrets.redis_password == "synthetic-redis-secret"  # noqa: S105 - synthetic fixture


def test_blank_credential_fields_preserve_existing_credentials(settings):
    store, _ = settings
    store.save(setup())
    before_config, before_secrets = store.resolved()
    result = store.save(
        DistributedSetup(
            postgres_dsn="",
            redis_url="",
            s3_access_key_id="",
            s3_secret_access_key="",
            max_concurrency=12,
        )
    )
    config, secrets = store.resolved()
    assert config == before_config
    assert secrets == before_secrets
    assert result["max_concurrency"] == 12


def test_partial_secret_write_failure_keeps_previous_setup_active(settings):
    store, vault = settings
    store.save(setup())
    before = store.load()
    before_resolved = store.resolved()
    vault.fail_key = "redis_password"
    with pytest.raises(RuntimeError, match="previous setup remains active"):
        store.save(
            setup(
                postgres_dsn="postgresql://worker:replacement-secret@replacement.example.test/swarm",
                max_concurrency=100,
            )
        )
    assert store.load() == before
    assert store.resolved() == before_resolved
    assert any(value == "replacement-secret" for value in vault.values.values())
    assert store.public()["max_concurrency"] == 80


@pytest.mark.parametrize(
    "changes",
    [
        {"postgres_dsn": "postgresql://worker:secret@db.example.test/swarm?sslmode=disable"},
        {"postgres_dsn": "postgresql://worker:secret@db.example.test/swarm?sslmode=require"},
        {"redis_url": "redis://worker:secret@cache.example.test/0"},
        {"s3_endpoint": "http://objects.example.test"},
        {"s3_endpoint": "https://user:secret@objects.example.test"},
        {"s3_endpoint": "https://objects.example.test/path"},
    ],
)
def test_invalid_remote_endpoints_rejected_before_any_secret_write(settings, changes):
    store, vault = settings
    with pytest.raises(ValueError):
        store.save(setup(**changes))
    assert not store.path.exists()
    assert not vault.writes


@pytest.mark.parametrize(
    "changes",
    [
        {"s3_endpoint": "https://user:synthetic-secret@objects.example.test"},
        {"s3_endpoint": "http://objects.example.test"},
        {"postgres_dsn": "postgresql://worker@db.example.test/swarm?password=synthetic-secret"},
        {"redis_url": "redis://worker:synthetic-secret@cache.example.test/0"},
    ],
)
def test_disabled_draft_cannot_persist_unsafe_endpoints_or_embedded_secrets(settings, changes):
    store, vault = settings
    with pytest.raises(ValueError):
        store.save(DistributedSetup.model_validate(dict(changes, enabled=False)))
    assert not store.path.exists()
    assert not vault.writes


def test_disabling_setup_preserves_credentials_for_later_recovery(settings):
    store, _ = settings
    store.save(setup())
    before = store.resolved()
    assert store.save(DistributedSetup(enabled=False))["enabled"] is False
    with pytest.raises(ValueError, match="disabled"):
        store.resolved()
    store.save(DistributedSetup(enabled=True))
    assert store.resolved() == before
