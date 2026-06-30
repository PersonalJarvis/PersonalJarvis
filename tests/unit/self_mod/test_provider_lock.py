"""Provider-selection lock — Jarvis may not switch its own brain provider.

The active brain provider (and the other brain provider-selection keys) is the
user's HARD choice. It changes ONLY through an explicit user action — the
control CLI or the manual provider switch in the desktop app (``actor=USER``) —
never through Jarvis itself (voice/chat self-mod, ``actor != USER``) or any
automatic mechanism.

This guards the self-mod writer, the convergence point of every self-mod /
Control-API mutation, so the lock holds regardless of which tool initiates the
write. The dedicated ``config_writer.set_brain_primary`` path used by the UI
button + ``jarvis brain switch`` does NOT flow through here and stays allowed.
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from jarvis.core.config import JarvisConfig
from jarvis.core.self_mod import (
    AtomicConfigWriter,
    AuditActor,
    AuditSource,
    MutationRequest,
    ProviderSwitchLockedError,
    SelfModAudit,
)
from jarvis.core.self_mod.provider_lock import (
    PROVIDER_LOCK_PATHS,
    is_provider_lock_path,
)

FIXTURE = Path(__file__).parent / "fixtures" / "minimal_jarvis.toml"


def _isolated_loader(path: Path) -> JarvisConfig:
    """Loader without JARVIS__* ENV overrides (test isolation)."""
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return JarvisConfig.model_validate(tomllib.loads(raw.decode("utf-8")))


@pytest.fixture
def fixture_path(tmp_path: Path) -> Path:
    target = tmp_path / "jarvis.toml"
    target.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    return target


@pytest.fixture
def writer(fixture_path: Path, tmp_path: Path) -> AtomicConfigWriter:
    return AtomicConfigWriter(
        config_path=fixture_path,
        backup_dir=tmp_path / "backups",
        audit=SelfModAudit(path=tmp_path / "audit.log"),
        config_loader=_isolated_loader,
    )


def _req(path: str, value: object, actor: AuditActor) -> MutationRequest:
    return MutationRequest(
        path=path, new_value=value, actor=actor, source=AuditSource.VOICE
    )


# ----------------------------------------------------------------------
# Policy — which paths are user-only
# ----------------------------------------------------------------------


class TestPolicy:
    def test_brain_provider_selection_keys_are_locked(self) -> None:
        for path in (
            "brain.primary",
            "brain.fallback",
            "brain.deep_brain",
            "brain.routing_provider",
            "brain.router.provider",
            "brain.router.fallback_provider",
        ):
            assert is_provider_lock_path(path) is True, path

    def test_locked_paths_constant_matches_helper(self) -> None:
        for path in PROVIDER_LOCK_PATHS:
            assert is_provider_lock_path(path) is True, path

    def test_unrelated_keys_are_not_locked(self) -> None:
        # tts/stt provider switches, the per-provider MODEL picker, and other
        # settings stay freely self-mutable — the lock is brain-provider only.
        for path in (
            "tts.provider",
            "stt.provider",
            "brain.providers.gemini.model",
            "brain.reply_language",
            "tts.speed",
        ):
            assert is_provider_lock_path(path) is False, path


# ----------------------------------------------------------------------
# Enforcement — the self-mod writer refuses a non-USER provider switch
# ----------------------------------------------------------------------


class TestWriterEnforcement:
    def test_hauptjarvis_cannot_switch_brain_primary(
        self, writer: AtomicConfigWriter, fixture_path: Path
    ) -> None:
        with pytest.raises(ProviderSwitchLockedError):
            writer.mutate(_req("brain.primary", "gemini", AuditActor.HAUPTJARVIS))
        # The file must be untouched — no write, no drift.
        assert _isolated_loader(fixture_path).brain.primary == "claude-api"

    def test_user_can_switch_brain_primary(
        self, writer: AtomicConfigWriter, fixture_path: Path
    ) -> None:
        # The CLI / manual UI path (actor=USER) is the sanctioned channel and
        # must still go through.
        result = writer.mutate(_req("brain.primary", "gemini", AuditActor.USER))
        assert result.ok is True
        assert _isolated_loader(fixture_path).brain.primary == "gemini"

    def test_hauptjarvis_can_still_switch_unrelated_setting(
        self, writer: AtomicConfigWriter, fixture_path: Path
    ) -> None:
        # The lock is narrow: Jarvis keeps full voice control over everything
        # else (here: the TTS provider).
        result = writer.mutate(_req("tts.provider", "elevenlabs", AuditActor.HAUPTJARVIS))
        assert result.ok is True
        assert _isolated_loader(fixture_path).tts.provider == "elevenlabs"

    def test_refused_switch_is_audited(
        self, writer: AtomicConfigWriter, tmp_path: Path
    ) -> None:
        with pytest.raises(ProviderSwitchLockedError):
            writer.mutate(_req("brain.primary", "gemini", AuditActor.HAUPTJARVIS))
        entries = [
            json.loads(line)
            for line in (tmp_path / "audit.log").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert any(
            e["path"] == "brain.primary"
            and e["ok"] is False
            and "provider_switch_locked" in e["error"]
            for e in entries
        )


# ----------------------------------------------------------------------
# The voice/chat self-mod config tool turns the lock into an honest message
# ----------------------------------------------------------------------


class TestSelfModConfigTool:
    async def test_brain_primary_is_refused_honestly(
        self, writer: AtomicConfigWriter, fixture_path: Path
    ) -> None:
        from jarvis.brain.tools.self_mod_tools import SetConfigValueTool
        from jarvis.core.self_mod import PendingMutationStore

        store = PendingMutationStore(writer=writer, auto_apply="all")
        tool = SetConfigValueTool(pending_store=store)  # default actor=HAUPTJARVIS

        result = await tool.execute(
            {"path": "brain.primary", "new_value": "gemini", "reason": "x"}, None
        )

        assert result.success is False
        assert "provider_switch_locked" in (result.error or "")
        # The provider must be untouched.
        assert _isolated_loader(fixture_path).brain.primary == "claude-api"
