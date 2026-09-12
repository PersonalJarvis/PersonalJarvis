"""Privacy checks scan plaintext Blender content and fail closed on compression."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "gigi_source_metadata", ROOT / "scripts/art/gigi_source_metadata.py"
)
assert SPEC and SPEC.loader
metadata = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metadata)


@pytest.mark.parametrize("path", [b"C:/Users/private-fixture/", b"/home/private-fixture/"])
def test_rejects_profile_paths_even_in_unused_field_tails(path):
    with pytest.raises(ValueError, match="profile metadata") as failure:
        metadata.validate_saved_source(b"BLENDER" + b"//\x00" + path)
    assert "private-fixture" not in str(failure.value)


def test_rejects_compressed_bytes_instead_of_claiming_a_clean_plaintext_scan():
    with pytest.raises(ValueError, match="uncompressed"):
        metadata.validate_saved_source(b"\x28\xb5\x2f\xfdcompressed-source")


def test_actual_committed_source_is_uncompressed_and_has_no_profile_paths():
    raw = (ROOT / "art/studies/gigi-hover-companion/source/gigi.blend").read_bytes()
    assert metadata.validate_saved_source(raw)["validated_bytes"] == len(raw)


def test_authoring_fields_are_portable_and_reject_absolute_image_paths():
    assert (
        metadata.validate_source_path_records(
            [
                ("directory", b"//"),
                ("filename", ""),
                ("render", "//renders/"),
                ("image", "//materials/surface.png"),
            ]
        )["validated_fields"]
        == 4
    )
    with pytest.raises(ValueError, match="image"):
        metadata.validate_source_path_records([("image", "C:/Users/private-fixture/surface.png")])
