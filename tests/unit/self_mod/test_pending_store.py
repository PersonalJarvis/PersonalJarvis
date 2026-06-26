"""Tests for the PendingMutationStore auto-apply policy (Wave 1.3).

The voice path applies every change immediately ("never ask, always now"), while
REST/CLI keep the SAFE-auto / ASK-confirm split. The store carries the policy:
``auto_apply="all"`` applies any non-forbidden tier at once; the default
``"safe_only"`` preserves today's behaviour.
"""
from __future__ import annotations

from pathlib import Path

import tomllib

import pytest

from jarvis.core.config import JarvisConfig
from jarvis.core.self_mod import (
    AtomicConfigWriter,
    PendingMutationStore,
    SecretAccessError,
    SelfModAudit,
)
from jarvis.core.self_mod.schema import MutationRequest

FIXTURE = Path(__file__).parent / "fixtures" / "minimal_jarvis.toml"


def _isolated_loader(path: Path) -> JarvisConfig:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return JarvisConfig.model_validate(tomllib.loads(raw.decode("utf-8")))


@pytest.fixture
def writer(tmp_path: Path) -> AtomicConfigWriter:
    target = tmp_path / "jarvis.toml"
    target.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    return AtomicConfigWriter(
        config_path=target,
        backup_dir=tmp_path / "backups",
        audit=SelfModAudit(path=tmp_path / "audit.log"),
        config_loader=_isolated_loader,
    )


def _req(path: str, value: object) -> MutationRequest:
    return MutationRequest(path=path, new_value=value, reason="test")


class TestAutoApplyAll:
    def test_applies_ask_tier_immediately(self, writer: AtomicConfigWriter) -> None:
        # tts.provider is ASK-tier; under "all" it applies without confirmation.
        store = PendingMutationStore(writer=writer, auto_apply="all")
        pending = store.create(_req("tts.provider", "elevenlabs"))
        assert pending.applied is True
        assert pending.needs_confirmation is False
        assert len(store) == 0  # nothing parked in the pending bucket

    def test_still_refuses_forbidden(self, writer: AtomicConfigWriter) -> None:
        store = PendingMutationStore(writer=writer, auto_apply="all")
        with pytest.raises(SecretAccessError):
            store.create(_req("security.admin_password_hash", "x"))


class TestDefaultSafeOnly:
    def test_defers_ask_tier(self, writer: AtomicConfigWriter) -> None:
        # The default policy keeps ASK-tier in the bucket for confirmation.
        store = PendingMutationStore(writer=writer)
        pending = store.create(_req("tts.provider", "elevenlabs"))
        assert pending.applied is False
        assert pending.needs_confirmation is True
        assert len(store) == 1

    def test_applies_safe_tier(self, writer: AtomicConfigWriter) -> None:
        store = PendingMutationStore(writer=writer)
        pending = store.create(_req("tts.speed", 1.25))
        assert pending.applied is True
        assert pending.needs_confirmation is False
