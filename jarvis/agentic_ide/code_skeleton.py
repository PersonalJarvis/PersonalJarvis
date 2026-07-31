"""A dense outline of a source file, for the prompt composer to read.

The composer used to receive candidate file *paths* and nothing else, which is
why its prompts said "the ranking logic" where they could have said
``_fuse_ranked()``. Giving it the files themselves is the fix, but not
verbatim: a leading excerpt spends most of its tokens on imports and the first
function's body, and the interesting part of a module — what it offers and
what it is for — is spread across the whole file.

So what it gets is a skeleton: the module docstring, then every class and
function signature with the first line of its own docstring. That is roughly
the table of contents a developer reads first, at a fraction of the tokens.

Safety and bounds, because the user can open ANY folder:

* ``ast.parse`` never executes the file it parses. Nothing here imports,
  compiles, or runs workspace code.
* Paths that try to leave the workspace are refused outright, symlinks
  included.
* Every read is bounded (per file and in total) and every failure — missing,
  unreadable, undecodable, syntactically broken — degrades to a smaller
  result or an empty string, never to an exception. A missing skeleton costs
  prompt quality; a raised exception would cost the user their instruction.
"""
from __future__ import annotations

import ast
import re
from collections.abc import Sequence
from pathlib import Path

# Bounds. Raised from 3/6k/20k after the first live compositions came back at
# a third of the allowed prompt length: the writer can only describe what it
# was shown, so a thin outline budget caps prompt depth no matter how the
# instructions are worded. Five files covers a feature's surface rather than
# just its entry point, and the budgets still leave a frontier model's context
# far from the point where a provider would truncate.
MAX_SKELETON_FILES = 5
MAX_CHARS_PER_FILE = 9_000
MAX_CHARS_TOTAL = 34_000

# Never read more than this off disk before outlining — a "source file" in an
# arbitrary folder can be a 200 MB generated blob.
_MAX_SOURCE_BYTES = 400_000

# Lines that look like a declaration in a curly-brace or indentation language.
# Deliberately shallow: this is the fallback for everything Python's parser
# cannot read, and a wrong line costs one glance, not a failure.
_SIGNATURE_RE = re.compile(
    r"^\s{0,8}(?:export\s+)?(?:default\s+)?"
    r"(?:public\s+|private\s+|protected\s+|static\s+|abstract\s+|final\s+)*"
    r"(?:async\s+)?(?:"
    r"function\b|class\b|interface\b|type\b|enum\b|struct\b|trait\b|impl\b"
    r"|const\s+\w+\s*[:=]\s*(?:async\s*)?\(|fn\b|func\b|def\b|module\b"
    r"|(?:public|private|protected)\s+\w+\s+\w+\s*\("
    r")",
)


def _safe_path(root: str, rel: str) -> Path | None:
    """``rel`` resolved under ``root``, or None if it escapes the workspace."""
    if not rel or rel.startswith(("/", "\\")) or ":" in rel[:3]:
        return None
    if ".." in rel.replace("\\", "/").split("/"):
        return None
    try:
        base = Path(root).resolve()
        target = (base / rel).resolve()
    except (OSError, ValueError):
        return None
    # A symlink pointing outside the workspace is the remaining escape route.
    if base != target and base not in target.parents:
        return None
    return target


def _read(path: Path) -> str:
    """File text, or "" when it cannot be read. Never raises."""
    try:
        if not path.is_file() or path.stat().st_size > _MAX_SOURCE_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return ""


def _first_docstring_line(node: ast.AST) -> str:
    try:
        raw = ast.get_docstring(node)  # type: ignore[arg-type]
    except TypeError:
        return ""
    if not raw:
        return ""
    return raw.strip().splitlines()[0].strip()


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """The ``def ...`` header as written, collapsed onto one line."""
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    try:
        args = ast.unparse(node.args)
        returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    except Exception:  # noqa: BLE001 - unparse is best-effort decoration
        return f"{prefix} {node.name}(...)"
    return f"{prefix} {node.name}({args}){returns}"


def _python_skeleton(source: str) -> str:
    """Module docstring plus every top-level and class-level declaration."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return ""

    lines: list[str] = []
    module_doc = _first_docstring_line(tree)
    if module_doc:
        lines.append(f'"""{module_doc}"""')

    def emit(node: ast.AST, indent: str) -> None:
        if isinstance(node, ast.ClassDef):
            lines.append(f"{indent}class {node.name}:")
            doc = _first_docstring_line(node)
            if doc:
                lines.append(f'{indent}    """{doc}"""')
            for child in node.body:
                if isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    emit(child, indent + "    ")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lines.append(f"{indent}{_signature(node)}")
            doc = _first_docstring_line(node)
            if doc:
                lines.append(f'{indent}    """{doc}"""')

    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            emit(node, "")
        elif isinstance(node, ast.Assign):
            # Module-level constants carry real meaning in this codebase
            # (bounds, tier tables); their values do not.
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    lines.append(f"{target.id} = ...")

    return "\n".join(lines)


def _lexical_skeleton(source: str) -> str:
    """Declaration-shaped lines, for everything Python's parser cannot read."""
    out: list[str] = []
    for line in source.splitlines():
        if _SIGNATURE_RE.match(line):
            out.append(line.rstrip().rstrip("{").rstrip())
    return "\n".join(out)


def skeleton_for(root: str, rel: str, *, max_chars: int = MAX_CHARS_PER_FILE) -> str:
    """A bounded outline of ``rel`` under ``root``. "" when unavailable."""
    if max_chars <= 0:
        return ""
    path = _safe_path(root, rel)
    if path is None:
        return ""
    source = _read(path)
    if not source:
        return ""

    out = ""
    if path.suffix in {".py", ".pyi"}:
        out = _python_skeleton(source)
    if not out:
        out = _lexical_skeleton(source)
    if not out:
        # Neither parser found structure — a config file, a plain document.
        # Its opening is then the most informative thing about it.
        out = "\n".join(source.splitlines()[:40])

    return out[:max_chars]


def skeletons(
    root: str,
    rels: Sequence[str],
    *,
    max_files: int = MAX_SKELETON_FILES,
    max_total: int = MAX_CHARS_TOTAL,
) -> dict[str, str]:
    """Outlines for the first ``max_files`` readable paths, within ``max_total``.

    Ordered as given — the file index already ranks by relevance, and the
    budget should go to the best candidates.
    """
    out: dict[str, str] = {}
    spent = 0
    for rel in rels:
        if len(out) >= max_files or spent >= max_total:
            break
        text = skeleton_for(
            root, rel, max_chars=min(MAX_CHARS_PER_FILE, max_total - spent)
        )
        if not text:
            continue
        out[rel] = text
        spent += len(text)
    return out


__all__ = [
    "MAX_CHARS_PER_FILE",
    "MAX_CHARS_TOTAL",
    "MAX_SKELETON_FILES",
    "skeleton_for",
    "skeletons",
]
