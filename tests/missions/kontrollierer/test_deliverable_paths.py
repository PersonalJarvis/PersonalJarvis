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
    find_generator_scripts,
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


# --- find_generator_scripts: the 2026-06-22 generator-script leak --------------
# Live forensic (mission_019ef099): the user asked by voice for "one HTML file"
# and got THREE deliverables — melbourne_guide.html PLUS its Python generator
# generate_guide.py (which embeds the whole HTML as a string literal and writes
# the sibling .html) PLUS a hero image. The user opened the .py and "only saw
# code". A generator/build script is process scratch, not the thing asked for.
# This filter drops it — but only when the document it emits SURVIVES, so a
# script the user actually requested is never removed.


def _reader(mapping: dict[str, str]):
    return lambda rel: mapping.get(rel, "")


def test_generator_script_emitting_sibling_html_is_detected() -> None:
    files = ["generate_guide.py", "melbourne_guide.html", "melbourne_hero.jpg"]
    text = {
        "generate_guide.py": (
            'html_content = """<!DOCTYPE html><html lang="de">...</html>"""\n'
            'with open("melbourne_guide.html", "w", encoding="utf-8") as f:\n'
            "    f.write(html_content)\n"
        ),
    }
    assert find_generator_scripts(files, _reader(text)) == frozenset(
        {"generate_guide.py"}
    )


def test_emitted_doc_and_its_asset_are_never_dropped() -> None:
    files = ["generate_guide.py", "melbourne_guide.html", "melbourne_hero.jpg"]
    text = {
        "generate_guide.py": (
            '<!DOCTYPE html>\nopen("melbourne_guide.html", "w").write(page)\n'
        )
    }
    gen = find_generator_scripts(files, _reader(text))
    assert "melbourne_guide.html" not in gen  # the real deliverable survives
    assert "melbourne_hero.jpg" not in gen  # its asset survives


def test_standalone_script_with_no_sibling_doc_is_kept() -> None:
    # The user asked FOR a script — nothing it emits is in the set, so it must
    # never be classified as scratch (the never-drop-a-deliverable guarantee).
    files = ["scraper.py", "requirements.txt"]
    text = {"scraper.py": 'import requests\nopen("out.txt", "w").write(r.text)\n'}
    assert find_generator_scripts(files, _reader(text)) == frozenset()


def test_script_only_reading_a_doc_is_not_a_generator() -> None:
    # Reading an HTML (mode 'r') is not generating it.
    files = ["lint.py", "page.html"]
    text = {"lint.py": 'open("page.html", "r").read()  # validate markup\n'}
    assert find_generator_scripts(files, _reader(text)) == frozenset()


def test_two_scripts_importing_each_other_are_kept() -> None:
    files = ["main.py", "utils.py"]
    text = {"main.py": "import utils\nutils.run()\n", "utils.py": "def run():\n    pass\n"}
    assert find_generator_scripts(files, _reader(text)) == frozenset()


def test_generator_detected_via_embedded_markup_literal() -> None:
    # A build.js that references index.html and embeds the doctype literal is a
    # generator even when the write call is obscured (template engine, etc.).
    files = ["build.js", "index.html"]
    text = {"build.js": "const page = `<!DOCTYPE html><html></html>`; // index.html\n"}
    assert find_generator_scripts(files, _reader(text)) == frozenset({"build.js"})


def test_no_document_in_set_means_no_generators() -> None:
    # Without any document target, there is nothing to generate -> empty result,
    # never leaving the user with nothing.
    files = ["only.py", "data.bin"]
    text = {"only.py": 'open("only.html", "w").write("<html>")'}
    assert find_generator_scripts(files, _reader(text)) == frozenset()


def test_windows_backslash_rels_are_handled() -> None:
    files = [r"src\gen.py", r"out\report.html"]
    text = {r"src\gen.py": '<!DOCTYPE html>\nopen("report.html", "w").write(x)\n'}
    assert find_generator_scripts(files, _reader(text)) == frozenset({r"src\gen.py"})
