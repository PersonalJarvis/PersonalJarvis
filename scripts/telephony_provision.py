"""CLI over jarvis.telephony.provisioning — list/buy numbers, set the webhook.

Reads Twilio credentials from the standard places: account SID from
``[integrations.twilio]`` in jarvis.toml (or ``--sid``), auth token from the
Credential Manager (``twilio_auth_token`` / ENV ``TWILIO_AUTH_TOKEN``).

Examples
--------
    python scripts/telephony_provision.py verify
    python scripts/telephony_provision.py list --country DE --area-code 30
    python scripts/telephony_provision.py buy --number +49301234567 \
        --url https://jarvis.example.com/api/telephony/voice
    python scripts/telephony_provision.py set-webhook --number +49301234567 \
        --url https://jarvis.example.com/api/telephony/voice
    python scripts/telephony_provision.py inspect --number +49301234567
"""

from __future__ import annotations

import argparse
import sys

# Windows Unicode rule: emit UTF-8 regardless of the cp1252 console default.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001, S110 - console encoding is best-effort
    pass

from jarvis.core.config import get_secret, load_config
from jarvis.telephony import provisioning


def _resolve_credentials(args: argparse.Namespace) -> tuple[str, str]:
    sid = args.sid
    if not sid:
        try:
            sid = load_config().integrations.twilio.account_sid
        except Exception:  # noqa: BLE001
            sid = ""
    token = args.token or get_secret("twilio_auth_token", "TWILIO_AUTH_TOKEN") or ""
    if not sid or not token:
        print(
            "ERROR: missing account SID or auth token. Set them in jarvis.toml / "
            "the credential manager, or pass --sid / --token.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return sid, token


def _cmd_verify(args: argparse.Namespace) -> int:
    sid, token = _resolve_credentials(args)
    info = provisioning.verify_credentials(sid, token)
    print(f"OK: account_status={info['account_status']} name={info['friendly_name']}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    sid, token = _resolve_credentials(args)
    numbers = provisioning.list_available_numbers(
        sid, token, country=args.country, area_code=args.area_code, limit=args.limit
    )
    if not numbers:
        print("No available numbers found.")
        return 0
    for n in numbers:
        print(f"{n.phone_number}  {n.locality or n.region}  ({n.iso_country})")
    return 0


def _cmd_buy(args: argparse.Namespace) -> int:
    sid, token = _resolve_credentials(args)
    owned = provisioning.buy_number(
        sid, token, phone_number=args.number, voice_webhook_url=args.url
    )
    print(f"Bought {owned.phone_number} (sid={owned.sid}); voice webhook -> {owned.voice_url}")
    return 0


def _cmd_set_webhook(args: argparse.Namespace) -> int:
    sid, token = _resolve_credentials(args)
    owned = provisioning.set_voice_webhook(
        sid, token, phone_number=args.number, voice_webhook_url=args.url
    )
    print(f"Updated {owned.phone_number}: voice webhook -> {owned.voice_url}")
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    sid, token = _resolve_credentials(args)
    owned = provisioning.inspect_number(sid, token, phone_number=args.number)
    if owned is None:
        print(f"{args.number} is not owned by this account.")
        return 1
    print(f"{owned.phone_number} (sid={owned.sid}); voice webhook = {owned.voice_url or '(none)'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Twilio number provisioning for Jarvis telephony")
    parser.add_argument("--sid", default="", help="Account SID (defaults to jarvis.toml)")
    parser.add_argument("--token", default="", help="Auth token (defaults to credential manager)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("verify", help="Authenticate against Twilio")

    p_list = sub.add_parser("list", help="List buyable numbers")
    p_list.add_argument("--country", default="DE")
    p_list.add_argument("--area-code", dest="area_code", default=None)
    p_list.add_argument("--limit", type=int, default=10)

    p_buy = sub.add_parser("buy", help="Buy a number and set its voice webhook")
    p_buy.add_argument("--number", required=True)
    p_buy.add_argument("--url", required=True)

    p_set = sub.add_parser("set-webhook", help="Point an owned number at Jarvis")
    p_set.add_argument("--number", required=True)
    p_set.add_argument("--url", required=True)

    p_ins = sub.add_parser("inspect", help="Show an owned number's webhook")
    p_ins.add_argument("--number", required=True)

    args = parser.parse_args(argv)

    handlers = {
        "verify": _cmd_verify,
        "list": _cmd_list,
        "buy": _cmd_buy,
        "set-webhook": _cmd_set_webhook,
        "inspect": _cmd_inspect,
    }
    try:
        return handlers[args.cmd](args)
    except provisioning.TelephonyProvisionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
