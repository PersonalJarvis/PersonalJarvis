"""Portable crypto security and interoperability proof; no live service or credentials."""

from __future__ import annotations

import datetime as dt
import time

import asyncssh
import cryptography
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from cryptography.x509.verification import (
    Criticality,
    ExtensionPolicy,
    PolicyBuilder,
    Store,
    VerificationError,
)
from packaging.version import Version

NOW = dt.datetime(2026, 10, 1, tzinfo=dt.UTC)


def certificate(name, key, *, issuer=None, issuer_key=None, ca=True, dns=None, permitted=None):
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    issuer_key = issuer_key or key
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer.subject if issuer else subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - dt.timedelta(days=1))
        .not_valid_after(NOW + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), True)
        .add_extension(x509.KeyUsage(True, False, False, False, False, ca, ca, None, None), True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), False
        )
    )
    if dns:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(dns)]), False)
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False
        )
    if permitted:
        builder = builder.add_extension(x509.NameConstraints([x509.DNSName(permitted)], None), True)
    return builder.sign(issuer_key, hashes.SHA256())


def test_secure_dependency_versions():
    assert Version(cryptography.__version__) >= Version("50.0.2")
    assert Version(asyncssh.__version__) >= Version("2.24.0")


@pytest.mark.parametrize("dns", ["outside.example.test", "*.example.test"])
@pytest.mark.parametrize("custom_policy", [False, True])
def test_name_constraints_reject_outside_names_and_wildcards(dns, custom_policy):
    root_key = ec.generate_private_key(ec.SECP256R1())
    root = certificate("Root", root_key)
    intermediate_key = ec.generate_private_key(ec.SECP256R1())
    intermediate = certificate(
        "Constrained issuer",
        intermediate_key,
        issuer=root,
        issuer_key=root_key,
        permitted="allowed.example.test",
    )
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf = certificate(
        "Leaf", leaf_key, issuer=intermediate, issuer_key=intermediate_key, ca=False, dns=dns
    )
    builder = PolicyBuilder().store(Store([root])).time(NOW)
    if custom_policy:
        builder = builder.extension_policies(
            ca_policy=ExtensionPolicy.permit_all().require_present(
                x509.BasicConstraints, Criticality.AGNOSTIC, None
            ),
            ee_policy=ExtensionPolicy.permit_all().require_present(
                x509.SubjectAlternativeName, Criticality.AGNOSTIC, None
            ),
        )
    verifier = builder.build_server_verifier(x509.DNSName("outside.example.test"))
    with pytest.raises(VerificationError):
        verifier.verify(leaf, [intermediate])


def test_allowed_certificate_chain_still_verifies():
    root_key = ec.generate_private_key(ec.SECP256R1())
    root = certificate("Root", root_key)
    intermediate_key = ec.generate_private_key(ec.SECP256R1())
    intermediate = certificate(
        "Constrained issuer",
        intermediate_key,
        issuer=root,
        issuer_key=root_key,
        permitted="allowed.example.test",
    )
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf = certificate(
        "Leaf",
        leaf_key,
        issuer=intermediate,
        issuer_key=intermediate_key,
        ca=False,
        dns="api.allowed.example.test",
    )
    chain = (
        PolicyBuilder()
        .store(Store([root]))
        .time(NOW)
        .build_server_verifier(x509.DNSName("api.allowed.example.test"))
        .verify(leaf, [intermediate])
    )
    assert chain[-1] == root


def test_duplicate_untrusted_issuers_do_not_amplify_path_building():
    trusted_key = ec.generate_private_key(ec.SECP256R1())
    trusted = certificate("Trusted root", trusted_key)
    other_key = ec.generate_private_key(ec.SECP256R1())
    other = certificate("Untrusted issuer", other_key)
    leaf = certificate(
        "Leaf",
        ec.generate_private_key(ec.SECP256R1()),
        issuer=other,
        issuer_key=other_key,
        ca=False,
        dns="api.example",
    )
    verifier = (
        PolicyBuilder()
        .store(Store([trusted]))
        .time(NOW)
        .max_chain_depth(7)
        .build_server_verifier(x509.DNSName("api.example"))
    )
    started = time.monotonic()
    with pytest.raises(VerificationError):
        verifier.verify(leaf, [other] * 4)
    assert time.monotonic() - started < 1.0


def test_social_identity_signatures_and_mac_pkcs12_remain_usable():
    key = ed25519.Ed25519PrivateKey.generate()
    public = key.public_key()
    public.verify(key.sign(b"identity proof"), b"identity proof")
    with pytest.raises(cryptography.exceptions.InvalidSignature):
        public.verify(key.sign(b"identity proof"), b"altered proof")
    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = certificate("Ephemeral signing identity", signing_key)
    encryption = (
        serialization.PrivateFormat.PKCS12.encryption_builder()
        .kdf_rounds(50_000)
        .key_cert_algorithm(pkcs12.PBES.PBESv1SHA1And3KeyTripleDESCBC)
        .hmac_hash(hashes.SHA1())  # noqa: S303 - existing macOS Keychain import compatibility
        .build(b"test-only-password")
    )
    blob = pkcs12.serialize_key_and_certificates(b"test", signing_key, cert, None, encryption)
    restored_key, restored_cert, _ = pkcs12.load_key_and_certificates(blob, b"test-only-password")
    assert restored_key.public_key().public_numbers() == signing_key.public_key().public_numbers()
    assert restored_cert == cert
    with pytest.raises(ValueError):
        pkcs12.load_key_and_certificates(blob, b"wrong-password")


def test_encrypted_openssh_key_import_preserves_identity():
    key = asyncssh.generate_private_key("ssh-ed25519")
    encrypted = key.export_private_key("openssh", passphrase="test-only-password")  # noqa: S106
    assert (
        asyncssh.import_private_key(encrypted, "test-only-password").get_fingerprint()
        == key.get_fingerprint()
    )
    with pytest.raises(asyncssh.KeyEncryptionError):
        asyncssh.import_private_key(encrypted, "wrong-password")
