"""Bounded outlines of workspace files, for the prompt composer."""
from __future__ import annotations

from pathlib import Path

from jarvis.agentic_ide.code_skeleton import (
    MAX_CHARS_PER_FILE,
    skeleton_for,
    skeletons,
)

_PY = '''\
"""Ranking pipeline for the wiki."""
from __future__ import annotations

import math

CONSTANT = 3


class Ranker:
    """Fuses several ranked lists."""

    def fuse(self, lists: list[list[str]], k: int = 60) -> list[str]:
        """Reciprocal-rank fusion over ``lists``."""
        return []

    def _private_helper(self) -> None:
        pass


def rank_documents(query: str, limit: int = 10) -> list[str]:
    """Rank documents for ``query``."""
    body = query.strip()
    return [body][:limit]
'''


def test_python_skeleton_names_the_real_symbols(tmp_path: Path):
    (tmp_path / "pipeline.py").write_text(_PY, encoding="utf-8")

    out = skeleton_for(str(tmp_path), "pipeline.py")

    assert "Ranking pipeline for the wiki." in out
    assert "class Ranker" in out
    assert "Reciprocal-rank fusion" in out
    # The signature carries the name, every parameter with its annotation, and
    # the return type. Exact spacing is `ast.unparse`'s business, not ours.
    assert "def fuse(self, lists: list[list[str]], k: int" in out
    assert "60) -> list[str]" in out
    assert "def rank_documents(query: str, limit: int" in out
    assert "10) -> list[str]" in out
    # Bodies are what we are NOT paying tokens for.
    assert "return [body][:limit]" not in out


def test_broken_python_still_yields_something_useful(tmp_path: Path):
    (tmp_path / "broken.py").write_text(
        "def half_written(\n    # never closed\n", encoding="utf-8"
    )

    out = skeleton_for(str(tmp_path), "broken.py")

    assert "half_written" in out


def test_non_python_falls_back_to_signature_shaped_lines(tmp_path: Path):
    (tmp_path / "api.ts").write_text(
        "import { z } from 'zod'\n"
        "\n"
        "export interface Prompt { text: string }\n"
        "\n"
        "export async function sendPrompt(name: string): Promise<void> {\n"
        "  const body = JSON.stringify({ name })\n"
        "  await fetch('/api', { body })\n"
        "}\n",
        encoding="utf-8",
    )

    out = skeleton_for(str(tmp_path), "api.ts")

    assert "export interface Prompt" in out
    assert "export async function sendPrompt" in out
    assert "JSON.stringify" not in out


def test_a_missing_file_is_empty_not_an_error(tmp_path: Path):
    assert skeleton_for(str(tmp_path), "nope.py") == ""


def test_a_path_escaping_the_workspace_is_refused(tmp_path: Path):
    assert skeleton_for(str(tmp_path), "../outside.py") == ""
    assert skeleton_for(str(tmp_path), "/etc/passwd") == ""


def test_per_file_bound_is_enforced(tmp_path: Path):
    body = "\n".join(f"def function_{i}(argument: int) -> int: ..." for i in range(4000))
    (tmp_path / "huge.py").write_text(body, encoding="utf-8")

    out = skeleton_for(str(tmp_path), "huge.py")

    assert len(out) <= MAX_CHARS_PER_FILE


def test_skeletons_respects_the_file_count_and_total_bounds(tmp_path: Path):
    for i in range(6):
        (tmp_path / f"mod_{i}.py").write_text(
            f'"""Module {i}."""\n\n\ndef entry_{i}() -> None: ...\n', encoding="utf-8"
        )

    out = skeletons(
        str(tmp_path), [f"mod_{i}.py" for i in range(6)], max_files=3, max_total=10_000
    )

    assert len(out) == 3
    assert list(out) == ["mod_0.py", "mod_1.py", "mod_2.py"]
    assert sum(len(v) for v in out.values()) <= 10_000


def test_total_bound_stops_before_exceeding(tmp_path: Path):
    for i in range(3):
        (tmp_path / f"m{i}.py").write_text(
            '"""D."""\n\n\ndef f() -> None: ...\n' + ("# pad\n" * 200),
            encoding="utf-8",
        )

    out = skeletons(str(tmp_path), ["m0.py", "m1.py", "m2.py"], max_total=120)

    assert sum(len(v) for v in out.values()) <= 120


def test_undecodable_bytes_do_not_raise(tmp_path: Path):
    (tmp_path / "weird.py").write_bytes(b"def f():\n    x = '\xff\xfe'\n")

    assert isinstance(skeleton_for(str(tmp_path), "weird.py"), str)
