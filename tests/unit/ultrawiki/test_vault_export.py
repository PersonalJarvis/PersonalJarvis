"""Vault-export unit tests — the projection as a real Obsidian vault.

Filenames carry most of the risk here and all of the cross-platform risk. On
the reference corpus 14 of 947 topic labels contain a character that is
illegal or dangerous in a filename (``personaljarvis/personaljarvis``,
``win32/uia``, ``@eslint/plugin-kit``): written naively a slash silently
creates a subdirectory on POSIX and raises on Windows. Another 12 labels
differ only in case, which is the same file on Windows and macOS. Those cases
are tested through the pure naming function, so they are covered wherever CI
happens to run.

The second risk is destructive: the export deletes stale files. It may only
ever delete what it wrote itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.ultrawiki.projection import (
    ProjectedEntity,
    ProjectedMoment,
    WikiProjection,
)
from jarvis.ultrawiki.vault_export import (
    GENERATED_DIRS,
    GENERATED_MARKER,
    MANIFEST_NAME,
    USER_DIR,
    assign_note_names,
    export_vault,
    safe_note_name,
)


def entity(
    key: str,
    label: str,
    *,
    mentions: int = 2,
    first: str = "2026-03-01T10:00:00Z",
    last: str = "2026-04-01T10:00:00Z",
    neighbors: tuple[tuple[str, int], ...] = (),
) -> ProjectedEntity:
    return ProjectedEntity(
        key=key,
        label=label,
        mentions=mentions,
        item_ids=(1,) * mentions,
        first_seen=first,
        last_seen=last,
        neighbors=neighbors,
    )


def moment(
    document_id: int,
    title: str,
    *,
    entity_keys: tuple[str, ...] = (),
    timestamp: str = "2026-03-01T10:00:00Z",
    summary: str = "A summary.",
    resolution: str = "",
) -> ProjectedMoment:
    return ProjectedMoment(
        document_id=document_id,
        item_id=document_id * 10,
        title=title,
        summary=summary,
        resolution=resolution,
        entity_keys=entity_keys,
        timestamp_utc=timestamp,
        source_id="src1",
        source_label="Jarvis Conversations",
        permalink=f"app://item/{document_id}",
    )


def projection(
    entities: list[ProjectedEntity], moments: list[ProjectedMoment]
) -> WikiProjection:
    by_entity: dict[str, list[ProjectedMoment]] = {}
    for m in moments:
        for key in m.entity_keys:
            by_entity.setdefault(key, []).append(m)
    return WikiProjection(
        entities=tuple(entities),
        moments=tuple(moments),
        entity_by_key={e.key: e for e in entities},
        moments_by_entity={k: tuple(v) for k, v in by_entity.items()},
    )


TRIP = projection(
    [
        entity("bora bora", "Bora Bora", mentions=2, neighbors=(("tahiti", 2),)),
        entity("tahiti", "Tahiti", mentions=2, neighbors=(("bora bora", 2),)),
    ],
    [
        moment(1, "How do I get to Bora Bora?", entity_keys=("bora bora", "tahiti")),
        moment(
            2,
            "Which airline flies to Tahiti?",
            entity_keys=("bora bora", "tahiti"),
            timestamp="2026-04-01T10:00:00Z",
        ),
    ],
)


# ---------------------------------------------------------------------------
# Filenames — the cross-platform surface
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "forbidden"),
    [
        ("personaljarvis/personaljarvis", "/"),
        ("win32/uia", "/"),
        ("C:\\Windows", "\\"),
        ('a "quoted" name', '"'),
        ("what?", "?"),
        ("a*b", "*"),
        ("a<b>c", "<"),
        ("a|b", "|"),
        ("ratio 1:2", ":"),
    ],
)
def test_characters_that_break_a_path_are_replaced(label: str, forbidden: str):
    name = safe_note_name(label)
    assert forbidden not in name
    # And what comes out is a single path component, not a nested path.
    assert Path(name).name == name


def test_control_characters_are_replaced():
    assert "\n" not in safe_note_name("two\nlines")
    assert "\t" not in safe_note_name("a\tb")


def test_trailing_dots_and_spaces_are_stripped():
    # Windows silently drops them, which would break the orphan comparison:
    # the file on disk would no longer match the name we think we wrote.
    assert safe_note_name("version 1.") == "version 1"
    assert safe_note_name("spaced  ") == "spaced"


@pytest.mark.parametrize("reserved", ["CON", "PRN", "AUX", "NUL", "COM1", "LPT9", "con"])
def test_reserved_device_names_get_a_suffix(reserved: str):
    name = safe_note_name(reserved)
    assert name.lower() != reserved.lower()
    assert reserved.lower() in name.lower()


def test_long_labels_are_truncated_but_stay_distinct():
    base = "x" * 200
    a = safe_note_name(base + "aaa")
    b = safe_note_name(base + "bbb")

    assert len(a) <= 140
    assert a != b  # the trailing hash keeps a shared prefix from colliding


def test_a_label_that_sanitizes_to_nothing_still_gets_a_name():
    assert safe_note_name("///").strip() != ""


def test_names_are_composed_unicode():
    decomposed = "Cafe\u0301"
    assert safe_note_name(decomposed) == "Caf\u00e9"


def test_colliding_labels_get_deterministic_distinct_names():
    # Two labels that sanitize to the same string, plus a case pair that is one
    # file on Windows and macOS.
    names = assign_note_names(
        [
            entity("a/b", "A/B"),
            entity("a-b", "A-B"),
            entity("apple", "Apple"),
        ]
    )

    assert len(set(names.values())) == 3
    assert names == assign_note_names(
        [entity("a/b", "A/B"), entity("a-b", "A-B"), entity("apple", "Apple")]
    )


def test_names_never_collide_case_insensitively():
    names = assign_note_names([entity("x", "Report"), entity("y", "REPORT")])
    lowered = [name.lower() for name in names.values()]
    assert len(set(lowered)) == 2


# ---------------------------------------------------------------------------
# Vault shape
# ---------------------------------------------------------------------------


def test_export_writes_topics_moments_and_overview(tmp_path: Path):
    result = export_vault(TRIP, tmp_path / "vault")
    root = tmp_path / "vault"

    assert (root / "Topics" / "Bora Bora.md").exists()
    assert (root / "Moments" / "2026-03" / "How do I get to Bora Bora.md").exists()
    assert (root / "Overview" / "All topics.md").exists()
    assert (root / "Overview" / "Timeline.md").exists()
    assert (root / "Overview" / "Sources.md").exists()
    assert (root / "README.md").exists()
    assert result.topics == 2
    assert result.moments == 2


def test_the_user_directory_is_created_and_never_written_into(tmp_path: Path):
    root = tmp_path / "vault"
    export_vault(TRIP, root)
    mine = root / USER_DIR / "my thoughts.md"
    mine.write_text("Mine.", encoding="utf-8")

    export_vault(TRIP, root)

    assert mine.read_text(encoding="utf-8") == "Mine."


def test_a_topic_note_links_to_its_neighbours_and_moments(tmp_path: Path):
    export_vault(TRIP, tmp_path / "vault")
    text = (tmp_path / "vault" / "Topics" / "Bora Bora.md").read_text(encoding="utf-8")

    assert "[[Tahiti]]" in text
    assert "[[How do I get to Bora Bora]]" in text


def test_a_moment_note_links_its_topics_and_keeps_its_evidence_link(tmp_path: Path):
    export_vault(TRIP, tmp_path / "vault")
    text = (
        tmp_path / "vault" / "Moments" / "2026-03" / "How do I get to Bora Bora.md"
    ).read_text(encoding="utf-8")

    assert "[[Bora Bora]]" in text
    assert "app://item/1" in text


def test_every_generated_note_declares_that_it_is_generated(tmp_path: Path):
    root = tmp_path / "vault"
    export_vault(TRIP, root)

    for directory in GENERATED_DIRS:
        for note in (root / directory).rglob("*.md"):
            assert GENERATED_MARKER in note.read_text(encoding="utf-8"), note


def test_files_are_utf8_without_bom_and_lf(tmp_path: Path):
    root = tmp_path / "vault"
    export_vault(TRIP, root)
    raw = (root / "Topics" / "Bora Bora.md").read_bytes()

    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw


# ---------------------------------------------------------------------------
# Re-export: idempotent, and destructive only towards its own files
# ---------------------------------------------------------------------------


def test_re_export_produces_byte_identical_notes(tmp_path: Path):
    root = tmp_path / "vault"
    export_vault(TRIP, root)
    before = {
        path: path.read_bytes() for path in (root / "Topics").rglob("*.md")
    }

    export_vault(TRIP, root)

    after = {path: path.read_bytes() for path in (root / "Topics").rglob("*.md")}
    assert after == before


def test_a_topic_that_disappeared_is_removed(tmp_path: Path):
    root = tmp_path / "vault"
    export_vault(TRIP, root)
    assert (root / "Topics" / "Tahiti.md").exists()

    smaller = projection([TRIP.entities[0]], list(TRIP.moments))
    export_vault(smaller, root)

    assert not (root / "Topics" / "Tahiti.md").exists()
    assert (root / "Topics" / "Bora Bora.md").exists()


def test_a_hand_written_file_inside_a_generated_folder_survives(tmp_path: Path):
    root = tmp_path / "vault"
    export_vault(TRIP, root)
    stray = root / "Topics" / "my own note.md"
    stray.write_text("I wrote this.", encoding="utf-8")

    export_vault(TRIP, root)

    # Fail-closed: no generated marker means it is not ours to delete.
    assert stray.read_text(encoding="utf-8") == "I wrote this."


def test_a_re_export_does_not_read_the_notes_back(tmp_path: Path, monkeypatch):
    """Comparing by re-reading every note costs 2.5 ms per file on a machine
    with a virus scanner — 24 s for a corpus that changed nothing. The export
    remembers what it wrote instead."""
    root = tmp_path / "vault"
    export_vault(TRIP, root)

    reads: list[Path] = []
    original = Path.read_bytes

    def counting_read(self: Path) -> bytes:
        reads.append(self)
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", counting_read)
    export_vault(TRIP, root)

    assert [p for p in reads if p.suffix == ".md"] == []


def test_a_lost_manifest_still_prunes_correctly(tmp_path: Path):
    """The manifest is an optimisation, not the authority: without it the
    export falls back to the front-matter marker rather than pruning nothing
    (stale notes forever) or everything (someone else's files)."""
    root = tmp_path / "vault"
    export_vault(TRIP, root)
    stray = root / "Topics" / "mine.md"
    stray.write_text("Mine.", encoding="utf-8")
    (root / MANIFEST_NAME).unlink()

    export_vault(projection([TRIP.entities[0]], list(TRIP.moments)), root)

    assert not (root / "Topics" / "Tahiti.md").exists()
    assert stray.read_text(encoding="utf-8") == "Mine."


def test_a_note_deleted_by_hand_is_written_again(tmp_path: Path):
    root = tmp_path / "vault"
    export_vault(TRIP, root)
    (root / "Topics" / "Tahiti.md").unlink()

    export_vault(TRIP, root)

    assert (root / "Topics" / "Tahiti.md").exists()


def test_an_empty_projection_produces_a_vault_that_explains_itself(tmp_path: Path):
    root = tmp_path / "vault"

    result = export_vault(projection([], []), root)

    assert result.topics == 0
    assert (root / "README.md").exists()
    assert (root / USER_DIR).is_dir()
