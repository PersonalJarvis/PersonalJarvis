"""Unit tests for the shared deliverable-path predicate.

Single source of truth that tells a genuine worker deliverable from internal
tool-scratch in a mission's ``artifacts/files/`` subtree. The archive filter
(orchestrator), the Outputs-view listing + download guards (outputs_routes),
and the user-folder mirror (deliverable) all consume it — so this is the
anti-drift guard for "what is an output".

Live forensic 2026-06-21 (mission_019eeb34-bb67): a browser/QA worker launched
four headless Chrome instances with profiles under ``qa-artifacts/chrome-profile-*``
and the archive's ``--ignored`` enumeration union re-imported all 199 cache /
journal blobs (68 of them 0-byte) into ``artifacts/files/`` alongside the 2 real
deliverables, which the Outputs view (cap 200, sort-by-mtime) then buried. The
worker had correctly gitignored the profiles (``chrome-profile-*/``); the
denylist just didn't know browser profiles were scratch.
"""
from __future__ import annotations

import pytest

from jarvis.missions.kontrollierer.deliverable_paths import (
    is_nondeliverable_scratch,
)


# Real junk paths copied verbatim from mission_019eeb34-bb67's archive — every
# one of these must be recognised as scratch (NOT a deliverable).
_REAL_JUNK = [
    "qa-artifacts/chrome-profile-dd6355b81ddb49db87fc5045a7012b19/GrShaderCache/data_2",
    "qa-artifacts/chrome-profile-dd6355b81ddb49db87fc5045a7012b19/Default/Cache/Cache_Data/data_3",
    "qa-artifacts/chrome-profile-dd6355b81ddb49db87fc5045a7012b19/Default/Shared Dictionary/db-journal",
    "qa-artifacts/chrome-profile-dd6355b81ddb49db87fc5045a7012b19/Default/Web Data-journal",
    "qa-artifacts/chrome-profile-215c81b1bed945dbb720aeb3f32b38d3/Last Browser",
    "qa-artifacts/chrome-profile-215c81b1bed945dbb720aeb3f32b38d3/Variations",
    "qa-artifacts/chrome-profile-53f907ebce1e41fe82c76f8894c64cf0/first_party_sets.db-journal",
    "qa-artifacts/chrome-profile-2889ad51367a4d8c9ae31efac95bb17f/Crashpad/settings.dat",
]

# Real genuine deliverables from the SAME mission — every one must survive
# (False == "is a deliverable"). Note: melbourne-plan-render.png lives INSIDE
# qa-artifacts/ next to the junk, so we must exclude the chrome-profile subtrees
# WITHOUT excluding all of qa-artifacts/.
_REAL_DELIVERABLES = [
    "index.html",
    "scripts/qa.mjs",
    "qa-artifacts/melbourne-plan-render.png",
    "qa-artifacts/.gitignore",
]

# Legit names that merely resemble browser/cache nomenclature — must NOT be
# misclassified as scratch (false-positive guard; the fix must only ever
# *remove* genuine junk, never a real deliverable).
_LEGIT_LOOKALIKES = [
    "user-profile/index.tsx",       # a UI "user profile" component
    "src/profile.py",               # a module literally named profile
    "data/cache.json",              # a file named cache, not a cache dir
    "components/ProfileCard.tsx",
    "dist/app.js",                  # gitignored build output IS a deliverable
    "output.log",                   # gitignored log IS a deliverable
    "data_0",                       # a bare data_N file outside a profile
    "scoped_directory/main.py",     # 'scoped_dir' prefix but not a temp profile
]


@pytest.mark.parametrize("rel", _REAL_JUNK)
def test_real_chrome_profile_junk_is_scratch(rel: str) -> None:
    assert is_nondeliverable_scratch(rel) is True, rel


@pytest.mark.parametrize("rel", _REAL_DELIVERABLES)
def test_real_deliverables_survive(rel: str) -> None:
    assert is_nondeliverable_scratch(rel) is False, rel


@pytest.mark.parametrize("rel", _LEGIT_LOOKALIKES)
def test_legit_lookalikes_are_not_scratch(rel: str) -> None:
    assert is_nondeliverable_scratch(rel) is False, rel


def test_windows_backslash_paths_normalised() -> None:
    win = r"qa-artifacts\chrome-profile-deadbeef\GrShaderCache\data_2"
    assert is_nondeliverable_scratch(win) is True


def test_pre_existing_junk_dirs_still_filtered() -> None:
    # The dirs that were already in _JUNK_DIR_NAMES must keep being filtered.
    for rel in (
        ".git/config",
        "node_modules/react/index.js",
        "__pycache__/foo.pyc",
        ".venv/lib/site.py",
        "openclaw_state/workspace.json",
    ):
        assert is_nondeliverable_scratch(rel) is True, rel


def test_other_browser_profile_roots() -> None:
    # Profile roots named by other engines / automation harnesses are scratch
    # even when the inner cache-dir name is unconventional.
    for rel in (
        "chromium-profile-xyz/Local State",
        "puppeteer_dev_chrome_profile-abc/Default/Preferences",
        "edge-profile-1/Last Version",
        "tmp/chrome-user-data/Default/Cookies",
    ):
        assert is_nondeliverable_scratch(rel) is True, rel


def test_empty_and_root_paths() -> None:
    assert is_nondeliverable_scratch("") is False
    assert is_nondeliverable_scratch("/") is False
