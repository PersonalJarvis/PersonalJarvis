"""``/api/mission-billing`` — the paid-API fallback switch for missions.

``GET`` reports the switch, whether this install is a subscription install
(the switch has no effect otherwise: an API-key-only install already runs on
its keys), the hard caps, the automatic paid spend of the last 24 h and which
key would be used first. ``PUT`` flips ``[missions] paid_api_fallback`` through
``jarvis/core/config_writer.py``; the next mission decision reads it fresh
(jarvis/missions/capacity.py).

The PUT spends money on the user's behalf from then on, so it is marked
``x-jarvis-dangerous`` (the CLI demands ``--yes``) and the path is excluded
from the brain's app-action catalog: no voice, chat or agent tool can flip it.
The global ``SurfaceSecurity`` boundary guards both routes like every other
``/api`` route (credential required, trusted Origin for a browser write).

Reading never calls a provider: keys, config, login files and the local
ledger only. No response carries a secret — provider slug and model id at
most.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictBool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mission-billing", tags=["missions"])


class MissionBillingBody(BaseModel):
    """The one writable field: the paid-API fallback switch."""

    model_config = ConfigDict(extra="forbid")

    # Strict: "yes", 1 or "true" never switch spending on by coercion.
    paid_api_fallback: StrictBool


def _policy(request: Request) -> Any:
    """The running Kontrollierer's capacity policy (shares the in-memory
    daily reservations), else a fresh one on the same persisted ledger."""
    kontrollierer = getattr(request.app.state, "kontrollierer", None)
    policy = getattr(kontrollierer, "capacity_policy", None)
    if policy is not None:
        return policy
    from jarvis.missions.capacity import CapacityPolicy

    return CapacityPolicy()


def _paid_provider(request: Request) -> dict[str, Any] | None:
    """The key a paid fallback would use first (same order as the offer),
    or None when no priced key is usable."""
    kontrollierer = getattr(request.app.state, "kontrollierer", None)
    option = getattr(kontrollierer, "paid_option", None)
    if option is None:
        from jarvis.missions.init import ApiKeyPaidOption

        option = ApiKeyPaidOption()
    try:
        offer = option.offer(pinned_family=None, open_steps=1, reason="provider_quota")
    except Exception:  # noqa: BLE001 - a broken key read means "no key shown", logged
        logger.warning("mission-billing: paid provider lookup failed", exc_info=True)
        return None
    if offer is None:
        return None
    return {"provider": offer.provider, "model": offer.model, "price_known": True}


def _billing_state(request: Request) -> dict[str, Any]:
    from jarvis.missions.capacity import (
        PAID_DAILY_CAP_USD,
        PAID_MISSION_CAP_USD,
        paid_api_fallback_enabled,
    )

    policy = _policy(request)
    try:
        subscription_mode = bool(policy.pinned())
    except Exception:  # noqa: BLE001 - shown as a key-only install, logged
        logger.warning("mission-billing: subscription signal unreadable", exc_info=True)
        subscription_mode = False
    return {
        "paid_api_fallback": paid_api_fallback_enabled(),
        "subscription_mode": subscription_mode,
        "per_mission_cap_usd": PAID_MISSION_CAP_USD,
        "daily_cap_usd": PAID_DAILY_CAP_USD,
        "spent_last_24h_usd": round(policy.daily.spent_last_24h(), 6),
        "paid_provider": _paid_provider(request),
    }


@router.get("")
def get_mission_billing(request: Request) -> dict[str, Any]:
    """Paid-API fallback switch, caps, automatic spend of the last 24 h, and
    the key that would be used first. Read-only; never calls a provider."""
    return _billing_state(request)


@router.put("", openapi_extra={"x-jarvis-dangerous": True})
def put_mission_billing(body: MissionBillingBody, request: Request) -> dict[str, Any]:
    """Turn automatic paid API use for missions on or off.

    ON: when no connected subscription has capacity, a mission continues on
    your own API key, within $2 per mission and $10 per rolling 24 h. OFF
    (default): it waits for a subscription; a one-off approval per mission
    stays possible. Applies to the next decision, no restart. No effect on an
    API-key-only install.
    """
    from jarvis.core.config_writer import set_missions_paid_api_fallback

    try:
        set_missions_paid_api_fallback(body.paid_api_fallback)
    except (OSError, ValueError) as exc:
        logger.exception("mission-billing: could not persist the paid fallback switch")
        raise HTTPException(
            status_code=500, detail="The setting could not be saved."
        ) from exc
    logger.warning(
        "mission-billing: paid API fallback for missions turned %s",
        "ON" if body.paid_api_fallback else "OFF",
    )
    return _billing_state(request)
