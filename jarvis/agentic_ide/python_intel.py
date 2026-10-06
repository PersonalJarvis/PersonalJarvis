"""Python completions, hovers and go-to-definition for the code editor.

Jedi reads the editor's live buffer together with the workspace on disk, so a
completion after ``from jarvis.core import `` lists that package's names and a
definition can lead into another workspace file. Workspace source is only
parsed, never imported or run; compiled extensions found in the workspace are
not loaded either (``load_unsafe_extensions=False``), so a cloned repository
cannot run native code through a completion. Jedi's helper process for the
interpreter's own compiled modules starts without a console window on Windows.

Every request runs on one dedicated worker thread: jedi's caches are not
thread-safe, and a burst of hovers must not occupy the shared thread pool that
saves and searches use. When requests pile up, the newest ones get an empty
answer at once instead of queueing behind stale ones.

Jedi is imported lazily: nothing here touches the boot path (AP-26), and a
machine without it answers with empty results instead of an error.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from loguru import logger

from .file_editing import EditError, _resolve

__all__ = ["complete", "definitions", "hover", "submit"]

#: Completions returned for one request; Monaco filters further as you type.
MAX_COMPLETIONS = 300
_HOVER_DOC_CHARS = 4000

#: Requests waiting or running before new ones are answered empty.
MAX_PENDING = 3
_LINE_BREAK = re.compile(r"\r\n|\r|\n")

_projects: dict[str, Any] = {}
_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="python-intel")
_pending = 0


def submit(fn: Callable[..., Any], *args: Any, empty: Any) -> Future[Any]:
    """Run one lookup on the worker thread, or answer ``empty`` when it is busy."""
    global _pending
    with _lock:
        if _pending >= MAX_PENDING:
            done: Future[Any] = Future()
            done.set_result(empty)
            return done
        _pending += 1

    def run() -> Any:
        global _pending
        try:
            return fn(*args)
        finally:
            with _lock:
                _pending -= 1

    return _executor.submit(run)


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
            # The workspace is the import root (smart_sys_path), so its own
            # packages resolve; its compiled extensions are never loaded.
            project = jedi.Project(path=str(base), load_unsafe_extensions=False)
            _projects[key] = project
    return jedi.Script(code=text, path=str(target), project=project), base


def _position(text: str, line: int, column: int) -> tuple[int, int]:
    """Monaco's 1-based line/column as jedi's 1-based line, 0-based column, clamped."""
    # Only CRLF, CR and LF end a line, as in the editor (splitlines() would
    # also split on form feeds and drop a trailing empty line).
    lines = _LINE_BREAK.split(text)
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
