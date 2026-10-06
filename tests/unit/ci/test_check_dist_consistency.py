"""The dist-consistency gate: missing chunks and orphaned chunks."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.ci import check_dist_consistency as gate

A = gate.ASSETS_PREFIX


def _bundle() -> dict[str, str]:
    return {
        gate.ENTRY_HTML: '<script type="module" src="/assets/index-AAAAAAAA.js"></script>',
        A + "index-AAAAAAAA.js": 'import("./Lazy-BBBBBBBB.js");import "./style-CCCCCCCC.css";',
        A + "Lazy-BBBBBBBB.js": 'new URL("ort-wasm-DDDDDDDD.wasm", import.meta.url)',
        A + "style-CCCCCCCC.css": "body{}",
        A + "ort-wasm-DDDDDDDD.wasm": "",
        # An earlier build's chunks: nothing reachable names them.
        A + "Lazy-EEEEEEEE.js": 'import("./Old-FFFFFFFF.js")',
        A + "Old-FFFFFFFF.js": "",
        # Public assets and nested folders are not build output.
        A + "material-file-icons/file-GGGGGGGG.svg": "",
        A + "logo.png": "",
    }


def test_walk_reaches_the_live_graph_including_unprefixed_urls() -> None:
    files = _bundle()
    reachable, missing = gate._walk(set(files), files.__getitem__)
    assert not missing
    assert A + "ort-wasm-DDDDDDDD.wasm" in reachable
    assert A + "style-CCCCCCCC.css" in reachable


def test_orphans_are_only_unreached_generated_chunks() -> None:
    files = _bundle()
    reachable, _ = gate._walk(set(files), files.__getitem__)
    assert gate._orphans(files, reachable) == [
        A + "Lazy-EEEEEEEE.js",
        A + "Old-FFFFFFFF.js",
    ]


def test_missing_chunk_is_still_reported() -> None:
    files = _bundle()
    del files[A + "Lazy-BBBBBBBB.js"]
    _, missing = gate._walk(set(files), files.__getitem__)
    assert missing == {A + "Lazy-BBBBBBBB.js": A + "index-AAAAAAAA.js"}


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_prune_disk_removes_only_orphans(tmp_path: Path) -> None:
    files = _bundle()
    _write(tmp_path, files)
    assert gate._prune_disk(tmp_path) == 0
    left = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()}
    assert left == set(files) - {A + "Lazy-EEEEEEEE.js", A + "Old-FFFFFFFF.js"}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_staged_mode_blocks_added_orphans_and_prune_untracks_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    files = _bundle()
    _write(tmp_path, files)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "--", gate.DIST)
    monkeypatch.chdir(tmp_path)

    assert gate.main(["--staged"]) == 1
    assert "ORPHAN" in capsys.readouterr().out

    assert gate.main(["--staged", "--prune"]) == 0
    staged = set(_git(tmp_path, "ls-files", "--cached").split())
    assert A + "Lazy-EEEEEEEE.js" not in staged
    assert (tmp_path / A / "Lazy-EEEEEEEE.js").is_file()  # disk untouched
    assert gate.main(["--staged"]) == 0
