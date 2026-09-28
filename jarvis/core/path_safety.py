"""Containment helpers for paths built from caller-supplied names.

Routes and stores often join a name that came from a request (an id, a slug,
a file name) onto a directory they own. A name like ``..``, ``..\\x``, an
absolute path or a Windows drive-relative name (``C:x``) would otherwise make
that join land outside the directory. These helpers resolve the joined path
and refuse it unless it stays inside the root.

The check is written as ``os.path.realpath`` followed by a ``startswith`` on
the resolved root so static analysers recognise it as a path sanitiser.
Stdlib only; identical behaviour on Windows, macOS and Linux.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

__all__ = ["UnsafePathError", "contained_path", "is_safe_name", "safe_child"]

#: One path component: letters, digits, dot, dash, underscore, space. It must
#: start with a letter or digit, so ``.``, ``..`` and hidden names never pass.
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\- ]{0,254}")


class UnsafePathError(ValueError):
    """A caller-supplied path would leave the directory it must stay in."""


def is_safe_name(name: str) -> bool:
    """True when *name* is a single, plain path component."""
    return isinstance(name, str) and _SAFE_NAME.fullmatch(name) is not None


def contained_path(root: str | os.PathLike[str], candidate: str | os.PathLike[str]) -> Path:
    """Resolve *candidate* (relative to *root* unless absolute) inside *root*.

    Returns the fully resolved path. Raises :class:`UnsafePathError` when the
    result is not *root* itself or a descendant of it, symlinks included.
    """
    real_root = os.path.realpath(os.fspath(root))
    real_target = os.path.realpath(os.path.join(real_root, os.fspath(candidate)))
    prefix = real_root if real_root.endswith(os.sep) else real_root + os.sep
    if real_target != real_root and not real_target.startswith(prefix):
        raise UnsafePathError(f"path escapes its directory: {os.fspath(candidate)!r}")
    return Path(real_target)


def safe_child(root: str | os.PathLike[str], name: str) -> Path:
    """``root / name`` for a single plain component *name*, contained in *root*.

    Raises :class:`UnsafePathError` when *name* is not a plain component
    (separators, ``..``, drive letters, leading dot) or resolves outside
    *root* (for instance through a symlink).
    """
    if not is_safe_name(name):
        raise UnsafePathError(f"not a plain file name: {name!r}")
    target = contained_path(root, name)
    if target == Path(os.path.realpath(os.fspath(root))):
        raise UnsafePathError(f"not a plain file name: {name!r}")
    return target
