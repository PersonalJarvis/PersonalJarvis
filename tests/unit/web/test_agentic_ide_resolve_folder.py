"""Turning a dropped folder into a path — a name is a name, not a substring.

A browser tells the page only what a dropped folder is CALLED. The route then
searches for that name, and the search also returns folders that merely contain
it — a cache directory named after a project path, say. Those are not rivals:
a folder called exactly what was dropped wins outright, and only several exact
matches are a real ambiguity worth asking about.
"""

from __future__ import annotations

import pytest

from jarvis.agentic_ide.folders import FolderEntry
from jarvis.ui.web import agentic_ide_routes as routes


def _entry(name: str, path: str) -> FolderEntry:
    return FolderEntry(name=name, path=path, is_project=True, is_repo=False)


async def test_one_exact_name_match_wins_over_folders_that_merely_contain_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hits = [
        _entry("shop", "/home/x/Desktop/shop"),
        _entry("C--Users-x-Desktop-shop", "/home/x/.cache/tool/C--Users-x-Desktop-shop"),
        _entry("shop-backup", "/home/x/old/shop-backup"),
    ]
    monkeypatch.setattr(routes, "search_folders", lambda *_a, **_k: hits)

    res = await routes.resolve_folder(routes.ResolveRequest(name="shop"))

    assert res.resolved == "/home/x/Desktop/shop"


async def test_several_exact_matches_are_offered_without_the_lookalikes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hits = [
        _entry("shop", "/home/x/a/shop"),
        _entry("Shop", "/home/x/b/Shop"),
        _entry("shop-old", "/home/x/c/shop-old"),
    ]
    monkeypatch.setattr(routes, "search_folders", lambda *_a, **_k: hits)

    res = await routes.resolve_folder(routes.ResolveRequest(name="shop"))

    assert res.resolved is None
    assert [c.path for c in res.candidates] == ["/home/x/a/shop", "/home/x/b/Shop"]
    assert "pick the right one" in res.detail


async def test_only_lookalikes_are_still_offered_as_a_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hits = [
        _entry("webshop", "/home/x/webshop"),
        _entry("shop-old", "/home/x/shop-old"),
    ]
    monkeypatch.setattr(routes, "search_folders", lambda *_a, **_k: hits)

    res = await routes.resolve_folder(routes.ResolveRequest(name="shop"))

    assert res.resolved is None
    assert len(res.candidates) == 2


async def test_a_real_path_needs_no_search(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The desktop shell reports the dropped folder's real path — that is used as
    is, and the name search never runs."""

    def _explode(*_a: object, **_k: object) -> None:
        raise AssertionError("a known path must not be searched for")

    monkeypatch.setattr(routes, "search_folders", _explode)

    res = await routes.resolve_folder(routes.ResolveRequest(path=str(tmp_path), name="x"))

    assert res.resolved == str(tmp_path)
