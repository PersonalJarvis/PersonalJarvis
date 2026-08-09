"""Checksum-guarded patch machinery for the managed local-realtime server.

The full pristine→patched round trip is exercised by the install engine's
smoke test against the real wheel; these tests pin the fail-closed contract
with synthetic files — the paths a drifted upstream or broken install must
never sneak through.
"""

from pathlib import Path

import pytest

from jarvis.realtime.local_server import patching


def _write_service(site: Path, data: bytes) -> Path:
    target = site / patching.SERVICE_RELPATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def _write_pocket_handler(site: Path, data: bytes) -> Path:
    target = site / patching.POCKET_HANDLER_RELPATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


class TestPatchState:
    def test_missing_file_reports_missing(self, tmp_path: Path) -> None:
        assert patching.patch_state(tmp_path) == "missing"

    def test_unknown_content_reports_drifted(self, tmp_path: Path) -> None:
        _write_service(tmp_path, b"def something_else(): ...\n")
        assert patching.patch_state(tmp_path) == "drifted"


class TestApplyFailClosed:
    def test_missing_target_raises_and_writes_nothing(self, tmp_path: Path) -> None:
        with pytest.raises(patching.PatchDriftError):
            patching.apply_create_response_patch(tmp_path)
        assert not (tmp_path / patching.SERVICE_RELPATH).exists()

    def test_drifted_target_raises_and_stays_untouched(self, tmp_path: Path) -> None:
        drifted = b"# some other upstream version\n"
        target = _write_service(tmp_path, drifted)
        with pytest.raises(patching.PatchDriftError):
            patching.apply_create_response_patch(tmp_path)
        assert target.read_bytes() == drifted


class TestUnifiedDiffApplier:
    def test_applies_insert_and_replace_hunks(self) -> None:
        original = "one\ntwo\nthree\nfour\n"
        diff = "--- a/f\n+++ b/f\n@@ -1,4 +1,5 @@\n one\n+inserted\n two\n-three\n+THREE\n four\n"
        assert patching._apply_unified_diff(original, diff) == "one\ninserted\ntwo\nTHREE\nfour\n"

    def test_context_mismatch_raises(self) -> None:
        diff = "--- a/f\n+++ b/f\n@@ -1,1 +1,1 @@\n-not there\n+x\n"
        with pytest.raises(patching.PatchDriftError):
            patching._apply_unified_diff("actual line\n", diff)


class TestVendoredArtifacts:
    def test_vendored_patch_file_ships_and_parses(self) -> None:
        raw = patching._PATCH_FILE.read_bytes().decode("utf-8")
        assert raw.startswith("--- a/speech_to_speech/")
        assert "create_response" in raw
        # The vendored diff must stay a single-file patch (one +++ header);
        # a multi-file diff would silently confuse the minimal applier.
        assert raw.count("+++ ") == 1

    def test_hashes_are_wired_to_the_pinned_version(self) -> None:
        assert patching.PATCH_TARGET_VERSION in patching._PATCH_FILE.name
        assert len(patching.PRISTINE_SHA256) == 64
        assert len(patching.PATCHED_SHA256) == 64
        assert patching.PRISTINE_SHA256 != patching.PATCHED_SHA256

    def test_pocket_language_patch_ships_with_byte_exact_hashes(self) -> None:
        raw = patching._POCKET_PATCH_FILE.read_bytes().decode("utf-8")
        assert raw.startswith("--- a/speech_to_speech/TTS/pocket_tts_handler.py")
        assert "TTSModel.load_model(language=language)" in raw
        assert len(patching.POCKET_HANDLER_PRISTINE_SHA256) == 64
        assert len(patching.POCKET_HANDLER_PATCHED_SHA256) == 64


def test_pocket_patch_applies_to_the_pinned_wheel_handler(tmp_path: Path) -> None:
    pristine = (
        Path(__file__).parents[4]
        / "data"
        / "local_realtime"
        / "venv"
        / "Lib"
        / "site-packages"
        / patching.POCKET_HANDLER_RELPATH
    )
    if not pristine.is_file():
        pytest.skip("the managed speech-to-speech wheel is not installed")
    target = _write_pocket_handler(tmp_path, pristine.read_bytes())

    assert patching.pocket_language_patch_state(tmp_path) == "pristine"
    assert patching.apply_pocket_language_patch(tmp_path) == "applied"
    assert patching.pocket_language_patch_state(tmp_path) == "patched"
    assert b"TTSModel.load_model(language=language)" in target.read_bytes()
