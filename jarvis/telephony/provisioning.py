"""Twilio account provisioning via the REST API (``twilio.rest.Client``).

Thin, side-effect-light wrapper used by the ``/api/telephony/test`` endpoint,
the ``GET /status`` reachability probe, and ``scripts/telephony_provision.py``:

  * ``verify_credentials`` — cheap auth check (fetch account).
  * ``list_available_numbers`` — search buyable numbers by country/area.
  * ``buy_number`` — purchase a number and point its voice webhook at Jarvis.
  * ``set_voice_webhook`` — (re)point an owned number's webhook.
  * ``inspect_number`` — read back an owned number's current webhook.

Every function takes explicit ``account_sid`` / ``auth_token`` so callers stay
in control of where the secret comes from (always ``get_secret`` at the call
site, never hard-coded — AP-12). All functions raise ``TelephonyProvisionError``
with a clean English message on failure; the caller turns that into JSON.
"""

from __future__ import annotations

from dataclasses import dataclass


class TelephonyProvisionError(RuntimeError):
    """Raised when a Twilio REST operation cannot complete."""


@dataclass(frozen=True, slots=True)
class AvailableNumber:
    phone_number: str
    friendly_name: str
    locality: str
    region: str
    iso_country: str


@dataclass(frozen=True, slots=True)
class OwnedNumber:
    sid: str
    phone_number: str
    friendly_name: str
    voice_url: str


def _client(account_sid: str, auth_token: str):  # noqa: ANN202 - twilio Client type optional
    if not account_sid or not auth_token:
        raise TelephonyProvisionError("Twilio account SID and auth token are required.")
    try:
        from twilio.rest import Client  # type: ignore[import-untyped]
    except ImportError as exc:
        raise TelephonyProvisionError(
            "The 'twilio' package is not installed. Install the telephony "
            "extra: pip install -e .[telephony]"
        ) from exc
    return Client(account_sid, auth_token)


def verify_credentials(account_sid: str, auth_token: str) -> dict[str, str]:
    """Authenticate against Twilio and return the account status.

    Returns ``{"account_status": "active", "friendly_name": ...}`` on success.
    Raises ``TelephonyProvisionError`` on auth failure / network error.
    """
    client = _client(account_sid, auth_token)
    try:
        account = client.api.accounts(account_sid).fetch()
    except Exception as exc:  # noqa: BLE001 - twilio raises a wide tree
        raise TelephonyProvisionError(f"Twilio credential check failed: {exc}") from exc
    return {
        "account_status": getattr(account, "status", "") or "",
        "friendly_name": getattr(account, "friendly_name", "") or "",
    }


def list_available_numbers(
    account_sid: str,
    auth_token: str,
    *,
    country: str = "DE",
    area_code: str | None = None,
    limit: int = 10,
) -> list[AvailableNumber]:
    """Search buyable voice-capable numbers in ``country``."""
    client = _client(account_sid, auth_token)
    try:
        kwargs: dict[str, object] = {"voice_enabled": True, "limit": limit}
        if area_code:
            kwargs["area_code"] = area_code
        results = client.available_phone_numbers(country).local.list(**kwargs)
    except Exception as exc:  # noqa: BLE001
        raise TelephonyProvisionError(f"Number search failed: {exc}") from exc
    return [
        AvailableNumber(
            phone_number=getattr(n, "phone_number", "") or "",
            friendly_name=getattr(n, "friendly_name", "") or "",
            locality=getattr(n, "locality", "") or "",
            region=getattr(n, "region", "") or "",
            iso_country=getattr(n, "iso_country", "") or country,
        )
        for n in results
    ]


def buy_number(
    account_sid: str,
    auth_token: str,
    *,
    phone_number: str,
    voice_webhook_url: str,
) -> OwnedNumber:
    """Purchase ``phone_number`` and set its voice webhook to ``voice_webhook_url``."""
    client = _client(account_sid, auth_token)
    try:
        bought = client.incoming_phone_numbers.create(
            phone_number=phone_number,
            voice_url=voice_webhook_url,
            voice_method="POST",
        )
    except Exception as exc:  # noqa: BLE001
        raise TelephonyProvisionError(f"Number purchase failed: {exc}") from exc
    return OwnedNumber(
        sid=getattr(bought, "sid", "") or "",
        phone_number=getattr(bought, "phone_number", "") or phone_number,
        friendly_name=getattr(bought, "friendly_name", "") or "",
        voice_url=getattr(bought, "voice_url", "") or voice_webhook_url,
    )


def set_voice_webhook(
    account_sid: str,
    auth_token: str,
    *,
    phone_number: str,
    voice_webhook_url: str,
) -> OwnedNumber:
    """Point an already-owned number's voice webhook at Jarvis."""
    client = _client(account_sid, auth_token)
    try:
        owned = client.incoming_phone_numbers.list(phone_number=phone_number, limit=1)
        if not owned:
            raise TelephonyProvisionError(f"Number {phone_number} is not owned by this account.")
        updated = owned[0].update(voice_url=voice_webhook_url, voice_method="POST")
    except TelephonyProvisionError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise TelephonyProvisionError(f"Webhook update failed: {exc}") from exc
    return OwnedNumber(
        sid=getattr(updated, "sid", "") or "",
        phone_number=getattr(updated, "phone_number", "") or phone_number,
        friendly_name=getattr(updated, "friendly_name", "") or "",
        voice_url=getattr(updated, "voice_url", "") or voice_webhook_url,
    )


def inspect_number(account_sid: str, auth_token: str, *, phone_number: str) -> OwnedNumber | None:
    """Return the current config of an owned number, or ``None`` if not owned."""
    client = _client(account_sid, auth_token)
    try:
        owned = client.incoming_phone_numbers.list(phone_number=phone_number, limit=1)
    except Exception as exc:  # noqa: BLE001
        raise TelephonyProvisionError(f"Number inspect failed: {exc}") from exc
    if not owned:
        return None
    n = owned[0]
    return OwnedNumber(
        sid=getattr(n, "sid", "") or "",
        phone_number=getattr(n, "phone_number", "") or phone_number,
        friendly_name=getattr(n, "friendly_name", "") or "",
        voice_url=getattr(n, "voice_url", "") or "",
    )


__all__ = [
    "AvailableNumber",
    "OwnedNumber",
    "TelephonyProvisionError",
    "buy_number",
    "inspect_number",
    "list_available_numbers",
    "set_voice_webhook",
    "verify_credentials",
]
