"""Per-machine code-signing identity for the managed macOS app bundle.

macOS TCC pins every privacy grant (Microphone, Screen Recording, ...) to an
app's *designated requirement*. For an ad-hoc signature that requirement is
the code-directory hash itself, so every rebuild of the app is a brand-new
app to TCC and every grant is orphaned (BUG-083/159/161). Signing with a
certificate changes the requirement to ``identifier "<bundle id>" and
certificate leaf = H"<cert hash>"`` — stable for as long as the same
certificate signs the bundle, however often its contents change.

There is no Developer ID on the source-install path, so this module creates a
self-signed code-signing certificate ONCE per user, keeps it in the login
keychain, and trusts it for code signing in the user's own trust domain. The
trust step is the one place macOS asks for the login password; everything
else is silent. Creation therefore only ever runs from the installer, where a
dialog is expected — the app itself only *looks up* the identity.

Pure stdlib + ``cryptography``; every ``security`` call is bounded and the
whole thing degrades to ``None`` (= ad-hoc signing, the previous behaviour)
rather than raising.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jarvis.core.branding import MACOS_SIGNING_IDENTITY_LABEL as IDENTITY_LABEL
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

_SECURITY = "/usr/bin/security"
_CODESIGN = "/usr/bin/codesign"
# `security find-identity -v` prints one line per identity:
#   1) 0E147B028894D40DC98570FE8FEB4E53253E3E21 "Personal Jarvis Local Signing"
_IDENTITY_LINE = re.compile(r'^\s*\d+\)\s+([0-9A-Fa-f]{40})\s+"(?P<label>.+)"\s*$')
_CERT_VALIDITY = timedelta(days=365 * 10)
# The trust dialog is answered by a human; a headless session never answers.
_TRUST_TIMEOUT_S = 180
_LAST_ERROR: str | None = None

Runner = Callable[..., subprocess.CompletedProcess]


def last_error() -> str | None:
    """Why the last ``ensure_local_signing_identity`` returned ``None``."""
    return _LAST_ERROR


def _run(runner: Runner, argv: list[str], *, timeout: float) -> subprocess.CompletedProcess:
    return runner(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )


def find_local_signing_identity(runner: Runner = subprocess.run) -> str | None:
    """Return the SHA-1 of the valid local signing identity, or ``None``.

    ``-v -p codesigning`` lists only identities macOS would actually let
    ``codesign`` use: certificate present, private key present, trusted for
    code signing. An untrusted leftover therefore reads as "absent", which is
    exactly right — signing with it would fail.
    """
    if sys.platform != "darwin":
        return None
    try:
        result = _run(runner, [_SECURITY, "find-identity", "-v", "-p", "codesigning"], timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("security find-identity did not run: %s", exc)
        return None
    for line in (result.stdout or "").splitlines():
        match = _IDENTITY_LINE.match(line)
        if match and match.group("label") == IDENTITY_LABEL:
            return match.group(1).upper()
    return None


def _gui_session(runner: Runner) -> bool:
    """Only an Aqua session can show the trust-settings password dialog."""
    try:
        result = _run(runner, ["/bin/launchctl", "managername"], timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("launchctl managername did not answer; assuming no GUI session: %s", exc)
        return False
    return (result.stdout or "").strip() == "Aqua"


def _login_keychain(runner: Runner) -> str | None:
    try:
        result = _run(runner, [_SECURITY, "default-keychain", "-d", "user"], timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("security default-keychain did not answer: %s", exc)
        return None
    path = (result.stdout or "").strip().strip('"')
    return path or None


def _generate_material(password: bytes) -> tuple[bytes, bytes]:
    """``(pkcs12_bytes, cert_pem)`` for a fresh self-signed code-signing cert.

    ``security import`` understands only the legacy PKCS#12 ciphers (SHA-1 +
    3DES); cryptography's default AES/PBKDF2-SHA256 container imports the
    certificate but silently drops the private key, leaving no identity.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, IDENTITY_LABEL)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + _CERT_VALIDITY)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CODE_SIGNING]), critical=True)
        .sign(key, hashes.SHA256())
    )
    encryption = (
        serialization.PrivateFormat.PKCS12.encryption_builder()
        .kdf_rounds(50000)
        .key_cert_algorithm(pkcs12.PBES.PBESv1SHA1And3KeyTripleDESCBC)
        .hmac_hash(hashes.SHA1())  # noqa: S303 — the only MAC `security import` accepts
        .build(password)
    )
    bundle = pkcs12.serialize_key_and_certificates(
        IDENTITY_LABEL.encode("utf-8"), key, cert, None, encryption
    )
    return bundle, cert.public_bytes(serialization.Encoding.PEM)


def create_local_signing_identity(runner: Runner = subprocess.run) -> str | None:
    """Create, import and trust the identity. One password dialog; else silent.

    Returns the new identity's SHA-1, or ``None`` with :func:`last_error`
    set. Never raises: a missing identity means ad-hoc signing, nothing worse.
    """
    global _LAST_ERROR
    _LAST_ERROR = None
    if sys.platform != "darwin":
        _LAST_ERROR = "only macOS uses a code-signing identity"
        return None
    if not _gui_session(runner):
        _LAST_ERROR = "no Aqua session: the trust-settings dialog cannot be shown"
        return None
    keychain = _login_keychain(runner)
    if keychain is None:
        _LAST_ERROR = "the user's login keychain could not be located"
        return None
    try:
        password = secrets.token_urlsafe(24).encode("ascii")
        p12, cert_pem = _generate_material(password)
    except Exception as exc:  # noqa: BLE001 - cryptography missing/broken → ad-hoc
        _LAST_ERROR = f"certificate generation failed: {type(exc).__name__}: {exc}"
        return None
    with tempfile.TemporaryDirectory(prefix="jarvis-signing-") as raw_work:
        work = Path(raw_work)
        p12_path = work / "identity.p12"
        cert_path = work / "identity.pem"
        try:
            fd = os.open(p12_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(p12)
            cert_path.write_bytes(cert_pem)
        except OSError as exc:
            # Reported through last_error(): the caller logs it with the
            # consequence (ad-hoc signing) once, instead of twice here.
            _LAST_ERROR = f"could not stage the identity: {exc}"
            return None
        steps: list[tuple[str, list[str], float]] = [
            (
                "import",
                [
                    _SECURITY,
                    "import",
                    str(p12_path),
                    "-k",
                    keychain,
                    "-P",
                    password.decode("ascii"),
                    "-T",
                    _CODESIGN,
                    "-A",
                ],
                60,
            ),
            (
                # The one interactive step: user-domain trust for code signing.
                "trust",
                [_SECURITY, "add-trusted-cert", "-p", "codeSign", "-k", keychain, str(cert_path)],
                _TRUST_TIMEOUT_S,
            ),
        ]
        for name, argv, timeout in steps:
            try:
                result = _run(runner, argv, timeout=timeout)
            except (OSError, subprocess.TimeoutExpired) as exc:
                # Reported through last_error() by the caller, with the
                # consequence (the app stays ad-hoc signed) spelled out.
                _LAST_ERROR = f"security {name} did not complete: {exc}"
                return None
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "unknown error").strip()
                _LAST_ERROR = f"security {name} failed (rc={result.returncode}): {detail[-300:]}"
                return None
    identity = find_local_signing_identity(runner)
    if identity is None:
        _LAST_ERROR = "the identity was imported but macOS does not list it as valid"
    return identity


def ensure_local_signing_identity(*, create: bool, runner: Runner = subprocess.run) -> str | None:
    """Look the identity up; create it only when the caller allows a dialog."""
    identity = find_local_signing_identity(runner)
    if identity is not None or not create:
        return identity
    identity = create_local_signing_identity(runner)
    if identity is None:
        log.warning(
            "No local code-signing identity — the app stays ad-hoc signed and its "
            "macOS permissions will not survive a rebuild. Reason: %s",
            last_error(),
        )
    else:
        log.info("Created the local code-signing identity %s (%s).", IDENTITY_LABEL, identity)
    return identity


__all__ = [
    "IDENTITY_LABEL",
    "create_local_signing_identity",
    "ensure_local_signing_identity",
    "find_local_signing_identity",
    "last_error",
]
