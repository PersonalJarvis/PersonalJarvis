"""``POST /folders/create`` — a refusal is an answer, not a status code.

The picker shows the reason next to the name field, the same way it shows a
typed path that does not exist, so the route reports through ``error`` and
never raises for an ordinary "no".
"""

from __future__ import annotations

from pathlib import Path

from jarvis.ui.web import agentic_ide_routes as routes


async def test_makes_the_folder_and_describes_it(tmp_path: Path) -> None:
    res = await routes.create_new_folder(
        routes.CreateFolderRequest(parent=str(tmp_path), name="shop")
    )

    assert res.error is None
    assert res.folder is not None
    assert res.folder.name == "shop"
    assert Path(res.folder.path) == tmp_path / "shop"
    assert (tmp_path / "shop").is_dir()


async def test_a_bad_name_comes_back_as_a_reason(tmp_path: Path) -> None:
    res = await routes.create_new_folder(
        routes.CreateFolderRequest(parent=str(tmp_path), name="a/b")
    )

    assert res.folder is None
    assert res.error is not None and "slashes" in res.error
    assert list(tmp_path.iterdir()) == []  # noqa: ASYNC240
