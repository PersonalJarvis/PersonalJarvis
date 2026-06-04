"""Tests for the CI 'no new German' language-policy gate.

The gate lives in scripts/ci/ (not an importable package), so we add that
directory to sys.path before importing — mirroring how CI runs the scripts
directly with `python scripts/ci/<name>.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_CI = Path(__file__).resolve().parents[3] / "scripts" / "ci"
sys.path.insert(0, str(_SCRIPTS_CI))

import check_no_new_german as gate  # noqa: E402
from _german_detect import looks_german  # noqa: E402


# --------------------------------------------------------------------------- #
# looks_german
# --------------------------------------------------------------------------- #
class TestLooksGerman:
    def test_umlaut_flags(self):
        assert looks_german("Datei konnte nicht geöffnet werden")
        assert looks_german("Größe")

    def test_strong_single_token_flags(self):
        assert looks_german("Das war ein Fehler beim Speichern")
        assert looks_german("Bitte Einstellungen aktualisieren")
        assert looks_german("Verbindung getrennt")

    def test_ascii_transliteration_flags(self):
        # de-umlauted German must still be caught
        assert looks_german("Aktion konnte nicht ausgefuehrt werden")
        assert looks_german("Bitte waehle eine Sprache fuer die Ausgabe")

    def test_two_weak_tokens_flag(self):
        assert looks_german("der und das")

    def test_plain_english_is_clean(self):
        assert not looks_german("Could not open the file for writing")
        assert not looks_german("Save the current settings and continue")
        assert not looks_german("Connection closed by remote host")

    def test_english_code_is_clean(self):
        assert not looks_german('logger.info("retry the request after backoff")')
        assert not looks_german("def render_user_settings(order, header):")
        assert not looks_german("const isReady = await provider.connect()")

    def test_single_english_false_friend_is_clean(self):
        # "die" / "war" alone (one weak hit) must not trip the gate
        assert not looks_german("the process may die on signal")
        assert not looks_german("the war room dashboard")

    def test_empty_and_punctuation(self):
        assert not looks_german("")
        assert not looks_german("=== >>> --- 123 {}();")


# --------------------------------------------------------------------------- #
# allowlist + extension filtering
# --------------------------------------------------------------------------- #
class TestAllowlist:
    patterns = [
        "jarvis/brain/JARVIS_PERSONA.md",
        "scripts/bulk_translate*.py",
        "*/i18n/*",
        "wiki/obsidian-vault/*",
        "*.de.json",
    ]

    def test_exact_match(self):
        assert gate.is_allowlisted("jarvis/brain/JARVIS_PERSONA.md", self.patterns)

    def test_glob_prefix(self):
        assert gate.is_allowlisted("scripts/bulk_translate_2.py", self.patterns)

    def test_subtree_match(self):
        assert gate.is_allowlisted(
            "jarvis/ui/web/frontend/src/i18n/de.ts", self.patterns
        )
        assert gate.is_allowlisted(
            "wiki/obsidian-vault/sessions/2026-05-28-x.md", self.patterns
        )

    def test_backslash_normalised(self):
        assert gate.is_allowlisted("jarvis\\brain\\JARVIS_PERSONA.md", self.patterns)

    def test_non_allowlisted(self):
        assert not gate.is_allowlisted("jarvis/brain/manager.py", self.patterns)

    def test_real_allowlist_file_loads(self):
        patterns = gate.load_allowlist()
        assert patterns, "the shipped allowlist should not be empty"
        assert gate.is_allowlisted("wiki/obsidian-vault/x.md", patterns)


class TestScannedExtensions:
    def test_text_types_scanned(self):
        assert gate.is_scanned("jarvis/foo.py")
        assert gate.is_scanned("docs/readme.md")
        assert gate.is_scanned("ui/Component.tsx")

    def test_binaries_skipped(self):
        assert not gate.is_scanned("assets/logo.png")
        assert not gate.is_scanned("video/promo.mp4")
        assert not gate.is_scanned("data/model.onnx")


# --------------------------------------------------------------------------- #
# diff parsing
# --------------------------------------------------------------------------- #
DIFF = """\
diff --git a/jarvis/foo.py b/jarvis/foo.py
index 1111111..2222222 100644
--- a/jarvis/foo.py
+++ b/jarvis/foo.py
@@ -10,0 +11,2 @@ def foo():
+    raise ValueError("Datei wurde nicht gefunden")
+    raise ValueError("file not found")
diff --git a/assets/old.svg b/assets/old.svg
deleted file mode 100644
index 3333333..0000000
--- a/assets/old.svg
+++ /dev/null
@@ -1 +0,0 @@
-<svg>Größe</svg>
"""


class TestParseAddedLines:
    def test_extracts_added_with_paths_and_linenos(self):
        added = gate.parse_added_lines(DIFF)
        assert ("jarvis/foo.py", 11, '    raise ValueError("Datei wurde nicht gefunden")') in added
        assert ("jarvis/foo.py", 12, '    raise ValueError("file not found")') in added

    def test_deletions_to_devnull_ignored(self):
        added = gate.parse_added_lines(DIFF)
        # the removed German "<svg>Größe</svg>" must NOT appear
        assert all("Größe" not in text for _, _, text in added)


# --------------------------------------------------------------------------- #
# end-to-end violation finding
# --------------------------------------------------------------------------- #
class TestFindViolations:
    def test_flags_new_german_skips_english(self):
        added = gate.parse_added_lines(DIFF)
        violations = gate.find_violations(added, patterns=[])
        assert len(violations) == 1
        assert violations[0][0] == "jarvis/foo.py"
        assert violations[0][1] == 11

    def test_allowlisted_path_is_exempt(self):
        added = [("jarvis/brain/JARVIS_PERSONA.md", 5, "Du bist ein Butler und hilfst")]
        assert gate.find_violations(added, ["jarvis/brain/JARVIS_PERSONA.md"]) == []

    def test_inline_escape_is_exempt(self):
        added = [("jarvis/x.py", 5, 'TEST = "Datei nicht gefunden"  # i18n-allow')]
        assert gate.find_violations(added, []) == []

    def test_non_text_file_is_skipped(self):
        added = [("assets/x.svg", 1, "<text>Größe und Höhe</text>")]
        assert gate.find_violations(added, []) == []
