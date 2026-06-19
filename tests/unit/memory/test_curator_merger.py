"""Unit-Tests fuer `jarvis.memory.curator.merger.Merger`.

Deckt ab:
- User-Facts landen in USER.md (set + Observation-Log).
- Person-Facts legen people/<slug>.md an und schreiben Observations.
- Listen-Felder werden appended + dedupliziert.
- Duplikat-Append → report.skipped inkrementiert (nicht applied).
- Bus-Events `ProfileUpdated` werden nach Persistenz emittiert.
"""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.core.events import ProfileUpdated
from jarvis.memory.curator.extractor import Candidate
from jarvis.memory.curator.merger import Merger
from jarvis.memory.people import PersonStore
from jarvis.memory.user_profile import UserProfile


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _cand(
    subject: str,
    cluster: str,
    field: str,
    value: Any,
    *,
    operation: str = "set",
    confidence: float = 0.9,
    evidence: str = "evidence",
    relationship: str | None = None,
) -> Candidate:
    return Candidate(
        subject=subject,
        cluster=cluster,
        field=field,
        value=value,
        operation=operation,
        confidence=confidence,
        evidence=evidence,
        relationship=relationship,
    )


# ======================================================================
# User-Scalar-Merging
# ======================================================================

class TestUserScalarMerge:
    @pytest.mark.asyncio
    async def test_sets_user_identity_name(self, merger: Merger, profile: UserProfile) -> None:
        cand = _cand("user", "identity", "name", "Ruben")
        report = await merger.apply([cand])
        assert report.applied == 1
        assert report.failed == 0

        # In-Memory gesetzt
        assert profile.get("identity", "name") == "Ruben"
        # Persistenz: frisch laden
        reloaded = UserProfile.load(profile.path)
        assert reloaded.get("identity", "name") == "Ruben"

    @pytest.mark.asyncio
    async def test_also_writes_observation_log_for_scalar_set(
        self, merger: Merger, profile: UserProfile
    ) -> None:
        """Bei jedem Scalar-Set zusaetzlich Observations-Append fuer Audit-Trail."""
        cand = _cand(
            "user", "identity", "name", "Ruben", evidence="User: 'ich bin Ruben'"
        )
        await merger.apply([cand])

        text = profile.path.read_text(encoding="utf-8")
        # Observation steht im Body
        assert "identity.name: Ruben" in text

    @pytest.mark.asyncio
    async def test_same_value_counts_as_skipped(
        self, merger: Merger, profile: UserProfile
    ) -> None:
        """Wenn der Wert schon gesetzt ist, erhoeht der Merger report.skipped."""
        profile.set("identity", "name", "Ruben")
        profile.save()

        cand = _cand("user", "identity", "name", "Ruben")
        report = await merger.apply([cand])

        assert report.skipped == 1
        assert report.applied == 0


# ======================================================================
# User-List-Merging
# ======================================================================

class TestUserListMerge:
    @pytest.mark.asyncio
    async def test_appends_to_list_field(
        self, merger: Merger, profile: UserProfile
    ) -> None:
        cand = _cand(
            "user", "values", "pet_peeves", "confirmation-fatigue",
            operation="append",
        )
        report = await merger.apply([cand])
        assert report.applied == 1

        reloaded = UserProfile.load(profile.path)
        assert "confirmation-fatigue" in (reloaded.get("values", "pet_peeves") or [])

    @pytest.mark.asyncio
    async def test_duplicate_append_increments_skipped(
        self, merger: Merger, profile: UserProfile
    ) -> None:
        """Zweiter identischer Append → skipped erhoeht sich."""
        cand = _cand(
            "user", "values", "pet_peeves", "buzzwords", operation="append"
        )
        r1 = await merger.apply([cand])
        assert r1.applied == 1

        r2 = await merger.apply([cand])
        assert r2.applied == 0
        assert r2.skipped == 1

        reloaded = UserProfile.load(profile.path)
        peeves = reloaded.get("values", "pet_peeves") or []
        # Dedupliziert — nur einmal drin
        assert peeves.count("buzzwords") == 1

    @pytest.mark.asyncio
    async def test_append_humor_types(
        self, merger: Merger, profile: UserProfile
    ) -> None:
        cand = _cand(
            "user", "communication", "humor_types", "dry", operation="append"
        )
        await merger.apply([cand])
        reloaded = UserProfile.load(profile.path)
        assert "dry" in (reloaded.get("communication", "humor_types") or [])


# ======================================================================
# Person-Merging (die Firewall)
# ======================================================================

class TestPersonMerge:
    @pytest.mark.asyncio
    async def test_creates_people_file_for_new_person(
        self,
        merger: Merger,
        person_store: PersonStore,
    ) -> None:
        cand = _cand(
            "person:Laura",
            "identity",
            "profession",
            "Designerin",
            relationship="partner",
        )
        report = await merger.apply([cand])
        assert report.applied == 1
        assert report.failed == 0

        # Datei wurde angelegt
        persons = person_store.list_all()
        assert len(persons) == 1
        laura = persons[0]
        assert laura.name == "Laura"
        assert laura.relationship == "partner"
        # Observation steht in der Person-Datei
        text = laura.path.read_text(encoding="utf-8")
        assert "Designerin" in text

    @pytest.mark.asyncio
    async def test_person_facts_do_not_leak_into_user_profile(
        self,
        merger: Merger,
        profile: UserProfile,
    ) -> None:
        """Kernregel: person:Laura darf USER.md NICHT anfassen."""
        before_meta = dict(profile.meta)

        cand = _cand(
            "person:Laura",
            "identity",
            "profession",
            "Designerin",
            relationship="partner",
        )
        await merger.apply([cand])

        # USER.md-Meta unveraendert (last_updated wuerde bei User-Touch gesetzt)
        reloaded = UserProfile.load(profile.path)
        assert reloaded.get("identity", "name") == before_meta.get("identity", {}).get("name")


# ======================================================================
# Bus-Events
# ======================================================================

class TestBusEvents:
    @pytest.mark.asyncio
    async def test_emits_profile_updated_event_for_user_fact(
        self, merger: Merger, fake_bus
    ) -> None:
        cand = _cand("user", "identity", "name", "Ruben")
        await merger.apply([cand])

        assert len(fake_bus.published) == 1
        evt = fake_bus.published[0]
        assert isinstance(evt, ProfileUpdated)
        assert evt.subject == "user"
        assert evt.cluster == "identity"
        assert evt.field == "name"

    @pytest.mark.asyncio
    async def test_emits_event_with_person_subject(
        self, merger: Merger, fake_bus
    ) -> None:
        cand = _cand(
            "person:Laura", "identity", "profession", "Designerin",
            relationship="partner",
        )
        await merger.apply([cand])

        assert len(fake_bus.published) == 1
        evt = fake_bus.published[0]
        assert isinstance(evt, ProfileUpdated)
        assert evt.subject == "person:Laura"

    @pytest.mark.asyncio
    async def test_no_events_when_nothing_applied(
        self, merger: Merger, fake_bus, profile: UserProfile
    ) -> None:
        """Bei `applied == 0` duerfen keine Events publiziert werden."""
        profile.set("identity", "name", "Ruben")
        profile.save()
        # Candidate setzt den gleichen Wert → skipped, nicht applied
        cand = _cand("user", "identity", "name", "Ruben")
        report = await merger.apply([cand])

        assert report.applied == 0
        assert report.skipped == 1
        assert fake_bus.published == []

    @pytest.mark.asyncio
    async def test_emits_one_event_per_candidate_on_batch(
        self, merger: Merger, fake_bus
    ) -> None:
        cands = [
            _cand("user", "identity", "name", "Ruben"),
            _cand(
                "user", "values", "pet_peeves", "buzzwords", operation="append"
            ),
        ]
        await merger.apply(cands)

        # Pro Candidate ein ProfileUpdated-Event
        assert len(fake_bus.published) == 2
        assert all(isinstance(e, ProfileUpdated) for e in fake_bus.published)


# ======================================================================
# Edge Cases
# ======================================================================

class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_candidates_returns_empty_report(self, merger: Merger) -> None:
        report = await merger.apply([])
        assert report.applied == 0
        assert report.skipped == 0
        assert report.failed == 0

    @pytest.mark.asyncio
    async def test_observation_field_falls_into_markdown(
        self, merger: Merger, profile: UserProfile
    ) -> None:
        """Ein Candidate mit field='observation' landet nur in der Markdown-Section."""
        cand = _cand(
            "user",
            "values",
            "observation",
            "User mag Direktheit",
            evidence="Voice-Turn",
        )
        report = await merger.apply([cand])
        assert report.applied == 1

        text = profile.path.read_text(encoding="utf-8")
        assert "User mag Direktheit" in text
