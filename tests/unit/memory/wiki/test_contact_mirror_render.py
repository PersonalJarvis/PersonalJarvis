"""Rendering for the contact mirror: managed block, preservation, PII exclusion."""
from __future__ import annotations

from jarvis.contacts.store import ContactStore
from jarvis.memory.frontmatter import parse_frontmatter
from jarvis.memory.wiki.contact_mirror import (
    MANAGED_BLOCK_END,
    MANAGED_BLOCK_START,
    extract_managed_block,
    render_managed_block,
    render_person_page,
)


def _contact(tmp_path, **kwargs):
    store = ContactStore(base_dir=tmp_path)
    defaults = {"name": "Christoph Meyer"}
    defaults.update(kwargs)
    return store.put(**defaults)


def test_managed_block_contains_base_data(tmp_path):
    contact = _contact(
        tmp_path,
        aliases=["Chris", "Chrissi"],
        relationship="friend",
        note="Met at the climbing gym.",
    )
    block = render_managed_block(contact)
    assert block.startswith(MANAGED_BLOCK_START)
    assert block.endswith(MANAGED_BLOCK_END)
    for expected in ("Christoph Meyer", "Chris", "Chrissi", "friend", "climbing gym"):
        assert expected in block


def test_managed_block_excludes_pii(tmp_path):
    contact = _contact(
        tmp_path,
        emails=["christoph@example.com"],
        phones=["+49 151 2345678"],
        address={"street": "Musterweg 1", "city": "Berlin"},
    )
    page = render_person_page(contact, existing_text=None)
    assert "christoph@example.com" not in page
    assert "2345678" not in page
    assert "Musterweg" not in page
    assert "Berlin" not in page


def test_fresh_page_has_person_frontmatter(tmp_path):
    contact = _contact(tmp_path, relationship="colleague", aliases=["CM"])
    page = render_person_page(contact, existing_text=None)
    meta, _body = parse_frontmatter(page)
    assert meta["type"] == "person"
    assert meta["contact_slug"] == contact.slug
    assert meta["relationship"] == "colleague"
    assert meta["aliases"] == ["CM"]
    assert extract_managed_block(page) == render_managed_block(contact)


def test_resync_preserves_content_outside_block(tmp_path):
    contact = _contact(tmp_path, note="First note.")
    first = render_person_page(contact, existing_text=None)
    learned = "## Learned in conversation\n\n- Birthday is in August\n"
    edited = first + "\n" + learned
    contact2 = _contact(tmp_path, name="Christoph Meyer 2", note="Second note.")
    page = render_person_page(contact2, existing_text=edited)
    assert "Second note." in page
    assert "First note." not in page
    assert "Birthday is in August" in page


def test_resync_preserves_foreign_frontmatter_keys(tmp_path):
    contact = _contact(tmp_path)
    first = render_person_page(contact, existing_text=None)
    meta, body = parse_frontmatter(first)
    from jarvis.memory.frontmatter import write_frontmatter

    curated = write_frontmatter({**meta, "tags": ["vip"]}, body)
    page = render_person_page(contact, existing_text=curated)
    meta2, _ = parse_frontmatter(page)
    assert meta2.get("tags") == ["vip"]


def test_rendered_page_is_schema_valid(tmp_path):
    # The AtomicWriter validates every write via PageRepository.parse —
    # an invalid page is rolled back, so this is load-bearing, not cosmetic.
    from pathlib import Path

    from jarvis.memory.wiki.page import parse_markdown

    contact = _contact(tmp_path, relationship="friend")
    page = render_person_page(contact, existing_text=None)
    parsed = parse_markdown(page, Path("vault/people") / f"{contact.slug}.md")
    assert parsed.is_schema_valid


def test_page_without_markers_keeps_full_body(tmp_path):
    contact = _contact(tmp_path)
    legacy = "---\ntype: person\n---\n\nHand-written page about Christoph.\n"
    page = render_person_page(contact, existing_text=legacy)
    assert "Hand-written page about Christoph." in page
    assert extract_managed_block(page) == render_managed_block(contact)
