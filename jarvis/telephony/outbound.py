"""Outbound calling via the Twilio REST API (``twilio.rest.Client``).

This is the **Chunk C** half of the contacts feature: a contact-agnostic engine
that dials a *raw* E.164 number and lets Jarvis speak an opening line, then
converse — reusing the entire inbound conversation loop (``session.py`` +
the ``/api/telephony/media`` Media Streams socket). No knowledge of contacts
lives here; the caller passes a bare number.

The mechanism mirrors the inbound path exactly:

  1. ``place_call`` issues ``client.calls.create(to, from_, url=…)`` where ``url``
     points back at the existing ``/api/telephony/voice`` webhook. The ``opening``
     rides along as a URL query parameter (``place_call`` is a stateless function
     with no access to the call registry, and the per-call secret is minted
     server-side by the webhook once Twilio supplies the ``CallSid``).
  2. When the callee answers, Twilio fetches that webhook, which returns
     ``<Connect><Stream>`` TwiML carrying ``direction=outbound`` + the ``opening``
     (plus the per-call secret) and pointing at the media socket.
  3. The session speaks the opening first, then runs the normal turn loop.

Cloud-first doctrine: the ``twilio`` SDK is an OPTIONAL extra
(``pip install -e .[telephony]``). When it is absent or the account is not
configured, ``place_call`` raises :class:`TelephonyProvisionError` with a clear
English message — the same graceful guard as ``provisioning.py`` (AD-T8) — so the
caller can degrade to a logged no-op rather than crash (AP-12: the auth token is
always supplied by the caller via ``get_secret``, never hard-coded).
"""

from __future__ import annotations

import re
from urllib.parse import urlencode

from .provisioning import TelephonyProvisionError
from .security import public_url_for

# Raw E.164: a leading '+' then 7..15 digits (first digit non-zero).
_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")

# The outbound TwiML webhook reuses the existing inbound voice route — its
# ``<Connect><Stream>`` machinery is identical; only the custom parameters
# (direction/opening) differ. Keep this in sync with telephony_routes.py.
_VOICE_WEBHOOK_PATH = "/api/telephony/voice"


def _client(account_sid: str, auth_token: str):  # noqa: ANN202 - twilio Client type optional
    """Build a twilio ``Client`` or raise ``TelephonyProvisionError``.

    Identical guard to ``provisioning._client``: missing credentials or a missing
    SDK both surface as a clear English ``TelephonyProvisionError`` rather than a
    bare ``ImportError``/``ValueError``.
    """
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


def place_call(
    *,
    to: str,
    opening: str = "",
    account_sid: str,
    auth_token: str,
    from_number: str,
    public_base_url: str,
) -> str:
    """Dial a raw E.164 number; Jarvis speaks ``opening`` first, then converses.

    This is **Contract 2** (frozen — Chunk B codes against this exact signature).

    Args:
        to: The raw destination number in E.164 (e.g. ``+4915112345678``). The
            engine is contact-agnostic — it never resolves a name.
        opening: Optional first line Jarvis speaks once the callee answers. It is
            delivered to the session through the voice webhook (URL query ->
            TwiML ``<Parameter>``) and scrubbed before TTS like any spoken text.
        account_sid: Twilio Account SID (``AC…``; an identifier, not a secret).
        auth_token: Twilio Auth Token — always supplied by the caller via
            ``get_secret`` (AP-12), never read here from config/env.
        from_number: The owned Twilio number to place the call from (E.164).
        public_base_url: The HTTPS base URL Twilio can reach Jarvis on. The
            outbound TwiML webhook URL is derived from it.

    Returns:
        The Twilio ``CallSid`` of the placed call.

    Raises:
        TelephonyProvisionError: with a clear English message when the ``twilio``
            SDK is missing, credentials are absent, the configuration is
            incomplete, ``to``/``from`` are not E.164, or the REST call fails.
    """
    to = (to or "").strip()
    from_number = (from_number or "").strip()
    public_base_url = (public_base_url or "").strip()

    if not _E164_RE.match(to):
        raise TelephonyProvisionError(
            f"Outbound 'to' must be an E.164 number, e.g. +4915112345678; got {to!r}."
        )
    if not _E164_RE.match(from_number):
        raise TelephonyProvisionError(
            "Outbound calling requires a configured E.164 Twilio 'from' number."
        )
    if not public_base_url:
        raise TelephonyProvisionError(
            "Outbound calling requires 'public_base_url' (the HTTPS URL Twilio can reach)."
        )

    client = _client(account_sid, auth_token)

    url = public_url_for(public_base_url, _VOICE_WEBHOOK_PATH)
    if opening:
        url = f"{url}?{urlencode({'opening': opening})}"

    try:
        call = client.calls.create(to=to, from_=from_number, url=url)
    except TelephonyProvisionError:
        raise
    except Exception as exc:  # noqa: BLE001 - twilio raises a wide error tree
        raise TelephonyProvisionError(f"Outbound call failed: {exc}") from exc

    return getattr(call, "sid", "") or ""


__all__ = ["TelephonyProvisionError", "place_call"]
