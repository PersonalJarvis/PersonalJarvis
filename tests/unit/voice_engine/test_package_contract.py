"""The engine package stays installable into its own environment."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import jarvis.voice_engine as engine
from jarvis.voice_engine import models

PACKAGE = Path(engine.__file__).parent
_HEAVY = {"onnxruntime", "sherpa_onnx", "torch", "pocket_tts", "faster_qwen3_tts", "mlx", "soxr",
          "psutil"}


def _imports(path: Path) -> list[tuple[str, bool]]:
    """(module, imported at module scope) for every import in a file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    top = {id(node) for node in tree.body}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(alias.name, id(node) in top) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.module, id(node) in top))
    return found


def test_engine_imports_nothing_from_the_app() -> None:
    offenders = [
        (path.name, module)
        for path in PACKAGE.rglob("*.py")
        for module, _ in _imports(path)
        if module.startswith("jarvis.") and not module.startswith("jarvis.voice_engine")
    ]
    assert offenders == []


def test_model_runtimes_are_imported_lazily() -> None:
    offenders = [
        (path.name, module)
        for path in PACKAGE.rglob("*.py")
        for module, at_top in _imports(path)
        if at_top and module.split(".")[0] in _HEAVY
    ]
    assert offenders == []


def test_registry_sources_are_pinned() -> None:
    for source in models.REGISTRY.values():
        assert source.url.startswith("https://")
        assert re.fullmatch(r"[0-9a-f]{64}", source.sha256), source.name
        assert source.size > 0
        assert bool(source.unpacks_to) == source.is_archive


def test_model_paths_live_under_the_given_root(tmp_path: Path) -> None:
    for name, source in models.REGISTRY.items():
        path = models.model_path(name, tmp_path)
        assert tmp_path in path.parents
        assert not models.is_present(name, tmp_path)
        assert path.name == (source.unpacks_to or source.filename)
