"""The release's one signature authorizes installers on every supported OS."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from jarvis.core.installer_update import (
    CHECKSUMS_ASSET_NAME,
    CHECKSUMS_SIGNATURE_ASSET_NAME,
    InstallerAsset,
    InstallerUpdateError,
    download_and_verify,
    installer_asset_name,
)
from tests.fakes.fake_installer_update import FakeAssetFetcher


def _asset(name: str) -> InstallerAsset:
    return InstallerAsset(name, f"https://example.invalid/{name}", 0)


@pytest.mark.parametrize(
    ("platform_name", "machine"),
    [("win32", "AMD64"), ("darwin", "arm64"), ("darwin", "x86_64"), ("linux", "x86_64")],
)
async def test_signed_manifest_rejects_a_replaced_installer_on_every_os(
    tmp_path: Path, platform_name: str, machine: str
) -> None:
    name = installer_asset_name(platform_name, machine)
    assert name is not None
    authentic = b"approved release installer"
    key = Ed25519PrivateKey.generate()
    manifest = (
        f"# release: v1.6.0\n{hashlib.sha256(authentic).hexdigest()}  {name}\n"
    )
    fetcher = FakeAssetFetcher(
        manifest=manifest,
        signature=base64.b64encode(key.sign(manifest.encode())),
        payload=b"replaced installer",
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )

    with pytest.raises(InstallerUpdateError, match="SHA-256"):
        await download_and_verify(
            _asset(name), _asset(CHECKSUMS_ASSET_NAME),
            _asset(CHECKSUMS_SIGNATURE_ASSET_NAME), release_tag="v1.6.0",
            dest_dir=tmp_path, fetcher=fetcher, public_key_pem=public,
        )
    assert not (tmp_path / name).exists()
