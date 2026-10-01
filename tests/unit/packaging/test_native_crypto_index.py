"""Native artifacts are narrow additions, not a generic alternate-index waiver."""

import copy
import struct

import pytest

from scripts import native_crypto_index as index


@pytest.fixture
def spec(monkeypatch):
    manifest = {
        "schema": 1,
        "build_revision": 1,
        "cryptography": {"version": "50.0.2"},
        "artifacts": [],
    }
    for target, tag in index.PLATFORMS.items():
        filename = f"cryptography-50.0.2-cp311-abi3-{tag}.whl"
        manifest["artifacts"].append(
            {
                "target": target,
                "filename": filename,
                "sha256": "a" * 64,
                "url": f"{index.PUBLISH_ROOT}/50.0.2-1/files/{filename}",
            }
        )
    monkeypatch.setattr(index, "load_manifest", lambda: manifest)
    return manifest


def test_only_the_two_missing_wheel_architectures_use_the_index(spec):
    assert index.pip_options("darwin", "x86_64") == ["--extra-index-url", index.index_url()]
    assert index.pip_options("win32", "ARM64")
    assert index.pip_options("darwin", "arm64") == []
    assert index.pip_options("win32", "AMD64") == []
    assert index.pip_options("linux", "aarch64") == []


def test_native_artifact_requires_exact_identity_digest_and_target(spec):
    row = spec["artifacts"][0]
    assert index.is_native_artifact(row["url"], row["sha256"], row["target"], "3.11")
    assert not index.is_native_artifact(row["url"], "b" * 64, row["target"], "3.11")
    assert not index.is_native_artifact(row["url"], row["sha256"], "windows-arm64", "3.11")
    assert not index.is_native_artifact(row["url"], row["sha256"], row["target"], "3.10")
    assert not index.is_native_artifact(
        row["url"] + "?override=1", row["sha256"], row["target"], "3.11"
    )
    assert not index.is_native_artifact(
        row["url"] + "#sha256=wrong", row["sha256"], row["target"], "3.11"
    )


@pytest.mark.parametrize("field", ["filename", "url", "sha256"])
def test_altered_manifest_record_is_rejected(spec, field):
    bad = copy.deepcopy(spec)
    bad["artifacts"][0][field] = "unreviewed"
    with pytest.raises(ValueError):
        index.native_records(bad)


def test_missing_architecture_is_not_a_complete_publication(spec):
    spec["artifacts"].pop()
    with pytest.raises(ValueError, match="Both native"):
        index.native_records(spec)


def test_macho_binary_cannot_silently_raise_the_macos_floor():
    header = struct.pack("<8I", 0xFEEDFACF, 0x01000007, 3, 8, 1, 24, 0, 0)
    index.validate_macho(header + struct.pack("<6I", 0x32, 24, 1, 13 << 16, 15 << 16, 0))
    with pytest.raises(ValueError, match="macOS 13"):
        index.validate_macho(header + struct.pack("<6I", 0x32, 24, 1, 15 << 16, 15 << 16, 0))
    with pytest.raises(ValueError, match="extent"):
        index.validate_macho(header)
