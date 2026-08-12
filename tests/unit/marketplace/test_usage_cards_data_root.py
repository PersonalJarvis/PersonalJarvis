"""Usage cards from the ``data/usage_cards/`` second root (community installs)."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.marketplace.usage_cards import loader


@pytest.fixture()
def data_cards_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "usage_cards"
    monkeypatch.setattr(loader, "_DATA_CARDS_DIR", directory)
    loader.load_usage_card.cache_clear()
    yield directory
    loader.load_usage_card.cache_clear()


def test_card_loads_from_data_root(data_cards_dir: Path) -> None:
    data_cards_dir.mkdir(parents=True)
    (data_cards_dir / "todo-fox.md").write_text(
        "---\nkeywords: todo, fox\n---\nUse TodoFox for tasks.",
        encoding="utf-8",
    )
    card = loader.load_usage_card("todo-fox")
    assert card is not None
    assert card.keywords == ["todo", "fox"]
    assert "TodoFox" in card.body


def test_package_card_wins_over_data_card(data_cards_dir: Path) -> None:
    """A community card must never shadow a shipped plugin's keywords."""
    data_cards_dir.mkdir(parents=True)
    (data_cards_dir / "github.md").write_text(
        "---\nkeywords: hijacked\n---\nshadowed",
        encoding="utf-8",
    )
    card = loader.load_usage_card("github")
    assert card is not None
    assert "hijacked" not in card.keywords


def test_save_usage_card_is_atomic_and_visible(data_cards_dir: Path) -> None:
    path = loader.save_usage_card("todo-fox", "---\nkeywords: fox\n---\nBody")
    assert path.parent == data_cards_dir
    assert not list(data_cards_dir.glob("*.tmp"))
    card = loader.load_usage_card("todo-fox")
    assert card is not None
    assert card.keywords == ["fox"]


def test_save_rejects_traversal_ids(data_cards_dir: Path) -> None:
    for bad in ("../evil", "a/b", "a\\b", ""):
        with pytest.raises(ValueError, match="invalid plugin id"):
            loader.save_usage_card(bad, "body")


def test_delete_usage_card_idempotent(data_cards_dir: Path) -> None:
    loader.save_usage_card("todo-fox", "body")
    loader.delete_usage_card("todo-fox")
    assert loader.load_usage_card("todo-fox") is None
    loader.delete_usage_card("todo-fox")  # second delete: no error


def test_missing_card_still_none(data_cards_dir: Path) -> None:
    assert loader.load_usage_card("never-published") is None
