"""Merger — writes validated candidates to the correct file.

Routing:
- `subject == "user"` → UserProfile (USER.md)
- `subject.startswith("person:")` → PersonStore.get_or_create(name)

Operations:
- `set`  + structured field → UserProfile.set(cluster, field, value)
- `append` + list field     → UserProfile.append_list(cluster, field, value)
- `observation` or unknown field → append to ## Observations

A `ProfileUpdated` event is emitted after every successful merge so that
the UI can show "3 new facts learned".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..people import PersonStore
from ..user_profile import UserProfile
from .extractor import Candidate

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus

log = logging.getLogger(__name__)


LIST_FIELDS = {
    ("identity", "languages"),
    ("identity", "devices"),
    ("communication", "humor_types"),
    ("values", "top_values"),
    ("values", "pet_peeves"),
    ("values", "motivations"),
}


@dataclass
class MergeReport:
    applied: int = 0
    skipped: int = 0
    failed: int = 0
    details: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.details is None:
            self.details = []


class Merger:
    """Writes validated candidates to USER.md / people/*.md."""

    def __init__(
        self,
        profile: UserProfile,
        people: PersonStore,
        bus: EventBus | None = None,
    ) -> None:
        self._profile = profile
        self._people = people
        self._bus = bus

    async def apply(self, candidates: list[Candidate]) -> MergeReport:
        report = MergeReport()
        if not candidates:
            return report

        # Collect candidates per file and write them in a batch so that
        # each file is saved only once (reduces file I/O).
        user_touched = False
        people_touched: dict[str, Any] = {}  # {slug: Person}

        for cand in candidates:
            try:
                if cand.is_person:
                    person = self._people.get_or_create(
                        name=cand.person_name or "Unknown",
                        relationship=cand.relationship or "unbekannt",
                    )
                    self._apply_to_person(person, cand)
                    people_touched[person.path.stem] = person
                    report.applied += 1
                    report.details.append(f"person:{person.name}.{cand.field}")
                else:
                    changed = self._apply_to_user(cand)
                    if changed:
                        user_touched = True
                        report.applied += 1
                        report.details.append(f"user.{cand.cluster}.{cand.field}")
                    else:
                        report.skipped += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("Merge failed for %s: %s", cand, exc)
                report.failed += 1

        # Persist
        if user_touched:
            try:
                self._profile.save()
            except Exception as exc:  # noqa: BLE001
                log.error("USER.md save failed: %s", exc)
                report.failed += 1

        for person in people_touched.values():
            try:
                person.save()
            except Exception as exc:  # noqa: BLE001
                log.error("Person save failed (%s): %s", person.name, exc)
                report.failed += 1

        # Emit events (after persistence — we do not want events for
        # aborted writes)
        if self._bus is not None and report.applied > 0:
            from jarvis.core.events import ProfileUpdated  # lazy import
            for cand in candidates:
                if cand.is_person:
                    subj = f"person:{cand.person_name}"
                else:
                    subj = "user"
                await self._bus.publish(ProfileUpdated(
                    subject=subj,
                    cluster=cand.cluster,
                    field=cand.field,
                    operation=cand.operation,
                    confidence=cand.confidence,
                    evidence=cand.evidence[:200],
                ))

        return report

    # ------------------------------------------------------------------
    # User
    # ------------------------------------------------------------------

    def _apply_to_user(self, cand: Candidate) -> bool:
        """Writes a candidate into the in-memory UserProfile. Returns True if changed."""
        if cand.field == "observation" or cand.cluster not in {
            "identity", "communication", "work_style", "values", "relationship"
        }:
            # Free observation → append to the Markdown section
            self._profile.append_observation(
                field_label=cand.field or "note",
                value=_stringify(cand.value),
                evidence=cand.evidence,
            )
            return True

        key = (cand.cluster, cand.field)

        # List fields: always append (with deduplication)
        if key in LIST_FIELDS:
            value = cand.value
            if not isinstance(value, list):
                value = [value]
            changed = False
            for v in value:
                if self._profile.append_list(cand.cluster, cand.field, v):
                    changed = True
            # Also log an observation for the audit trail
            if changed:
                self._profile.append_observation(
                    field_label=f"{cand.cluster}.{cand.field}",
                    value=_stringify(cand.value),
                    evidence=cand.evidence,
                )
            return changed

        # Scalar fields: set
        if cand.operation == "set":
            changed = self._profile.set(cand.cluster, cand.field, cand.value)
            if changed:
                self._profile.append_observation(
                    field_label=f"{cand.cluster}.{cand.field}",
                    value=_stringify(cand.value),
                    evidence=cand.evidence,
                )
            return changed

        # append on a non-list field → log as observation
        self._profile.append_observation(
            field_label=f"{cand.cluster}.{cand.field}",
            value=_stringify(cand.value),
            evidence=cand.evidence,
        )
        return True

    # ------------------------------------------------------------------
    # Person
    # ------------------------------------------------------------------

    def _apply_to_person(self, person, cand: Candidate) -> None:
        """All person facts are stored as observations (keeping people/*.md simple)."""
        # Name field: possible alias if the name is spelled differently
        if cand.cluster == "identity" and cand.field == "name":
            value_str = str(cand.value).strip()
            if value_str and value_str.lower() != person.name.lower():
                person.add_alias(value_str)
            return
        person.append_observation(
            field_label=f"{cand.cluster}.{cand.field}",
            value=_stringify(cand.value),
            evidence=cand.evidence,
        )


def _stringify(v: Any) -> str:
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    if isinstance(v, dict):
        return ", ".join(f"{k}={val}" for k, val in v.items())
    return str(v)
