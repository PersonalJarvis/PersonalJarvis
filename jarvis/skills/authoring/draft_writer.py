"""Writes skill drafts into the user-skills directory with forced state=draft.

Plan-§AD-8: `draft_writer` forces `state=draft`, even if Jarvis-Agent-Author
incorrectly sets `state=active` in the frontmatter. Plan-§AP-6: no
auto-activation — the user must explicitly promote via the UI / CLI.

Plan-§AP-10: drafts land ONLY in `user_skills_dir` (otherwise a hot-reload
bypass). Path-traversal protection before every write attempt via
`Path.resolve().relative_to(user_skills_dir.resolve())`.
"""
from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml

from jarvis.core.paths import user_skills_dir

from .schema import SkillDraft

_LOG = logging.getLogger(__name__)

_SKILL_FILENAME: Final[str] = "SKILL.md"
_SAFE_IMPORTS_FILE: Final[Path] = (
    Path(__file__).resolve().parents[1] / "safe_imports.txt"
)


class SlugError(ValueError):
    """Slug is invalid or a path-traversal attempt."""


class UnsafeSkillError(RuntimeError):
    """Skill body contains forbidden code (Plan-§7.5 security lint)."""


@dataclass(frozen=True)
class DraftWriteResult:
    """Result of a draft write."""

    slug: str
    draft_path: Path
    forced_state_override: bool


# ----------------------------------------------------------------------
# Slug safety
# ----------------------------------------------------------------------


def _resolve_user_skills_root() -> Path:
    return user_skills_dir().resolve()


def _resolved_target(slug: str, root: Path | None = None) -> Path:
    """Resolves the skill folder under user_skills_dir/<slug>/.

    Guarantees via `Path.resolve().relative_to(root)` that the result
    does NOT escape the user-skills directory — even if the
    slug validator were bypassed.
    """
    base = root.resolve() if root is not None else _resolve_user_skills_root()
    candidate = (base / slug).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise SlugError(
            f"Slug '{slug}' resolves outside user_skills_dir ({base})"
        ) from exc
    return candidate


def _resolve_clash_safe_slug(
    slug: str, base: Path | None = None
) -> str:
    """If `<slug>` already exists, append the suffix `_2`, `_3`, ...

    Plan-§7.5 pipeline step 2: clash check, no overwrite.
    """
    base_dir = base if base is not None else _resolve_user_skills_root()
    candidate_path = base_dir / slug
    if not candidate_path.exists():
        return slug
    for n in range(2, 100):
        new_slug = f"{slug}_{n}"
        if not (base_dir / new_slug).exists():
            return new_slug
    raise SlugError(f"Could not find a clash-free slug for '{slug}' after 99 attempts")


# ----------------------------------------------------------------------
# Security lint
# ----------------------------------------------------------------------


_FORBIDDEN_CALLS: Final[frozenset[str]] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "globals",
        "locals",
        # Jarvis-Agent-review-MAJOR (Phase 7.5): reflective bypass via getattr/vars.
        "getattr",
        "vars",
        "setattr",
    }
)
_FORBIDDEN_ATTRIBUTES: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("os", "system"),
        ("os", "popen"),
        ("os", "execvp"),
        ("os", "execv"),
        ("os", "execlp"),
        ("os", "execle"),
        ("os", "spawnvp"),
        ("os", "spawnv"),
        ("os", "fork"),
        ("subprocess", "Popen"),
        ("subprocess", "call"),
        ("subprocess", "run"),
        ("subprocess", "check_call"),
        ("subprocess", "check_output"),
        ("subprocess", "getoutput"),
        # Defense-in-depth (Jarvis-Agent-review-MAJOR): if `builtins`
        # or `importlib` ever lands in safe_imports, the recursion vectors
        # are explicitly blocked.
        ("builtins", "eval"),
        ("builtins", "exec"),
        ("builtins", "__import__"),
        ("importlib", "import_module"),
    }
)
# Jarvis-Agent-review-MAJOR: cast the codefence regex broadly — all language
# tags, incl. `python3`/`pycon`/`ipython`/empty. Plus the `~~~` tilde variant.
_CODE_BLOCK_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:```|~~~)([a-zA-Z0-9_+-]*)\s*\n(.*?)(?:```|~~~)",
    re.DOTALL,
)
_PYTHON_LANG_TAGS: Final[frozenset[str]] = frozenset(
    {"", "python", "py", "python3", "pycon", "ipython", "python2"}
)


def _load_safe_imports() -> frozenset[str]:
    """Reads the allowlist from `safe_imports.txt`. Error → empty list
    (failsafe: on a corrupt file the lint rejects all imports).
    """
    try:
        text = _SAFE_IMPORTS_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        _LOG.warning("safe_imports.txt not readable: %s", exc)
        return frozenset()
    allowed: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Top-level module (before the first dot)
        allowed.add(stripped.split(".", 1)[0])
    return frozenset(allowed)


def _walk_python_block(code: str, allowed_imports: frozenset[str]) -> list[str]:
    """Inspects a Python block via AST. Returns a list of findings.

    Jarvis-Agent-review-MAJOR hardening (Phase 7.5):
    - `ast.Subscript` calls (`__builtins__["eval"](...)`)
    - reflective imports (`getattr(os, "system")`)
    """
    findings: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        findings.append(f"syntax_error: {exc.msg}")
        return findings

    for node in ast.walk(tree):
        # Imports — allowlist only
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".", 1)[0]
                if top not in allowed_imports:
                    findings.append(f"forbidden_import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".", 1)[0]
            if top and top not in allowed_imports:
                findings.append(f"forbidden_import_from: {node.module}")

        # Calls — direct names, attribute pairs, and subscript bypass
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _FORBIDDEN_CALLS:
                findings.append(f"forbidden_call: {func.id}")
            elif isinstance(func, ast.Attribute):
                if isinstance(func.value, ast.Name):
                    pair = (func.value.id, func.attr)
                    if pair in _FORBIDDEN_ATTRIBUTES:
                        findings.append(f"forbidden_call: {pair[0]}.{pair[1]}")
                    if (
                        pair[0] == "subprocess"
                        and any(
                            isinstance(kw, ast.keyword)
                            and kw.arg == "shell"
                            and isinstance(kw.value, ast.Constant)
                            and kw.value.value is True
                            for kw in node.keywords
                        )
                    ):
                        findings.append(
                            f"subprocess_shell_true: {pair[0]}.{pair[1]}"
                        )
                # `builtins.eval(...)` as attribute-of-attribute: __builtins__.eval
                elif (
                    isinstance(func.value, ast.Attribute)
                    and isinstance(func.value.value, ast.Name)
                ):
                    if (
                        func.value.attr == "__builtins__"
                        or func.value.value.id == "__builtins__"
                    ):
                        findings.append(f"forbidden_builtins_access: {func.attr}")
            # Subscript bypass: __builtins__["eval"](), globals()["eval"]()
            elif isinstance(func, ast.Subscript):
                base = func.value
                if isinstance(base, ast.Name) and base.id in (
                    "__builtins__", "globals", "locals", "vars"
                ):
                    findings.append(f"forbidden_subscript_call: {base.id}[...]")
                elif isinstance(base, ast.Call):
                    inner = base.func
                    if (
                        isinstance(inner, ast.Name)
                        and inner.id in ("globals", "locals", "vars")
                    ):
                        findings.append(
                            f"forbidden_subscript_call: {inner.id}()[...]"
                        )
    return findings


def _block_looks_like_python(code: str) -> bool:
    """Heuristic: is the code-block content a parsable Python snippet?

    Without a language tag this is the only way to know whether we
    should AST-parse it. `ast.parse` tolerates empty snippets — we
    additionally check that at least one Python statement token occurs.
    """
    if not code.strip():
        return False
    try:
        ast.parse(code)
    except SyntaxError:
        return False
    return True


def safe_lint_skill_body(body: str) -> list[str]:
    """Inspects all code blocks in the skill body.

    Jarvis-Agent-review-MAJOR (Phase 7.5): the codefence language is no
    longer restricted to `python|py`. All tags + tilde fences are scanned;
    on an unknown/empty tag, `ast.parse` heuristically detects
    whether it's Python code.
    """
    allowed = _load_safe_imports()
    findings: list[str] = []
    for match in _CODE_BLOCK_RE.finditer(body):
        lang = (match.group(1) or "").lower()
        code = match.group(2)
        if lang in _PYTHON_LANG_TAGS:
            if not lang and not _block_looks_like_python(code):
                # Empty tag + not Python → ignore (e.g. a plain-text block)
                continue
            findings.extend(_walk_python_block(code, allowed))
    return findings


# ----------------------------------------------------------------------
# Render + Write
# ----------------------------------------------------------------------


def _render_skill_md(draft: SkillDraft) -> str:
    """Renders SKILL.md with frontmatter + body. `state` is ALWAYS `draft`."""
    triggers_parsed: list = []
    if draft.triggers_yaml.strip():
        try:
            parsed = yaml.safe_load(draft.triggers_yaml)
            if isinstance(parsed, list):
                triggers_parsed = parsed
        except yaml.YAMLError as exc:
            _LOG.warning("triggers_yaml parse failed for %s: %s", draft.slug, exc)
    frontmatter: dict = {
        "schema_version": "1",
        "name": draft.name,
        "version": "0.1.0",
        "description": draft.description,
        "category": draft.category,
        "tags": ["jarvis-agent-authored"],
        "author": "jarvis-agent",
        "license": "MIT",
        "triggers": triggers_parsed,
        "requires_tools": draft.requires_tools,
        # **HARDCODED**: Plan-§AD-8 — the Jarvis-Agent-Author output `state` is discarded.
        "state": "draft",
    }
    yaml_text = yaml.safe_dump(
        frontmatter, sort_keys=False, allow_unicode=True
    )
    return f"---\n{yaml_text}---\n\n{draft.body_markdown}\n"


def write_draft(
    draft: SkillDraft,
    *,
    user_skills_root: Path | None = None,
) -> DraftWriteResult:
    """Writes a Jarvis-Agent-Author draft as SKILL.md.

    Plan-§7.5 pipeline step 6: state=draft is ALWAYS forced. If
    the Jarvis-Agent-Author draft outputs `state != "draft"`, we note the
    override in the result (`forced_state_override=True`) — the caller
    writes that to the audit.
    """
    base = user_skills_root.resolve() if user_skills_root else _resolve_user_skills_root()
    base.mkdir(parents=True, exist_ok=True)

    # Slug validation already runs in SkillDraft.field_validator;
    # second defense-in-depth resolution against path traversal here.
    safe_slug = _resolve_clash_safe_slug(draft.slug, base=base)
    target_dir = _resolved_target(safe_slug, root=base)
    target_dir.mkdir(parents=True, exist_ok=True)
    skill_path = target_dir / _SKILL_FILENAME

    rendered = _render_skill_md(draft)
    skill_path.write_text(rendered, encoding="utf-8")

    forced = draft.state != "draft"
    return DraftWriteResult(
        slug=safe_slug,
        draft_path=skill_path,
        forced_state_override=forced,
    )
