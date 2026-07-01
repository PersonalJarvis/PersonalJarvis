"""Tests für `jarvis.voice.self_mod_flow.SelfModFlowController` (Phase 7.4).

Plan-Akzeptanzkriterien §7.4:
- Misshear-Test: User-Reject führt zu keinem Schreiben
- Timeout-Test: 30s ohne Antwort → automatischer Reject
- SAFE-Tier-Test: `tts.speed`-Mutation läuft ohne Echo durch
- Sprachdetection: Templates passen sich an `profile.language` an

Plus Prompt-AC:
- Confirm → APPLIED, set_config_value genau einmal, Audit hat
  voice_confirmation
- Veto → VETOED, set_config_value NICHT gerufen, Audit "voice_vetoed"
- Mehrdeutige Antwort verlängert NICHT den Timeout (kein Soft-Lock)
- Integrationstest: Hauptjarvis-Tool-Call → Voice-Layer-Confirm →
  jarvis.toml mutiert
"""
from __future__ import annotations

import asyncio
import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

from jarvis.brain.tools import build_self_mod_tools
from jarvis.core.config import JarvisConfig
from jarvis.core.protocols import ExecutionContext
from jarvis.core.self_mod import (
    AtomicConfigWriter,
    PendingMutation,
    PendingMutationStore,
    SelfModAudit,
)
from jarvis.voice import (
    FlowState,
    SelfModFlowController,
)

FIXTURE = (
    Path(__file__).parent.parent
    / "self_mod"
    / "fixtures"
    / "minimal_jarvis.toml"
)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


def _isolated_loader(path: Path) -> JarvisConfig:
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
def audit_log(tmp_path: Path) -> SelfModAudit:
    return SelfModAudit(path=tmp_path / "audit.log")


@pytest.fixture
def writer(
    fixture_path: Path, tmp_path: Path, audit_log: SelfModAudit
) -> AtomicConfigWriter:
    return AtomicConfigWriter(
        config_path=fixture_path,
        backup_dir=tmp_path / "backups",
        audit=audit_log,
        config_loader=_isolated_loader,
    )


@pytest.fixture
def pending_store(writer: AtomicConfigWriter) -> PendingMutationStore:
    return PendingMutationStore(writer=writer, auto_confirm_safe=True)


@pytest.fixture
def controller(
    pending_store: PendingMutationStore,
    audit_log: SelfModAudit,
) -> SelfModFlowController:
    return SelfModFlowController(
        pending_store=pending_store,
        audit=audit_log,
        timeout_seconds=30.0,
        default_language="de",
    )


def _make_pending_via_tool(
    pending_store: PendingMutationStore,
    *,
    path: str,
    new_value: Any,
) -> PendingMutation:
    """Hilfsfunktion: legt eine Pending-Mutation via Public-API an."""
    from jarvis.core.self_mod import AuditActor, AuditSource, MutationRequest

    request = MutationRequest(
        path=path,
        new_value=new_value,
        actor=AuditActor.HAUPTJARVIS,
        source=AuditSource.VOICE,
    )
    return pending_store.create(request)


def _read_audit(audit: SelfModAudit) -> list[dict]:
    if not audit.path.exists():
        return []
    return [
        json.loads(line)
        for line in audit.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ----------------------------------------------------------------------
# begin() — SAFE-Tier-Auto-Apply
# ----------------------------------------------------------------------


class TestBeginSafeTier:
    def test_safe_tier_starts_in_applied(
        self,
        controller: SelfModFlowController,
        pending_store: PendingMutationStore,
        fixture_path: Path,
    ) -> None:
        """Plan-§7.4 SAFE-Tier-Path: kein Echo, sofort persistiert."""
        pending = _make_pending_via_tool(
            pending_store, path="tts.speed", new_value=1.25
        )
        assert pending.applied is True  # SAFE-Tier vom Store auto-confirmt
        session = controller.begin(pending)
        assert session.state == FlowState.APPLIED
        assert session.echo_question == ""
        assert session.final_message is not None
        assert "1.25" in session.final_message
        # File wurde wirklich geschrieben (vom Store, vor begin())
        assert _isolated_loader(fixture_path).tts.speed == 1.25


# ----------------------------------------------------------------------
# begin() — ASK-Tier
# ----------------------------------------------------------------------


class TestBeginAskTier:
    def test_ask_tier_starts_in_confirming(
        self,
        controller: SelfModFlowController,
        pending_store: PendingMutationStore,
    ) -> None:
        pending = _make_pending_via_tool(
            pending_store, path="tts.provider", new_value="elevenlabs"
        )
        assert pending.applied is False
        session = controller.begin(pending)
        assert session.state == FlowState.CONFIRMING
        assert "Verstanden" in session.echo_question
        assert "elevenlabs" in session.echo_question

    def test_ask_tier_deadline_set(
        self,
        controller: SelfModFlowController,
        pending_store: PendingMutationStore,
    ) -> None:
        pending = _make_pending_via_tool(
            pending_store, path="tts.provider", new_value="elevenlabs"
        )
        session = controller.begin(pending, now=1000.0)
        assert session.deadline_ts == 1030.0  # 1000 + 30s


# ----------------------------------------------------------------------
# receive_answer — Confirm → APPLIED
# ----------------------------------------------------------------------


class TestConfirmFlow:
    def test_confirm_persists_value(
        self,
        controller: SelfModFlowController,
        pending_store: PendingMutationStore,
        fixture_path: Path,
        audit_log: SelfModAudit,
    ) -> None:
        pending = _make_pending_via_tool(
            pending_store, path="tts.provider", new_value="elevenlabs"
        )
        session = controller.begin(pending)
        final = controller.receive_answer(
            session, "ja, mach das", confidence=0.92
        )
        assert final.state == FlowState.APPLIED
        assert _isolated_loader(fixture_path).tts.provider == "elevenlabs"
        # Audit hat voice_confirmed-Pre-Event mit voice_confirmation-Feld
        entries = _read_audit(audit_log)
        voice_entries = [
            e for e in entries if e.get("ok") is True and "voice_confirmation" in e
        ]
        assert len(voice_entries) == 1
        vc = voice_entries[0]["voice_confirmation"]
        assert vc["transcript"] == "ja, mach das"
        assert vc["confidence"] == 0.92
        assert vc["timestamp_utc"].endswith("Z")

    def test_confirm_renders_success_message(
        self,
        controller: SelfModFlowController,
        pending_store: PendingMutationStore,
    ) -> None:
        pending = _make_pending_via_tool(
            pending_store, path="tts.provider", new_value="elevenlabs"
        )
        session = controller.begin(pending)
        final = controller.receive_answer(session, "ja")
        assert final.final_message is not None
        assert "Erledigt" in final.final_message
        assert "elevenlabs" in final.final_message

    def test_confirm_with_restart_path(
        self,
        controller: SelfModFlowController,
        pending_store: PendingMutationStore,
    ) -> None:
        # brain.primary is locked for non-USER actors (ProviderSwitchLockedError).
        # Use stt.provider instead — also has needs_restart=True per overrides.py
        # but is NOT in the provider lock list, so HAUPTJARVIS may change it.
        pending = _make_pending_via_tool(
            pending_store, path="stt.provider", new_value="openai-whisper"
        )
        session = controller.begin(pending)
        final = controller.receive_answer(session, "ja")
        # stt.provider hat needs_restart=True
        assert final.state == FlowState.APPLIED
        assert final.final_message is not None
        assert "neustarten" in final.final_message


# ----------------------------------------------------------------------
# receive_answer — Veto → VETOED
# ----------------------------------------------------------------------


class TestVetoFlow:
    def test_veto_does_not_persist(
        self,
        controller: SelfModFlowController,
        pending_store: PendingMutationStore,
        fixture_path: Path,
    ) -> None:
        original_bytes = fixture_path.read_bytes()
        pending = _make_pending_via_tool(
            pending_store, path="tts.provider", new_value="elevenlabs"
        )
        session = controller.begin(pending)
        final = controller.receive_answer(session, "nein, doch nicht")
        assert final.state == FlowState.VETOED
        # File unverändert
        assert fixture_path.read_bytes() == original_bytes

    def test_veto_writes_audit_voice_vetoed(
        self,
        controller: SelfModFlowController,
        pending_store: PendingMutationStore,
        audit_log: SelfModAudit,
    ) -> None:
        pending = _make_pending_via_tool(
            pending_store, path="tts.provider", new_value="elevenlabs"
        )
        session = controller.begin(pending)
        controller.receive_answer(session, "abbrechen", confidence=0.85)
        entries = _read_audit(audit_log)
        vetoed = [e for e in entries if e.get("error") == "voice_vetoed"]
        assert len(vetoed) == 1
        assert vetoed[0]["voice_confirmation"]["transcript"] == "abbrechen"
        assert vetoed[0]["voice_confirmation"]["confidence"] == 0.85

    def test_veto_renders_short_message(
        self,
        controller: SelfModFlowController,
        pending_store: PendingMutationStore,
    ) -> None:
        pending = _make_pending_via_tool(
            pending_store, path="tts.provider", new_value="elevenlabs"
        )
        session = controller.begin(pending)
        final = controller.receive_answer(session, "stopp")
        assert final.final_message == "Okay, lass ich."


# ----------------------------------------------------------------------
# Misshear-Test (Plan-§7.4-AC)
# ----------------------------------------------------------------------


class TestMisshearReject:
    def test_misheard_value_user_rejects_no_write(
        self,
        controller: SelfModFlowController,
        pending_store: PendingMutationStore,
        fixture_path: Path,
        audit_log: SelfModAudit,
    ) -> None:
        """Plan-AC: User hört Echo „Karen" statt „Charon" → Reject → kein Schreiben.

        Wir simulieren: STT hat „Karen" als new_value transkribiert, der Echo-Satz
        zeigt das, der User hört es und sagt „nein".
        """
        original_bytes = fixture_path.read_bytes()
        pending = _make_pending_via_tool(
            pending_store, path="tts.voice_de", new_value="Karen"
        )
        session = controller.begin(pending)
        # Echo zeigt "Karen" (genau das, was STT verstanden hat — End-Focus
        # macht das User-sichtbar):
        assert "Karen" in session.echo_question
        # User hört es und sagt nein
        final = controller.receive_answer(session, "nein, falsch verstanden")
        assert final.state == FlowState.VETOED
        # File unverändert — User wurde durch Echo gerettet
        assert fixture_path.read_bytes() == original_bytes


# ----------------------------------------------------------------------
# Timeout (Plan-§7.4-AC)
# ----------------------------------------------------------------------


class TestTimeoutFlow:
    def test_timeout_after_30s_no_answer(
        self,
        controller: SelfModFlowController,
        pending_store: PendingMutationStore,
        fixture_path: Path,
        audit_log: SelfModAudit,
    ) -> None:
        original_bytes = fixture_path.read_bytes()
        pending = _make_pending_via_tool(
            pending_store, path="tts.provider", new_value="elevenlabs"
        )
        session = controller.begin(pending, now=1000.0)
        final = controller.check_timeout(session, now=1031.0)  # 31s später
        assert final.state == FlowState.TIMEOUT
        assert fixture_path.read_bytes() == original_bytes
        # Audit "voice_timeout"
        entries = _read_audit(audit_log)
        assert any(e.get("error") == "voice_timeout" for e in entries)

    def test_check_timeout_within_window_keeps_confirming(
        self,
        controller: SelfModFlowController,
        pending_store: PendingMutationStore,
    ) -> None:
        pending = _make_pending_via_tool(
            pending_store, path="tts.provider", new_value="elevenlabs"
        )
        session = controller.begin(pending, now=1000.0)
        still_open = controller.check_timeout(session, now=1015.0)  # 15s
        assert still_open.state == FlowState.CONFIRMING

    def test_receive_answer_after_timeout_window_returns_timeout(
        self,
        controller: SelfModFlowController,
        pending_store: PendingMutationStore,
    ) -> None:
        pending = _make_pending_via_tool(
            pending_store, path="tts.provider", new_value="elevenlabs"
        )
        session = controller.begin(pending, now=1000.0)
        # User antwortet zu spät — wird als Timeout behandelt
        final = controller.receive_answer(session, "ja", now=1031.0)
        assert final.state == FlowState.TIMEOUT


# ----------------------------------------------------------------------
# Mehrdeutigkeit (kein Soft-Lock, kein Confirm-Bias)
# ----------------------------------------------------------------------


class TestAmbiguousNoSoftLock:
    def test_ambiguous_keeps_confirming_without_extending_deadline(
        self,
        controller: SelfModFlowController,
        pending_store: PendingMutationStore,
    ) -> None:
        """Plan-AP-12 + Plan-Sicherheits-Eigenschaft: Mehrdeutiges
        verlängert NICHT die Deadline, blockiert aber den Confirm.
        """
        pending = _make_pending_via_tool(
            pending_store, path="tts.provider", new_value="elevenlabs"
        )
        session = controller.begin(pending, now=1000.0)
        # User sagt "vielleicht"
        intermediate = controller.receive_answer(
            session, "vielleicht", now=1010.0
        )
        # Bleibt in CONFIRMING, Deadline UNVERÄNDERT
        assert intermediate.state == FlowState.CONFIRMING
        assert intermediate.deadline_ts == 1030.0  # nicht verlängert
        # Dann läuft Timer ab
        final = controller.check_timeout(intermediate, now=1031.0)
        assert final.state == FlowState.TIMEOUT


# ----------------------------------------------------------------------
# Integration: Tool-Call → Flow → Mutate
# ----------------------------------------------------------------------


class TestIntegrationToolCallToMutation:
    def test_tool_call_then_voice_confirm_persists(
        self,
        fixture_path: Path,
        tmp_path: Path,
        audit_log: SelfModAudit,
    ) -> None:
        """Hauptjarvis-Tool-Call → Voice-Layer-Confirm → File mutiert."""
        # Setup wie ein realistischer Run
        writer = AtomicConfigWriter(
            config_path=fixture_path,
            backup_dir=tmp_path / "backups",
            audit=audit_log,
            config_loader=_isolated_loader,
        )
        pending_store = PendingMutationStore(
            writer=writer, auto_confirm_safe=True
        )
        tools = build_self_mod_tools(
            writer=writer, pending_store=pending_store
        )
        controller = SelfModFlowController(
            pending_store=pending_store,
            audit=audit_log,
            timeout_seconds=30.0,
        )

        # 1. Hauptjarvis ruft set_config_value-Tool
        from uuid import uuid4

        ctx = ExecutionContext(
            trace_id=uuid4(),
            user_utterance="wechsle TTS auf elevenlabs",
            config={},
            memory_read=None,
            approved_by="auto",
        )
        tool_result = asyncio.run(
            tools["set_config_value"].execute(
                {
                    "path": "tts.provider",
                    "new_value": "elevenlabs",
                    "reason": "user wants more natural voice",
                },
                ctx,
            )
        )
        assert tool_result.success is True
        # Re-Konstruktion des PendingMutation-Modells aus dem Tool-Output
        pending = PendingMutation.model_validate(tool_result.output)
        assert pending.applied is False  # ASK-Tier

        # 2. Voice-Layer übernimmt die Echo-Confirmation
        session = controller.begin(pending)
        assert session.state == FlowState.CONFIRMING
        assert "elevenlabs" in session.echo_question

        # 3. User bestätigt via STT-Mock
        final = controller.receive_answer(session, "ja", confidence=0.95)
        assert final.state == FlowState.APPLIED

        # 4. jarvis.toml ist mutiert
        assert _isolated_loader(fixture_path).tts.provider == "elevenlabs"

        # 5. Audit-Trail enthält voice_confirmed (Pre) + ok=true (Mutate)
        entries = _read_audit(audit_log)
        voice_pre = [
            e
            for e in entries
            if e.get("ok") is True and "voice_confirmation" in e
        ]
        write_post = [
            e
            for e in entries
            if e.get("ok") is True
            and "voice_confirmation" not in e
        ]
        assert len(voice_pre) >= 1
        assert len(write_post) >= 1
        assert voice_pre[0]["voice_confirmation"]["transcript"] == "ja"


# ----------------------------------------------------------------------
# Misc — Terminal-States + Defense
# ----------------------------------------------------------------------


class TestTerminalStateGuards:
    def test_receive_answer_in_terminal_state_raises(
        self,
        controller: SelfModFlowController,
        pending_store: PendingMutationStore,
    ) -> None:
        pending = _make_pending_via_tool(
            pending_store, path="tts.provider", new_value="elevenlabs"
        )
        session = controller.begin(pending)
        final = controller.receive_answer(session, "ja")
        with pytest.raises(ValueError):
            controller.receive_answer(final, "ja")  # bereits APPLIED

    def test_check_timeout_on_terminal_returns_unchanged(
        self,
        controller: SelfModFlowController,
        pending_store: PendingMutationStore,
    ) -> None:
        pending = _make_pending_via_tool(
            pending_store, path="tts.provider", new_value="elevenlabs"
        )
        session = controller.begin(pending)
        vetoed = controller.receive_answer(session, "nein")
        same = controller.check_timeout(vetoed, now=99999999.0)
        assert same is vetoed  # frozen → identische Instanz
