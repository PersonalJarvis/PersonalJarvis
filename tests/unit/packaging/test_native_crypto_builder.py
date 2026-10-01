"""The publisher rejects altered sources and native library leakage."""

import hashlib
import io

import pytest

from scripts import build_native_crypto as builder


def test_source_digest_is_checked_before_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(builder.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"changed"))
    target = tmp_path / "source.tar.gz"
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        builder.download_verified("https://example.invalid/source", "0" * 64, target)
    assert not target.exists()


def test_verified_source_is_retained(tmp_path, monkeypatch):
    content = b"verified source"
    monkeypatch.setattr(builder.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(content))
    target = tmp_path / "source.tar.gz"
    builder.download_verified(
        "https://example.invalid/source", hashlib.sha256(content).hexdigest(), target
    )
    assert target.read_bytes() == content


def test_plaintext_and_local_downloads_are_rejected(tmp_path):
    for url in ("http://example.invalid/source", "file:///source.tar.gz"):
        with pytest.raises(ValueError, match="HTTPS"):
            builder.download_verified(url, "0" * 64, tmp_path / "source")


def test_windows_wheel_cannot_depend_on_builder_openssl():
    assert builder.windows_external_libraries(
        "  python3.dll\n  KERNEL32.dll\n  bcryptprimitives.dll\n  ntdll.dll\n"
    ) == []
    assert builder.windows_external_libraries("  python3.dll\n  libcrypto-4-arm64.dll\n") == [
        "libcrypto-4-arm64.dll"
    ]
