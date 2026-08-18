"""Browsing folders by a typed path — the answer is the folder, not the route.

The path field reads like ``cd``, so what arrives here can be ``~/code`` or
``projects/..``. The listing itself already coped; the PATH handed back is what
becomes the workspace, so it must be the folder's own name — home spelled out,
``..`` folded away — never the text someone happened to type.
"""

from __future__ import annotations

from pathlib import Path

from jarvis.ui.web import agentic_ide_routes as routes


async def test_a_typed_dot_dot_folds_away(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()

    res = await routes.get_folders(path=str(tmp_path / "sub" / ".."))

    assert res.error is None
    assert res.path == str(tmp_path)


async def test_a_typed_tilde_becomes_the_home_folder() -> None:
    res = await routes.get_folders(path="~")

    assert res.path == str(Path.home())


async def test_a_path_that_is_not_a_folder_is_reported_not_listed(tmp_path: Path) -> None:
    res = await routes.get_folders(path=str(tmp_path / "cd haral"))

    assert res.error and "Not a folder" in res.error
    assert res.entries == []
