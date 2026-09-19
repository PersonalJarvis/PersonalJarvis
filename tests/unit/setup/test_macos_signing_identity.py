"""Tests for the per-user macOS code-signing identity (rebuild-proof TCC)."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

import jarvis.setup.macos_signing_identity as msi
from jarvis.setup.macos_signing_identity import (
    IDENTITY_LABEL,
    create_local_signing_identity,
    ensure_local_signing_identity,
    find_local_signing_identity,
)

_SHA1 = "0E147B028894D40DC98570FE8FEB4E53253E3E21"
_KEYCHAIN = "/Users/x/Library/Keychains/login.keychain-db"
_LISTING = (
    "Policy: Code Signing\n"
    "  Matching identities\n"
    f'  1) {_SHA1} "{IDENTITY_LABEL}"\n'
    '  2) AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA "Apple Development: Someone"\n'
    "     2 identities found\n"
)


class _Security:
    """Scripted ``security``/``launchctl`` runner recording every argv."""

    def __init__(self, *, listing: str = "", aqua: bool = True, fail: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.listing = listing
        self.aqua = aqua
        self.fail = fail
        self.listing_after_trust = listing

    def __call__(self, argv, **_kwargs):
        self.calls.append(list(argv))
        verb = argv[1] if len(argv) > 1 else ""
        if argv[0].endswith("launchctl"):
            manager = "Aqua\n" if self.aqua else "Background\n"
            return SimpleNamespace(returncode=0, stdout=manager, stderr="")
        if verb == "default-keychain":
            return SimpleNamespace(returncode=0, stdout=f'    "{_KEYCHAIN}"\n', stderr="")
        if verb == "find-identity":
            return SimpleNamespace(returncode=0, stdout=self.listing, stderr="")
        if verb == self.fail:
            return SimpleNamespace(returncode=1, stdout="", stderr=f"{verb} exploded")
        if verb == "add-trusted-cert":
            self.listing = self.listing_after_trust
        return SimpleNamespace(returncode=0, stdout="", stderr="")


@pytest.fixture(autouse=True)
def _darwin(monkeypatch):
    monkeypatch.setattr(msi.sys, "platform", "darwin")
    # The real generator needs `cryptography`; the flow tests only care about
    # the security(1) choreography around it.
    monkeypatch.setattr(msi, "_generate_material", lambda _password: (b"p12", b"pem"))


def test_find_returns_only_our_label() -> None:
    runner = _Security(listing=_LISTING)

    assert find_local_signing_identity(runner) == _SHA1
    assert runner.calls == [["/usr/bin/security", "find-identity", "-v", "-p", "codesigning"]]


def test_find_is_none_off_macos_or_without_identity(monkeypatch) -> None:
    assert find_local_signing_identity(_Security(listing="     0 valid identities found\n")) is None
    monkeypatch.setattr(msi.sys, "platform", "linux")
    assert find_local_signing_identity(_Security(listing=_LISTING)) is None


def test_find_survives_a_missing_security_binary() -> None:
    def runner(argv, **_kwargs):
        raise OSError("no security")

    assert find_local_signing_identity(runner) is None


def test_create_imports_then_trusts_then_verifies() -> None:
    runner = _Security(listing="     0 valid identities found\n")
    runner.listing_after_trust = _LISTING

    assert create_local_signing_identity(runner) == _SHA1

    verbs = [call[1] if call[0].endswith("security") else call[0] for call in runner.calls]
    assert verbs == [
        "/bin/launchctl",
        "default-keychain",
        "import",
        "add-trusted-cert",
        "find-identity",
    ]
    import_call = next(call for call in runner.calls if call[1] == "import")
    # The key must be usable by codesign without a per-use ACL prompt.
    assert import_call[-3:] == ["-T", "/usr/bin/codesign", "-A"]
    assert "-k" in import_call and _KEYCHAIN in import_call
    trust_call = next(call for call in runner.calls if call[1] == "add-trusted-cert")
    # User-domain trust for code signing only — never `-d` (admin/system).
    assert trust_call[2:6] == ["-p", "codeSign", "-k", _KEYCHAIN]
    assert "-d" not in trust_call


def test_create_refuses_without_a_gui_session() -> None:
    runner = _Security(aqua=False)

    assert create_local_signing_identity(runner) is None
    assert msi.last_error() is not None and "Aqua" in msi.last_error()
    assert [call[1] for call in runner.calls if call[0].endswith("security")] == []


def test_create_reports_a_failing_trust_step() -> None:
    runner = _Security(listing="     0 valid identities found\n", fail="add-trusted-cert")

    assert create_local_signing_identity(runner) is None
    assert "add-trusted-cert" in (msi.last_error() or "")


def test_create_reports_an_identity_macos_does_not_list() -> None:
    runner = _Security(listing="     0 valid identities found\n")

    assert create_local_signing_identity(runner) is None
    assert "does not list it as valid" in (msi.last_error() or "")


def test_create_tolerates_a_hung_trust_dialog() -> None:
    def runner(argv, **kwargs):
        if argv[1:2] == ["add-trusted-cert"]:
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 0))
        return _Security(listing="     0 valid identities found\n")(argv, **kwargs)

    assert create_local_signing_identity(runner) is None
    assert "did not complete" in (msi.last_error() or "")


def test_ensure_never_creates_unless_allowed() -> None:
    runner = _Security(listing="     0 valid identities found\n")
    runner.listing_after_trust = _LISTING

    assert ensure_local_signing_identity(create=False, runner=runner) is None
    assert [call[1] for call in runner.calls if call[0].endswith("security")] == ["find-identity"]

    assert ensure_local_signing_identity(create=True, runner=runner) == _SHA1


def test_ensure_reuses_an_existing_identity_without_a_dialog() -> None:
    runner = _Security(listing=_LISTING)

    assert ensure_local_signing_identity(create=True, runner=runner) == _SHA1
    assert [call[1] for call in runner.calls if call[0].endswith("security")] == ["find-identity"]


def test_generated_material_is_a_code_signing_identity(monkeypatch) -> None:
    pytest.importorskip("cryptography")
    monkeypatch.undo()  # the autouse stub must not hide the real generator
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import ExtendedKeyUsageOID

    p12, pem = msi._generate_material(b"secret")

    key, cert, extra = pkcs12.load_key_and_certificates(p12, b"secret")
    assert key is not None and cert is not None and not extra
    assert cert.subject == cert.issuer
    assert cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value == IDENTITY_LABEL
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
    assert eku.critical and ExtendedKeyUsageOID.CODE_SIGNING in eku.value
    digest = cert.signature_hash_algorithm
    assert x509.load_pem_x509_certificate(pem).fingerprint(digest) == cert.fingerprint(digest)
