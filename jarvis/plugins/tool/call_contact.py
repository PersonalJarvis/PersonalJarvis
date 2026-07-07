"""``call-contact`` tool — place a real outbound call to a saved contact.

Chunk B (Brain integration). Router-tier, the integrator's headline action.

What it does
------------
"ruf Christoph an" -> resolve "Christoph" to a phone number (Contract 1:
``ContactStore.find_by_alias``), then dial it and have Jarvis speak an opening
in the Charon voice before conversing (Contract 2:
``jarvis.telephony.outbound.place_call``). An optional ``message`` becomes the
spoken opening; otherwise a short default opener is used so the callee is not
greeted by silence.

Risk tier ``ask``: dialing a real person is consequential, so the ToolExecutor's
approval workflow echo-confirms before the call goes out. (The tool itself does
not implement the confirm — the risk tier drives it.)

Cloud-first degradation (€5-VPS doctrine)
-----------------------------------------
Both the telephony engine (Contract 2, behind the ``[telephony]`` extra) and the
Twilio configuration are optional. When either is absent/unconfigured the tool
returns a clear English no-op pointing at the Telephony section — it never
crashes the turn and never raises. Contract 2's ``place_call`` raises a clear
English error when twilio is missing/unconfigured; the tool surfaces that.

Contracts are injectable for tests
----------------------------------
``store_resolver`` (Contract 1), ``place_call`` (Contract 2) and
``call_config_resolver`` (the Twilio config + secret) are all injectable, so B's
tests stub both contracts without importing Chunk A/C. In production the factory
passes the real store resolver; ``place_call``/config default to lazy loaders.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from jarvis.core.protocols import ToolResult

log = logging.getLogger(__name__)

# Spoken first when the user gives no explicit message. English per the Output
# Language Policy; the runtime TTS is bilingual, and the brain can always pass a
# localized ``message`` to override this.
_DEFAULT_OPENING: str = (
    "Hello, this is an assistant calling on behalf of my user. "
    "Do you have a moment?"
)


def _load_place_call() -> Callable[..., str] | None:
    """Lazy-import Contract 2's ``place_call`` (Chunk C, ``[telephony]`` extra).

    Returns ``None`` when the module is not available — Chunk C not merged, or
    the telephony extra not installed. Module-level so tests can monkeypatch it
    to deterministically exercise the engine-absent no-op regardless of whether
    Chunk C is present in the worktree.
    """
    try:
        from jarvis.telephony.outbound import place_call  # type: ignore[import]
    except Exception as exc:  # noqa: BLE001 — extra/module absent is expected
        log.debug("call-contact: place_call unavailable: %s", exc)
        return None
    return place_call


def _default_call_config() -> dict[str, str] | None:
    """Resolve the Twilio config + auth token for an outbound call.

    Returns a dict with ``account_sid``/``auth_token``/``from_number``/
    ``public_base_url`` when fully configured, else ``None``. Fully defensive:
    any missing piece or import error yields ``None`` so the tool degrades to a
    clean "configure the Telephony section" message.

    ``JarvisConfig`` has no top-level ``telephony`` field — Twilio lives at
    ``integrations.twilio`` (``config.py`` ``IntegrationsConfig``), and its
    field is ``phone_number``, not ``from_number`` (``config.py``
    ``TwilioConfig``). This mirrors exactly what the working
    ``/api/telephony`` routes read (``telephony_routes.py`` ``_twilio_cfg`` /
    ``_config_payload``). The token is read via ``get_secret`` only (never
    from jarvis.toml — AP-2/AP-12), under the real credential-manager key
    ``twilio_auth_token`` (see ``TwilioConfig`` docstring +
    ``telephony_routes.py`` ``_AUTH_TOKEN_KEY``) — not the ENV var name.
    """
    try:
        from jarvis.core import config as cfg

        loaded = cfg.load_config()
        integrations = getattr(loaded, "integrations", None)
        tcfg = getattr(integrations, "twilio", None)
        if tcfg is None:
            return None
        account_sid = getattr(tcfg, "account_sid", None)
        from_number = getattr(tcfg, "phone_number", None)
        public_base_url = getattr(tcfg, "public_base_url", None)
        auth_token = cfg.get_secret("twilio_auth_token", env_fallback="TWILIO_AUTH_TOKEN")
        # Honor the explicit Enabled switch (wizard step 5 / Telephony UI), just
        # like the working ``/api/telephony/outbound`` route, which refuses with
        # 409 "Telephony is disabled" when ``twilio.enabled`` is false
        # (telephony_routes.py). A filled-but-disabled config is a deliberate
        # "off" — resolve to None so "call X" honestly no-ops instead of placing
        # a real outbound call gated only by the generic ask-tier confirmation.
        enabled = getattr(tcfg, "enabled", False)
        if not (enabled and account_sid and auth_token and from_number and public_base_url):
            return None
        return {
            "account_sid": str(account_sid),
            "auth_token": str(auth_token),
            "from_number": str(from_number),
            "public_base_url": str(public_base_url),
        }
    except Exception as exc:  # noqa: BLE001 — unconfigured telephony is a no-op, not a crash
        log.debug("call-contact: telephony config unavailable: %s", exc)
        return None


_TELEPHONY_UNAVAILABLE_MSG: str = (
    "I can't place calls yet — telephony is not set up. Configure Twilio in the "
    "Telephony section first."
)


class CallContactTool:
    """Router-tier tool that places a real outbound call to a saved contact."""

    name: str = "call-contact"
    description: str = (
        "Place a real outbound phone call to a saved contact and speak to them "
        "live. Use this when the user says 'ruf Christoph an' / 'call Laura'. "
        "Resolve the person by name; an optional message becomes what you say "
        "first when they pick up. Only works for contacts that have a phone "
        "number saved and when telephony is configured."
    )
    risk_tier: str = "ask"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The contact's name or alias to call, e.g. 'Christoph'.",
            },
            "message": {
                "type": "string",
                "description": (
                    "Optional opening line to speak when the contact answers. "
                    "If omitted a short default greeting is used."
                ),
            },
        },
        "required": ["name"],
    }
    input_examples: list[dict[str, Any]] = [
        {"name": "Christoph"},
        {"name": "Laura", "message": "Hi Laura, are we still on for Friday?"},
    ]

    def __init__(
        self,
        *,
        store_resolver: Callable[[], Any],
        place_call: Callable[..., str] | None = None,
        call_config_resolver: Callable[[], dict[str, str] | None] | None = None,
    ) -> None:
        self._resolve_store = store_resolver
        # When None, resolved lazily at execute time via _load_place_call().
        self._place_call = place_call
        self._resolve_call_config = call_config_resolver or _default_call_config

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        name = str(args.get("name", "")).strip()
        if not name:
            return ToolResult(success=False, output="", error="missing 'name' argument")

        store = self._resolve_store()
        if store is None:
            log.warning("call-contact: no ContactStore available (Chunk A not merged?)")
            return ToolResult(
                success=False,
                output="",
                error=(
                    "contacts are not available yet — open the Contacts section "
                    "to add people first"
                ),
            )

        try:
            contact = store.find_by_alias(name)
        except Exception as exc:  # noqa: BLE001 — store error must not crash the turn
            log.warning("call-contact: store.find_by_alias raised %s", exc)
            return ToolResult(success=False, output="", error="contact lookup failed")

        if contact is None:
            return ToolResult(
                success=False,
                output="",
                error=f"no contact named {name!r} — add them in the Contacts section",
            )

        phone = getattr(contact, "primary_phone", None)
        if not phone:
            phones = list(getattr(contact, "phones", []) or [])
            phone = phones[0] if phones else None
        if not phone:
            display = getattr(contact, "name", name)
            return ToolResult(
                success=False,
                output="",
                error=f"no phone number saved for {display} — add one in the Contacts section",
            )

        # Telephony config (Twilio account + secret). Absent -> clean no-op.
        config = self._resolve_call_config()
        if not config:
            log.info("call-contact: telephony unconfigured; not dialing %s", name)
            return ToolResult(success=False, output="", error=_TELEPHONY_UNAVAILABLE_MSG)

        # Telephony engine (Contract 2). Absent (extra not installed / Chunk C
        # not merged) -> clean no-op.
        place_call = self._place_call or _load_place_call()
        if place_call is None:
            log.info("call-contact: telephony engine unavailable; not dialing %s", name)
            return ToolResult(success=False, output="", error=_TELEPHONY_UNAVAILABLE_MSG)

        opening = str(args.get("message") or "").strip() or _DEFAULT_OPENING
        display = getattr(contact, "name", name)

        # Privacy: the dialed number is logged at DEBUG only; INFO carries the
        # contact name, never the raw number.
        log.info("call-contact: dialing %s", display)
        log.debug("call-contact: %s -> %s", display, phone)
        try:
            call_sid = place_call(to=str(phone), opening=opening, **config)
        except Exception as exc:  # noqa: BLE001 — Contract 2 raises clear English; surface it
            log.warning("call-contact: place_call failed: %s", exc)
            return ToolResult(
                success=False,
                output="",
                error=f"could not place the call: {exc}",
            )

        return ToolResult(
            success=True,
            output=f"Calling {display} at {phone} (call id {call_sid}).",
        )
