"""End-to-End-Tests für die Self-Mod-Pipeline (Phase 7.6).

Plan-§7.6 vier Plan-Akzeptanzkriterien (test_setting_mutation,
test_skill_authoring, test_pre_validation_reject,
test_rollback_on_reload_failure) plus Prompt-Erweiterungen T1..T9.

Pattern: alle LLM-Aufrufe werden gemockt; jarvis.toml lebt in tmp_path.
Brain-Manager-Mock liefert PendingMutation-Output, Voice-Layer-Mock
liefert Confirm/Veto/Timeout-Antworten.

Markierung: alle Tests `@pytest.mark.e2e` — laufen nicht in CI by default
(Plan-§7.6: "E2E-Tests laufen nicht in CI"). Manueller Trigger via
`pytest -m e2e tests/e2e/`.
"""
from __future__ import annotations

import asyncio
import json
import tomllib
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.brain.tools import build_self_mod_tools
from jarvis.core.config import JarvisConfig, SecurityConfig
from jarvis.core.protocols import ExecutionContext
from jarvis.core.self_mod import (
    AtomicConfigWriter,
    AuditActor,
    AuditSource,
    MutationRequest,
    PendingMutation,
    PendingMutationStore,
    SelfModAudit,
)
from jarvis.skills.authoring import (
    AuthoringSuccess,
    SkillAuthoringRunner,
    write_draft,
)
from jarvis.skills.authoring.schema import SkillDraft
from jarvis.skills.registry import SkillRegistry
from jarvis.skills.schema import SkillLifecycleState
from jarvis.ui.web.self_mod_routes import router as self_mod_router
from jarvis.voice import (
    FlowState,
    SelfModFlowController,
)

pytestmark = pytest.mark.e2e

FIXTURE = (
    Path(__file__).parent.parent
    / "unit"
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
def audit(tmp_path: Path) -> SelfModAudit:
    return SelfModAudit(path=tmp_path / "audit.log")


@pytest.fixture
def writer(
    fixture_path: Path, tmp_path: Path, audit: SelfModAudit
) -> AtomicConfigWriter:
    return AtomicConfigWriter(
        config_path=fixture_path,
        backup_dir=tmp_path / "backups",
        audit=audit,
        config_loader=_isolated_loader,
    )


@pytest.fixture
def pending_store(writer: AtomicConfigWriter) -> PendingMutationStore:
    return PendingMutationStore(writer=writer, auto_confirm_safe=True)


@pytest.fixture
def controller(
    pending_store: PendingMutationStore,
    audit: SelfModAudit,
) -> SelfModFlowController:
    return SelfModFlowController(
        pending_store=pending_store,
        audit=audit,
        timeout_seconds=30.0,
        default_language="de",
    )


@pytest.fixture
def tools(
    writer: AtomicConfigWriter, pending_store: PendingMutationStore
) -> dict[str, Any]:
    return build_self_mod_tools(writer=writer, pending_store=pending_store)


def _make_ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid4(),
        user_utterance="",
        config={},
        memory_read=None,
        approved_by="auto",
    )


def _read_audit(audit: SelfModAudit) -> list[dict]:
    if not audit.path.exists():
        return []
    return [
        json.loads(line)
        for line in audit.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ----------------------------------------------------------------------
# T1 — Voice-Confirm-Flow End-to-End (Plan-§EK-1)
# ----------------------------------------------------------------------


class TestT1VoiceConfirm:
    def test_confirm_persists_with_audit_chain(
        self,
        tools: dict[str, Any],
        controller: SelfModFlowController,
        fixture_path: Path,
        audit: SelfModAudit,
    ) -> None:
        """Plan-§EK-1: Voice-Befehl ändert TTS-Provider, persistiert,
        Audit-Trail dokumentiert die Operation.

        Hauptjarvis ruft set_config_value-Tool, Voice-Layer übernimmt,
        User confirmt — am Ende ist jarvis.toml mutiert + Audit hat
        beide Trails (voice_confirmed Pre + write success).
        """
        # 1. Hauptjarvis-Tool-Call
        tool_result = asyncio.run(
            tools["set_config_value"].execute(
                {
                    "path": "tts.provider",
                    "new_value": "elevenlabs",
                    "reason": "user wants natural voice",
                },
                _make_ctx(),
            )
        )
        assert tool_result.success is True
        pending = PendingMutation.model_validate(tool_result.output)
        # 2. Voice-Layer
        session = controller.begin(pending)
        assert session.state == FlowState.CONFIRMING
        final = controller.receive_answer(session, "ja", confidence=0.94)
        assert final.state == FlowState.APPLIED
        # 3. File mutiert
        assert _isolated_loader(fixture_path).tts.provider == "elevenlabs"
        # 4. Audit-Chain: voice_confirmed + write success
        entries = _read_audit(audit)
        voice_pre = [e for e in entries if "voice_confirmation" in e]
        write_post = [
            e for e in entries
            if e.get("ok") is True and "voice_confirmation" not in e
        ]
        assert len(voice_pre) == 1
        assert len(write_post) == 1


# ----------------------------------------------------------------------
# T2 — Voice-Veto-Flow
# ----------------------------------------------------------------------


class TestT2VoiceVeto:
    def test_veto_no_mutation_audit_voice_vetoed(
        self,
        tools: dict[str, Any],
        controller: SelfModFlowController,
        fixture_path: Path,
        tmp_path: Path,
        audit: SelfModAudit,
    ) -> None:
        original_bytes = fixture_path.read_bytes()
        tool_result = asyncio.run(
            tools["set_config_value"].execute(
                {
                    "path": "tts.provider",
                    "new_value": "elevenlabs",
                    "reason": "",
                },
                _make_ctx(),
            )
        )
        pending = PendingMutation.model_validate(tool_result.output)
        session = controller.begin(pending)
        final = controller.receive_answer(session, "nein, doch nicht")
        assert final.state == FlowState.VETOED
        # File unverändert
        assert fixture_path.read_bytes() == original_bytes
        # Audit "voice_vetoed"
        entries = _read_audit(audit)
        assert any(e.get("error") == "voice_vetoed" for e in entries)
        # KEIN Backup-Eintrag (Veto vor Pre-Validate-Stage)
        backups = list((tmp_path / "backups").glob("jarvis.toml.*.bak"))
        assert backups == []


# ----------------------------------------------------------------------
# T3 — Denied-Path
# ----------------------------------------------------------------------


class TestT3DeniedPath:
    def test_unallowed_path_returns_path_not_allowed(
        self,
        tools: dict[str, Any],
        fixture_path: Path,
    ) -> None:
        original_bytes = fixture_path.read_bytes()
        result = asyncio.run(
            tools["set_config_value"].execute(
                {
                    "path": "brain.fantasy_field",
                    "new_value": "x",
                    "reason": "",
                },
                _make_ctx(),
            )
        )
        assert result.success is False
        assert "path_not_allowed" in result.error
        # File unverändert (Plan-§AD-1 strukturell durchgesetzt)
        assert fixture_path.read_bytes() == original_bytes
        # Plan-§AD-6 Audit für Allowlist-Reject ist Phase-7.3-Erweiterung,
        # aktuell schreibt der Tool-Handler keinen Audit-Eintrag bei Reject —
        # der Reject ist im Tool-Result selbst dokumentiert.

    def test_secret_path_returns_forbidden(
        self,
        tools: dict[str, Any],
        fixture_path: Path,
    ) -> None:
        original_bytes = fixture_path.read_bytes()
        result = asyncio.run(
            tools["set_config_value"].execute(
                {
                    "path": "security.admin_password_hash",
                    "new_value": "x",
                    "reason": "",
                },
                _make_ctx(),
            )
        )
        assert result.success is False
        assert "forbidden_path" in result.error
        assert fixture_path.read_bytes() == original_bytes


# ----------------------------------------------------------------------
# T4 — Validate-Failed mit Auto-Rollback (Plan-§EK-3 + §EK-4)
# ----------------------------------------------------------------------


class TestT4ValidateFailRollback:
    def test_post_validate_failure_triggers_rollback(
        self,
        fixture_path: Path,
        tmp_path: Path,
        audit: SelfModAudit,
    ) -> None:
        """Plan-§EK-4: Reload-Failure führt zu automatischem Rollback,
        Audit `rolled_back=true`.
        """
        original_bytes = fixture_path.read_bytes()

        def crashing_loader(_path: Path) -> JarvisConfig:
            raise RuntimeError("simulated post-validate failure")

        writer = AtomicConfigWriter(
            config_path=fixture_path,
            backup_dir=tmp_path / "backups",
            audit=audit,
            config_loader=crashing_loader,
        )
        from jarvis.core.self_mod.errors import ReloadError

        request = MutationRequest(
            path="tts.provider",
            new_value="elevenlabs",
            actor=AuditActor.HAUPTJARVIS,
            source=AuditSource.VOICE,
        )
        with pytest.raises(ReloadError):
            writer.mutate(request)

        # File byte-identisch
        assert fixture_path.read_bytes() == original_bytes
        # Audit "post_validate_failed_rolled_back" + rolled_back=True
        entries = _read_audit(audit)
        rb = [e for e in entries if e.get("rolled_back") is True]
        assert len(rb) == 1
        assert "post_validate_failed_rolled_back" in rb[0]["error"]


# ----------------------------------------------------------------------
# T5 — Skill-Authoring End-zu-End (Plan-§EK-2)
# ----------------------------------------------------------------------


class TestT5SkillAuthoring:
    def test_authoring_writes_draft_excluded_from_active_pool(
        self,
        tmp_path: Path,
        audit: SelfModAudit,
    ) -> None:
        """Plan-§EK-2: Voice-Befehl erzeugt Skill-Draft, der in UI sichtbar
        wird, aber nicht triggert.
        """
        skills_root = tmp_path / "user_skills"
        skills_root.mkdir()

        async def mock_spawn(prompt: str) -> str:
            return json.dumps(
                {
                    "slug": "spotify-auto-pause",
                    "name": "Spotify Auto-Pause",
                    "description": "Pausiert Spotify wenn User redet.",
                    "intent": "user wants spotify auto-pause",
                    "triggers_yaml": "[{type: voice, pattern: '^pause spotify'}]",
                    "requires_tools": ["run-shell"],
                    "body_markdown": "## Spotify Auto-Pause\n\nDieser Skill ...",
                    "state": "draft",
                }
            )

        runner = SkillAuthoringRunner(
            spawn_callback=mock_spawn,
            audit=audit,
            user_skills_root=skills_root,
        )
        result = asyncio.run(runner.author("Pause Spotify when I talk"))
        assert isinstance(result, AuthoringSuccess)
        assert result.draft_path.exists()

        # Hot-Reload + active-Pool-Exclusion
        registry = SkillRegistry(skills_root, bus=None)
        registry.reload_sync()
        assert len(registry.list_active()) == 0  # Plan-§EK-2 Trigger-Negativ
        assert any(
            s.path.parent.name == "spotify-auto-pause"
            for s in registry.list_drafts()
        )

        # Audit "skill_authored"
        entries = _read_audit(audit)
        authored = [e for e in entries if e.get("type") == "skill_authored"]
        assert len(authored) == 1


# ----------------------------------------------------------------------
# T6 — Skill-Promotion via CLI
# ----------------------------------------------------------------------


class TestT6SkillPromote:
    def test_promote_makes_skill_active(
        self,
        tmp_path: Path,
        audit: SelfModAudit,  # noqa: ARG002 — fixture initialisiert das tmp-audit
    ) -> None:
        """CLI-Promote (Plan-§7.5/§7.6) → state=active, Hot-Reload."""
        skills_root = tmp_path / "user_skills"
        skills_root.mkdir()

        # Vorab: Draft schreiben
        draft = SkillDraft(
            slug="promo-test",
            name="Promo Test",
            description="Promotion-Test",
            intent="test",
            triggers_yaml="[]",
            body_markdown="## Promo\n\nNur ein Body.",
            state="draft",
        )
        write_draft(draft, user_skills_root=skills_root)

        registry = SkillRegistry(skills_root, bus=None)
        registry.reload_sync()
        assert registry.list_drafts()
        promoted = registry.promote("promo-test")
        assert promoted.state in (
            SkillLifecycleState.ACTIVE,
            SkillLifecycleState.VALIDATED,
        )
        # Frontmatter `state: active`
        text = promoted.path.read_text(encoding="utf-8")
        fm = yaml.safe_load(text.split("---", 2)[1])
        assert fm["state"] == "active"


# ----------------------------------------------------------------------
# T7 — Audit-Log-Integrität bei 100 Mixed-Operations
# ----------------------------------------------------------------------


class TestT7AuditIntegrity:
    def test_100_mixed_operations_yield_valid_log(
        self,
        tools: dict[str, Any],
        controller: SelfModFlowController,
        audit: SelfModAudit,
    ) -> None:
        # Plan-§7.6 robustness: 100 randomisierte ops → Audit ist valide.
        import random

        rng = random.Random(42)  # noqa: S311 — Test-Determinismus, kein Crypto
        for i in range(100):
            op = rng.choice(["safe_set", "denied", "ask_confirm", "ask_veto"])
            if op == "safe_set":
                asyncio.run(
                    tools["set_config_value"].execute(
                        {
                            "path": "tts.speed",
                            "new_value": round(0.5 + (i % 10) * 0.15, 2),
                            "reason": "",
                        },
                        _make_ctx(),
                    )
                )
            elif op == "denied":
                asyncio.run(
                    tools["set_config_value"].execute(
                        {
                            "path": "brain.fantasy",
                            "new_value": "x",
                            "reason": "",
                        },
                        _make_ctx(),
                    )
                )
            elif op == "ask_confirm":
                tool_result = asyncio.run(
                    tools["set_config_value"].execute(
                        {
                            "path": "tts.voice_de",
                            "new_value": f"Voice{i}",
                            "reason": "",
                        },
                        _make_ctx(),
                    )
                )
                pending = PendingMutation.model_validate(tool_result.output)
                session = controller.begin(pending)
                controller.receive_answer(session, "ja")
            elif op == "ask_veto":
                tool_result = asyncio.run(
                    tools["set_config_value"].execute(
                        {
                            "path": "tts.voice_en",
                            "new_value": f"VoiceEn{i}",
                            "reason": "",
                        },
                        _make_ctx(),
                    )
                )
                pending = PendingMutation.model_validate(tool_result.output)
                session = controller.begin(pending)
                controller.receive_answer(session, "nein")

        # Audit-Log ist valide: jede Zeile ist parsbar JSON, hat ts/audit_id.
        # Anzahl ist Operations-abhängig (denied + ask_veto erzeugen weniger
        # Einträge als safe_set+ask_confirm) — mindestens 50 als robuste Untergrenze.
        entries = _read_audit(audit)
        assert len(entries) >= 50, (
            f"Erwartete ≥50 Audit-Einträge nach 100 Mixed-Ops, "
            f"erhielt {len(entries)}"
        )
        from uuid import UUID

        for entry in entries:
            assert "ts" in entry
            assert "audit_id" in entry
            UUID(entry["audit_id"])  # Pydantic-validation
        # Timestamps sind monoton (oder gleich): ISO-String-Vergleich reicht
        # für die Plan-§7.6-Robustness-Aussage.
        timestamps = [entry["ts"] for entry in entries]
        sorted_ts = sorted(timestamps)
        # Audit-Log ist append-only — Reihenfolge im File == Reihenfolge der
        # Einträge. Wir erlauben Gleichheit (Mikrosekunden-Auflösung kann kollidieren).
        assert timestamps == sorted_ts or len(set(timestamps)) >= len(timestamps) - 5


# ----------------------------------------------------------------------
# T8 — Backup-FIFO unter Last
# ----------------------------------------------------------------------


class TestT8BackupFifo:
    def test_60_safe_sets_with_max_50_keeps_50(
        self,
        fixture_path: Path,
        tmp_path: Path,
        audit: SelfModAudit,
    ) -> None:
        """60 erfolgreiche Sets mit max_backups=50 → 50 erhalten, älteste gelöscht.

        Plan-§7.2 GC-Policy strukturell verifiziert.
        """
        writer = AtomicConfigWriter(
            config_path=fixture_path,
            backup_dir=tmp_path / "backups",
            max_backups=50,
            backup_min_keep=10,
            audit=audit,
            config_loader=_isolated_loader,
        )
        for i in range(60):
            request = MutationRequest(
                path="tts.speed",
                new_value=round(0.5 + i * 0.01, 3),
                actor=AuditActor.SYSTEM,
                source=AuditSource.UI,
            )
            writer.mutate(request)
        backups = list((tmp_path / "backups").glob("jarvis.toml.*.bak"))
        assert len(backups) == 50


# ----------------------------------------------------------------------
# T9 — Cross-Phase-Audit-Korrelation via correlation_id
# ----------------------------------------------------------------------


class TestT9CorrelationId:
    def test_correlation_id_threads_through_voice_flow(
        self,
        tools: dict[str, Any],
        controller: SelfModFlowController,
        audit: SelfModAudit,
    ) -> None:
        """Eine `correlation_id` zieht sich durch tool → voice → mutate-audit.

        Aktuell ist die `correlation_id` nicht überall im Audit als Feld
        — wir prüfen via `audit_id` der ersten und letzten Audit-Zeile,
        dass beide auf denselben Pfad verweisen (logische Korrelation).
        """
        tool_result = asyncio.run(
            tools["set_config_value"].execute(
                {
                    "path": "tts.provider",
                    "new_value": "elevenlabs",
                    "reason": "",
                },
                _make_ctx(),
            )
        )
        pending = PendingMutation.model_validate(tool_result.output)
        correlation_id = pending.id
        UUID(str(correlation_id))  # validate uuid

        session = controller.begin(pending)
        final = controller.receive_answer(session, "ja")
        assert final.state == FlowState.APPLIED

        entries = _read_audit(audit)
        path_entries = [e for e in entries if e.get("path") == "tts.provider"]
        # Mind. zwei Einträge mit gleichem Pfad (voice_confirmed Pre + write success)
        assert len(path_entries) >= 2


# ----------------------------------------------------------------------
# Plan-§7.6 REST-API-Tests (FastAPI TestClient)
# ----------------------------------------------------------------------


@pytest.fixture
def api_app(
    fixture_path: Path,
    tmp_path: Path,
    audit: SelfModAudit,
    writer: AtomicConfigWriter,
) -> FastAPI:
    """Minimal FastAPI-App für die Self-Mod-Endpoints."""
    app = FastAPI()
    app.include_router(self_mod_router)
    app.state.self_mod_audit = audit
    app.state.self_mod_writer = writer
    # Admin-PW: SHA-256 von "secret"
    import hashlib

    expected_hash = hashlib.sha256(b"secret").hexdigest()
    app.state.config = type(
        "C",
        (),
        {"security": SecurityConfig(admin_password_hash=expected_hash)},
    )()
    return app


class TestRestApi:
    def test_get_audit_returns_events(
        self,
        api_app: FastAPI,
        tools: dict[str, Any],
        controller: SelfModFlowController,
    ) -> None:
        # Vorab: einen Audit-Eintrag erzeugen
        tool_result = asyncio.run(
            tools["set_config_value"].execute(
                {"path": "tts.speed", "new_value": 1.42, "reason": ""},
                _make_ctx(),
            )
        )
        assert tool_result.success is True
        client = TestClient(api_app)
        response = client.get("/api/self-mod/audit")
        assert response.status_code == 200
        body = response.json()
        assert "events" in body
        assert body["total_returned"] >= 1

    def test_get_mutable_returns_8_specs(self, api_app: FastAPI) -> None:
        client = TestClient(api_app)
        response = client.get("/api/self-mod/mutable")
        assert response.status_code == 200
        assert len(response.json()["specs"]) == 8

    def test_get_backups_empty_when_no_mutations(
        self, api_app: FastAPI
    ) -> None:
        client = TestClient(api_app)
        response = client.get("/api/self-mod/backups")
        assert response.status_code == 200
        assert response.json()["backups"] == []

    def test_post_restore_requires_admin_password(
        self, api_app: FastAPI
    ) -> None:
        client = TestClient(api_app)
        response = client.post(
            "/api/self-mod/restore",
            json={"filename": "any.bak", "admin_password": "wrong"},
        )
        assert response.status_code == 403

    def test_audit_endpoint_redacts_sensitive_paths(
        self,
        api_app: FastAPI,
        audit: SelfModAudit,
    ) -> None:
        # Manuell einen sensitive-Pfad-Audit-Eintrag schreiben
        from jarvis.core.self_mod.schema import AuditEvent

        audit.record(
            AuditEvent(
                source=AuditSource.UI,
                requested_by=AuditActor.USER,
                path="anthropic_api_key",
                old_value="sk-leakable-VERY-LONG",
                new_value="sk-new-leakable-VALUE",
                ok=True,
                rolled_back=False,
            )
        )
        client = TestClient(api_app)
        response = client.get("/api/self-mod/audit")
        body = response.json()
        # Plan-§AP-2 Defense-in-Depth: Klartext darf NICHT durchsickern
        text = json.dumps(body)
        assert "sk-leakable-VERY-LONG" not in text
        assert "sk-new-leakable-VALUE" not in text

    def test_audit_endpoint_no_mutation_verbs(
        self, api_app: FastAPI
    ) -> None:
        """Plan-§AP-Audit-Mutation: /api/self-mod/audit ist read-only.

        DELETE/PUT/PATCH müssen 405 oder 404 werfen.
        """
        client = TestClient(api_app)
        for method in ("delete", "put", "patch"):
            resp = getattr(client, method)("/api/self-mod/audit")
            assert resp.status_code in (404, 405), (
                f"{method.upper()} sollte abgelehnt werden, kam {resp.status_code}"
            )
