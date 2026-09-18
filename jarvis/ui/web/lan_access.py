"""Phone access on the home network (opt-in ``[ui].lan_access``).

A second uvicorn listener serves the SAME ASGI app over HTTPS on the
machine's private LAN address only (never 0.0.0.0, never a public address).
HTTPS is not optional: ``SurfaceSecurity`` refuses to mint a session cookie
over plain HTTP on a non-loopback bind, and it is right to.

* The certificate is self-signed for the LAN IP and kept in the user data
  dir; the phone shows one "not private" warning the first time.
* A LAN request is never "local", so the browser lock does not matter: the
  phone signs in with a one-time pairing token (``#pair=`` in the URL the
  Settings QR encodes) or the Control Key.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import ipaddress
import logging
import secrets
import socket
from pathlib import Path
from typing import Any

from jarvis.core.paths import user_data_dir

log = logging.getLogger(__name__)

#: Documentation-range address: ``connect`` on a UDP socket only picks the
#: outgoing interface, nothing is sent.
_PROBE_ADDR = ("192.0.2.1", 9)


def lan_ip() -> str | None:
    """This machine's private IPv4 address on the default route, or None."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(_PROBE_ADDR)
            ip = str(sock.getsockname()[0])
    except OSError:
        return None
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if not addr.is_private or addr.is_loopback or addr.is_link_local:
        return None
    return ip


def lan_origin(cfg: Any) -> str | None:
    """``https://<lan-ip>:<port>`` when LAN access is on and a LAN IP exists."""
    ui = getattr(cfg, "ui", None)
    if not getattr(ui, "lan_access", False):
        return None
    ip = lan_ip()
    if ip is None:
        return None
    return f"https://{ip}:{int(getattr(ui, 'lan_port', 47843))}"


def _cert_dir() -> Path:
    path = user_data_dir() / "lan-tls"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_certificate(ip: str, directory: Path | None = None) -> tuple[Path, Path]:
    """A self-signed cert/key pair whose SAN is ``ip``; reused while it matches."""
    from cryptography import x509  # noqa: PLC0415 - only when LAN access is on
    from cryptography.hazmat.primitives import hashes, serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import ec  # noqa: PLC0415
    from cryptography.x509.oid import NameOID  # noqa: PLC0415

    folder = directory or _cert_dir()
    cert_path, key_path = folder / "lan-cert.pem", folder / "lan-key.pem"
    now = _dt.datetime.now(_dt.UTC)
    if cert_path.is_file() and key_path.is_file():
        try:
            cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
            ips = [str(v) for v in san.value.get_values_for_type(x509.IPAddress)]
            if ip in ips and cert.not_valid_after_utc > now + _dt.timedelta(days=7):
                return cert_path, key_path
        except Exception:  # noqa: BLE001 - an unreadable cert is simply replaced
            log.info("LAN certificate unreadable; issuing a new one", exc_info=True)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Jarvis {ip}")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(minutes=5))
        .not_valid_after(now + _dt.timedelta(days=397))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(ip))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def mint_pairing_url(cfg: Any) -> str | None:
    """A one-time sign-in URL for a phone, or None when LAN access is off."""
    origin = lan_origin(cfg)
    if origin is None:
        return None
    from jarvis.ui.web.surface_security import _register_bootstrap_tokens  # noqa: PLC0415

    token = secrets.token_urlsafe(32)
    _register_bootstrap_tokens((token,))
    return f"{origin}/#pair={token}"


async def start_lan_listener(app: Any, cfg: Any, *, log_level: str = "info") -> Any | None:
    """Serve ``app`` over HTTPS on the LAN IP. Returns the uvicorn server or None."""
    origin = lan_origin(cfg)
    if origin is None:
        return None
    import uvicorn  # noqa: PLC0415

    from jarvis.core import control_key  # noqa: PLC0415
    from jarvis.ui.web.control_auth import assert_bind_safe  # noqa: PLC0415

    ip = lan_ip()
    assert ip is not None  # lan_origin checked it
    assert_bind_safe(ip, control_key.get_control_key())
    cert, key = await asyncio.to_thread(ensure_certificate, ip)
    server = uvicorn.Server(
        uvicorn.Config(
            app=app,
            host=ip,
            port=int(cfg.ui.lan_port),
            ssl_certfile=str(cert),
            ssl_keyfile=str(key),
            log_level=log_level,
            lifespan="off",
            loop="asyncio",
        )
    )
    task = asyncio.create_task(server.serve(), name="lan-https")
    server._jarvis_task = task  # type: ignore[attr-defined]  # held against GC
    log.info("LAN access on %s", origin)
    return server


__all__ = [
    "ensure_certificate",
    "lan_ip",
    "lan_origin",
    "mint_pairing_url",
    "start_lan_listener",
]
