"""Bounded optional-service maintenance; PostgreSQL remains the sole authority."""

from __future__ import annotations

import logging
from typing import Any

from .store import SwarmAccessError, SwarmConflictError

log = logging.getLogger(__name__)


class DeliveryMaintenance:
    """Visit one team per pass on the service's dedicated optional-I/O lane."""

    def __init__(self) -> None:
        self.offset = 0
        self.registry: Any = None

    def tick(self, registry: Any, consumer: str) -> bool:
        if registry is None:
            self.registry = None
            self.offset = 0
            return False
        if registry is not self.registry:
            self.registry = registry
            self.offset = 0
        teams = registry.list(limit=1, offset=self.offset)
        self.offset = self.offset + len(teams) if teams else 0
        if not teams or teams[0].get("available") is False:
            return False
        store = registry.open(teams[0]["id"])
        coordination = getattr(registry.delivery, "coordination", None)
        if coordination is not None:
            coordination.refresh(store)
        # Each implementation snapshots PG state, releases its transaction,
        # performs Redis I/O, and only then commits bounded acknowledgements.
        store.pump_delivery(limit=32)
        hints = registry.delivery.poll(store, consumer, limit=32)
        verified = []
        for hint in hints:
            events = store.events_after(str(int(hint.event_seq) - 1), limit=1)
            if events and events[0]["id"] == hint.event_id:
                verified.append(hint)
        # Acknowledgement means the wake hint was resolved, not that work was
        # completed. Scheduling/recovery always rereads durable PG state.
        registry.delivery.ack_many(store, verified)
        return bool(verified)


def recover_attempt_uploads(store: Any, controller: Any, actor: Any) -> list[dict[str, Any]]:
    """Recover at most sixteen old-attempt objects without accepting their results."""
    pending = getattr(store, "pending_uploads", None)
    recover = getattr(store, "recover_upload", None)
    if not callable(pending) or not callable(recover):
        return []
    recovered = []
    for upload in pending(controller, actor, limit=16):
        try:
            artifact = recover(controller, actor, upload["id"])
            recovered.append(
                {
                    "id": artifact["id"],
                    "name": artifact["name"],
                    "sha256": artifact["sha256"],
                    "recovered_from": artifact.get("recovered_from"),
                }
            )
        except (SwarmAccessError, SwarmConflictError):
            raise
        except Exception:  # noqa: BLE001 - unknown uploads remain reserved and manually recoverable
            log.warning("A prior Swarm upload remains pending recovery", exc_info=True)
    return recovered
