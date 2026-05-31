"""Unit-Tests fuer `jarvis.memory.workspace`.

Fokus:
- `Workspace.ensure` legt Default-Files an (USER.md, SOUL.md, people/).
- Idempotenz — zweiter `ensure`-Aufruf zerstoert nichts.
- `person_slug` normalisiert Namen zu safe Filenamen.
"""
from __future__ import annotations

from pathlib import Path

from jarvis.memory.workspace import (
    BOOTSTRAP_MD,
    PEOPLE_DIR,
    SOUL_MD,
    USER_MD,
    Workspace,
    person_slug,
)


# ======================================================================
# Workspace.ensure
# ======================================================================

class TestWorkspaceEnsure:
    """Default-Files werden angelegt; Idempotenz."""

    def test_creates_user_md_soul_md_and_people_dir(self, tmp_path: Path) -> None:
        ws = Workspace.ensure(tmp_path / "ws")

        assert (ws.root / USER_MD).exists(), "USER.md fehlt nach ensure"
        assert (ws.root / SOUL_MD).exists(), "SOUL.md fehlt nach ensure"
        assert (ws.root / PEOPLE_DIR).is_dir(), "people/ fehlt nach ensure"

    def test_creates_bootstrap_when_user_md_is_empty(self, tmp_path: Path) -> None:
        """BOOTSTRAP.md entsteht nur wenn USER.md anfangs leer ist (heuristic)."""
        root = tmp_path / "ws"
        root.mkdir()
        # Leeres USER.md vorbereiten → ensure soll BOOTSTRAP.md anlegen
        (root / USER_MD).write_text("", encoding="utf-8")

        Workspace.ensure(root)

        assert (root / BOOTSTRAP_MD).exists()

    def test_ensure_is_idempotent(self, tmp_path: Path) -> None:
        """Zweiter ensure-Aufruf ueberschreibt bestehende Files NICHT."""
        ws = Workspace.ensure(tmp_path / "ws")

        # User-Edit simulieren
        custom_content = "---\ncustom: true\n---\n\n# User hat eigene Notizen"
        ws.user_path.write_text(custom_content, encoding="utf-8")

        # Zweiter ensure darf den User-Edit nicht zerstoeren
        Workspace.ensure(ws.root)

        assert ws.user_path.read_text(encoding="utf-8") == custom_content

    def test_accepts_str_and_path(self, tmp_path: Path) -> None:
        """ensure akzeptiert sowohl str als auch Path."""
        ws1 = Workspace.ensure(str(tmp_path / "ws1"))
        ws2 = Workspace.ensure(tmp_path / "ws2")
        assert ws1.root.exists()
        assert ws2.root.exists()

    def test_paths_properties_are_consistent(self, tmp_path: Path) -> None:
        ws = Workspace.ensure(tmp_path / "ws")
        assert ws.user_path == ws.root / USER_MD
        assert ws.soul_path == ws.root / SOUL_MD
        assert ws.bootstrap_path == ws.root / BOOTSTRAP_MD
        assert ws.people_dir == ws.root / PEOPLE_DIR


# ======================================================================
# person_slug — die Filename-Normalisierung
# ======================================================================

class TestPersonSlug:
    """Slug-Regeln: lowercase, Umlaute expandiert, nicht-safe chars entfernt."""

    def test_basic_umlaut_expansion(self) -> None:
        assert person_slug("Laura Müller") == "laura_mueller"

    def test_dots_and_whitespace_stripped(self) -> None:
        assert person_slug("Dr. Paul O.") == "dr_paul_o"

    def test_hyphen_preserved(self) -> None:
        """Bindestriche bleiben, weil filename-safe."""
        assert person_slug("Anne-Marie") == "anne-marie"

    def test_special_characters_removed(self) -> None:
        # Slash, Backslash, Stern etc. sind NICHT safe auf Windows.
        assert person_slug("Foo/Bar*Baz?") == "foobarbaz"

    def test_empty_name_falls_back_to_unknown(self) -> None:
        """Kein Crash wenn der Name leer oder komplett whitespace ist."""
        assert person_slug("") == "unknown"
        assert person_slug("   ") == "unknown"
        # Nur-Sonderzeichen kollabiert auch zu 'unknown'
        assert person_slug("???") == "unknown"

    def test_leading_trailing_whitespace_stripped(self) -> None:
        assert person_slug("  the maintainer  ") == "the maintainer"

    def test_sharp_s_expanded_to_ss(self) -> None:
        assert person_slug("Weißbier") == "weissbier"

    def test_case_insensitive_result(self) -> None:
        assert person_slug("the maintainer") == "the maintainer"
        assert person_slug("the maintainer") == "the maintainer"


# ======================================================================
# is_bootstrap_needed
# ======================================================================

class TestBootstrapDetection:
    def test_bootstrap_needed_when_file_present(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        root.mkdir()
        # Leeres USER.md → ensure legt BOOTSTRAP.md an
        (root / USER_MD).write_text("", encoding="utf-8")
        ws = Workspace.ensure(root)
        assert ws.is_bootstrap_needed() is True

    def test_consume_bootstrap_removes_file(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        root.mkdir()
        (root / USER_MD).write_text("", encoding="utf-8")
        ws = Workspace.ensure(root)
        assert ws.bootstrap_path.exists()
        ws.consume_bootstrap()
        assert not ws.bootstrap_path.exists()
        # zweiter Aufruf wirft nicht
        ws.consume_bootstrap()
