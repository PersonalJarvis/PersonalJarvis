"""Session-file permission contract (single-instance token hardening).

The session file carries the bearer token for the full control API; on POSIX
it must be owner-only (0600) — including when an older build left a
world-readable file behind. Windows relies on the per-user profile ACL, so
the mode-bit assertions are POSIX-only.
"""
from __future__ import annotations

import json
import os
import stat
import sys

import pytest

from jarvis.ui.shell.single_instance import SingleInstance


def test_write_session_roundtrip(tmp_path):
    si = SingleInstance(app_dir=tmp_path)
    si.write_session(port=1234, token="tok")
    data = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
    assert data["port"] == 1234
    assert data["token"] == "tok"
    assert data["pid"] == os.getpid()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits only")
def test_write_session_is_owner_only_on_posix(tmp_path):
    si = SingleInstance(app_dir=tmp_path)
    si.write_session(port=1234, token="tok")
    mode = stat.S_IMODE((tmp_path / "session.json").stat().st_mode)
    assert mode == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits only")
def test_write_session_repairs_preexisting_loose_permissions(tmp_path):
    loose = tmp_path / "session.json"
    loose.write_text("{}", encoding="utf-8")
    os.chmod(loose, 0o644)  # what older builds left behind
    SingleInstance(app_dir=tmp_path).write_session(port=1, token="t")
    assert stat.S_IMODE(loose.stat().st_mode) == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="flock is POSIX-only")
def test_posix_claim_is_exclusive_and_released(tmp_path):
    """The POSIX flock claim must actually enforce single-instance semantics:
    a second claim on the same app dir fails while the first is held and
    succeeds again after release()."""
    first = SingleInstance(app_dir=tmp_path).try_claim()
    assert first is not None

    # flock is per-(process, fd); a second fd in the SAME process still
    # observes the exclusivity because we use LOCK_NB on a fresh descriptor.
    second = SingleInstance(app_dir=tmp_path).try_claim()
    assert second is None

    first.release()
    third = SingleInstance(app_dir=tmp_path).try_claim()
    assert third is not None
    third.release()
