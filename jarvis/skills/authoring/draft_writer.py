"""Schreibt Skill-Drafts ins User-Skills-Verzeichnis mit forced state=draft.

Plan-§AD-8: `draft_writer` forciert `state=draft`, auch wenn OpenClaw-Author
fälschlich `state=active` ins Frontmatter setzt. Plan-§AP-6: keine
Auto-Aktivierung — der User muss in der UI / via CLI explizit promoten.

Plan-§AP-10: Drafts landen NUR im `user_skills_dir` (sonst Hot-Reload-
Bypass). Path-Traversal-Schutz vor jedem Schreibversuch via
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
    """Slug ist invalid oder Path-Traversal-Versuch."""


class UnsafeSkillError(RuntimeError):
    """Skill-Body enthält verbotenen Code (Plan-§7.5 Sicherheits-Lint)."""


@dataclass(frozen=True)
class DraftWriteResult:
    """Resultat eines Draft-Writes."""

    slug: str
    draft_path: Path
    forced_state_override: bool


# ----------------------------------------------------------------------
# Slug-Sicherheit
# ----------------------------------------------------------------------


def _resolve_user_skills_root() -> Path:
    return user_skills_dir().resolve()


def _resolved_target(slug: str, root: Path | None = None) -> Path:
    """Resolved den Skill-Ordner unter user_skills_dir/<slug>/.

    Garantiert via `Path.resolve().relative_to(root)`, dass das Ergebnis
    NICHT aus dem User-Skills-Verzeichnis ausbricht — selbst wenn der
    Slug-Validator umgangen würde.
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
    """Falls `<slug>` schon existiert, Suffix `_2`, `_3`, ... anhängen.

    Plan-§7.5-Pipeline-Schritt 2: Clash-Check, kein Overwrite.
    """
    base_dir = base if base is not None else _resolve_user_skills_root()
    candidate_path = base_dir / slug
    if not candidate_path.exists():
        return slug
    for n in range(2, 100):
        new_slug = f"{slug}_{n}"
        if not (base_dir / new_slug).exists():
            return new_slug
    raise SlugError(f"Konnte für '{slug}' nach 99 Versuchen keinen Clash-freien Slug finden")


# ----------------------------------------------------------------------
# Sicherheits-Lint
# ----------------------------------------------------------------------


_FORBIDDEN_CALLS: Final[frozenset[str]] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "globals",
        "locals",
        # Sub-Agent-Review-MAJOR (Phase 7.5): Reflektive-Bypass via getattr/vars.
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
        # Defense-in-Depth (Sub-Agent-Review-MAJOR): falls jemals `builtins`
        # oder `importlib` in safe_imports landet, sind die Recursion-Vektoren
        # explizit blockiert.
        ("builtins", "eval"),
        ("builtins", "exec"),
        ("builtins", "__import__"),
        ("importlib", "import_module"),
    }
)
# Sub-Agent-Review-MAJOR: Codefence-Regex breit fassen — alle Sprach-Tags,
# inkl. `python3`/`pycon`/`ipython`/leer. Plus `~~~`-Tilden-Variante.
_CODE_BLOCK_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:```|~~~)([a-zA-Z0-9_+-]*)\s*\n(.*?)(?:```|~~~)",
    re.DOTALL,
)
_PYTHON_LANG_TAGS: Final[frozenset[str]] = frozenset(
    {"", "python", "py", "python3", "pycon", "ipython", "python2"}
)


def _load_safe_imports() -> frozenset[str]:
    """Liest die Allowlist aus `safe_imports.txt`. Fehler → leere Liste
    (failsafe: bei korrupter Datei lehnt der Lint alle Imports ab).
    """
    try:
        text = _SAFE_IMPORTS_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        _LOG.warning("safe_imports.txt nicht lesbar: %s", exc)
        return frozenset()
    allowed: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Top-Level-Modul (vor dem ersten Punkt)
        allowed.add(stripped.split(".", 1)[0])
    return frozenset(allowed)


def _walk_python_block(code: str, allowed_imports: frozenset[str]) -> list[str]:
    """Inspiziert einen Python-Block per AST. Liefert Liste von Findings.

    Sub-Agent-Review-MAJOR-Hardening (Phase 7.5):
    - `ast.Subscript`-Calls (`__builtins__["eval"](...)`)
    - reflektive Imports (`getattr(os, "system")`)
    """
    findings: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        findings.append(f"syntax_error: {exc.msg}")
        return findings

    for node in ast.walk(tree):
        # Imports — nur Allowlist
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".", 1)[0]
                if top not in allowed_imports:
                    findings.append(f"forbidden_import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".", 1)[0]
            if top and top not in allowed_imports:
                findings.append(f"forbidden_import_from: {node.module}")

        # Calls — direkte Names, Attribute-Pairs und Subscript-Bypass
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
                # `builtins.eval(...)` als Attribut-of-Attribut: __builtins__.eval
                elif (
                    isinstance(func.value, ast.Attribute)
                    and isinstance(func.value.value, ast.Name)
                ):
                    if (
                        func.value.attr == "__builtins__"
                        or func.value.value.id == "__builtins__"
                    ):
                        findings.append(f"forbidden_builtins_access: {func.attr}")
            # Subscript-Bypass: __builtins__["eval"](), globals()["eval"]()
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
    """Heuristik: ist das Code-Block-Inhalt ein parsbares Python-Snippet?

    Ohne Sprach-Tag ist das die einzige Möglichkeit zu wissen, ob wir
    AST-parsen sollen. `ast.parse` toleriert leere Snippets — wir prüfen
    zusätzlich, ob mindestens ein Python-Statement-Token vorkommt.
    """
    if not code.strip():
        return False
    try:
        ast.parse(code)
    except SyntaxError:
        return False
    return True


def safe_lint_skill_body(body: str) -> list[str]:
    """Inspiziert alle Code-Blöcke im Skill-Body.

    Sub-Agent-Review-MAJOR (Phase 7.5): Codefence-Sprache ist nicht mehr
    auf `python|py` beschränkt. Alle Tags + tilde-Fences werden gescannt;
    bei unbekanntem/leerem Tag wird per `ast.parse` heuristisch erkannt,
    ob es Python-Code ist.
    """
    allowed = _load_safe_imports()
    findings: list[str] = []
    for match in _CODE_BLOCK_RE.finditer(body):
        lang = (match.group(1) or "").lower()
        code = match.group(2)
        if lang in _PYTHON_LANG_TAGS:
            if not lang and not _block_looks_like_python(code):
                # Leeres Tag + nicht-Python → ignorieren (z.B. Plain-Text-Block)
                continue
            findings.extend(_walk_python_block(code, allowed))
    return findings


# ----------------------------------------------------------------------
# Render + Write
# ----------------------------------------------------------------------


def _render_skill_md(draft: SkillDraft) -> str:
    """Rendert SKILL.md mit Frontmatter + Body. `state` IMMER `draft`."""
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
        "tags": ["openclaw-authored"],
        "author": "openclaw",
        "license": "MIT",
        "triggers": triggers_parsed,
        "requires_tools": draft.requires_tools,
        # **HARTKODIERT**: Plan-§AD-8 — OpenClaw-Author-Output `state` wird verworfen.
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
    """Schreibt einen OpenClaw-Author-Draft als SKILL.md.

    Plan-§7.5-Pipeline-Schritt 6: state=draft wird IMMER forciert. Wenn
    der OpenClaw-Author-Draft `state != "draft"` ausgibt, vermerken wir den
    Override im Resultat (`forced_state_override=True`) — der Caller
    schreibt das ins Audit.
    """
    base = user_skills_root.resolve() if user_skills_root else _resolve_user_skills_root()
    base.mkdir(parents=True, exist_ok=True)

    # Slug-Validation läuft schon in SkillDraft.field_validator;
    # zweite Defense-in-Depth-Resolution gegen Path-Traversal hier.
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
