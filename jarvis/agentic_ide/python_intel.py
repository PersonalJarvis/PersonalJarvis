"""Python completions, hovers and go-to-definition for the code editor.

Jedi reads the editor's live buffer together with the workspace on disk, so a
completion after ``from jarvis.core import `` lists that package's names and a
definition can lead into another workspace file. It analyses statically —
nothing in the workspace is imported or run — and its helper process for
compiled modules starts without a console window on Windows.

Jedi is imported lazily: nothing here touches the boot path (AP-26), and a
machine without it answers with empty results instead of an error.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from loguru import logger

from .file_editing import EditError, _resolve

__all__ = ["complete", "definitions", "hover"]

#: Completions returned for one request; Monaco filters further as you type.
MAX_COMPLETIONS = 300
_HOVER_DOC_CHARS = 4000

_projects: dict[str, Any] = {}
_lock = threading.Lock()


def _jedi() -> Any | None:
    try:
        import jedi  # noqa: PLC0415 — lazy, see the module docstring
    except ImportError:
        logger.info("Python language support unavailable: jedi is not installed")
        return None
    return jedi


def _script(root: str | os.PathLike[str], path: str, text: str) -> tuple[Any, Path] | None:
    jedi = _jedi()
    if jedi is None:
        return None
    _, target = _resolve(root, path)
    base = Path(os.path.realpath(os.fspath(root)))
    key = str(base)
    with _lock:
        project = _projects.get(key)
        if project is None:
            # The workspace is the import root, so its own packages resolve.
            project = jedi.Project(path=str(base), added_sys_path=[str(base)])
            _projects[key] = project
    return jedi.Script(code=text, path=str(target), project=project), base


def _position(text: str, line: int, column: int) -> tuple[int, int]:
    """Monaco's 1-based line/column as jedi's 1-based line, 0-based column, clamped."""
    lines = text.splitlines() or [""]
    line = max(1, min(line, len(lines)))
    current = lines[line - 1] if line - 1 < len(lines) else ""
    return line, max(0, min(column - 1, len(current)))


def complete(
    root: str | os.PathLike[str], path: str, text: str, line: int, column: int
) -> list[dict[str, str]]:
    """Names that can follow the cursor."""
    built = _script(root, path, text)
    if built is None:
        return []
    script, _ = built
    try:
        found = script.complete(*_position(text, line, column))
    except Exception as exc:  # noqa: BLE001 — a completion must never fail the editor
        logger.debug("Python completion failed in {}: {}", path, exc)
        return []
    return [
        {"name": item.name, "type": item.type, "detail": item.description or ""}
        for item in found[:MAX_COMPLETIONS]
    ]


def hover(root: str | os.PathLike[str], path: str, text: str, line: int, column: int) -> str | None:
    """Markdown for the name under the cursor: its signature and docstring."""
    built = _script(root, path, text)
    if built is None:
        return None
    script, _ = built
    try:
        names = script.help(*_position(text, line, column))
    except Exception as exc:  # noqa: BLE001
        logger.debug("Python hover failed in {}: {}", path, exc)
        return None
    for name in names:
        signature = name.description or name.name
        try:
            # "shout(text: str) -> str" says more than "def shout".
            signatures = [sig.to_string() for sig in name.get_signatures()]
        except Exception:  # noqa: BLE001 — not every name has signatures
            signatures = []
        if signatures:
            prefix = "class " if name.type == "class" else "def " if name.type == "function" else ""
            signature = "\n".join(prefix + sig for sig in signatures)
        try:
            doc = name.docstring(raw=True) or ""
        except Exception:  # noqa: BLE001 — some compiled names have no source
            doc = ""
        body = f"```python\n{signature}\n```"
        if doc.strip():
            body += "\n\n" + doc.strip()[:_HOVER_DOC_CHARS]
        return body
    return None


def definitions(
    root: str | os.PathLike[str], path: str, text: str, line: int, column: int
) -> list[dict[str, object]]:
    """Where the name under the cursor is defined, inside this workspace only."""
    built = _script(root, path, text)
    if built is None:
        return []
    script, base = built
    try:
        found = script.goto(*_position(text, line, column), follow_imports=True)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Python go-to-definition failed in {}: {}", path, exc)
        return []
    results: list[dict[str, object]] = []
    for name in found:
        module = name.module_path
        if module is None or name.line is None:
            continue
        real = Path(os.path.realpath(module))
        try:
            relative = real.relative_to(base).as_posix()
        except ValueError:
            continue  # a library outside the workspace: nothing to open here
        try:
            _resolve(root, relative)
        except EditError:
            continue
        results.append({"path": relative, "line": name.line, "column": (name.column or 0) + 1})
    return results
