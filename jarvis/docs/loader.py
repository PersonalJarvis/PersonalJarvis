"""Doc-Loader: Markdown-File -> Doc.

Pattern follows ``jarvis/skills/loader.py`` exactly — but tolerant of missing
frontmatter (legacy files in ``docs/`` have none today). When frontmatter is
absent, the loader synthesises sensible defaults from the filename and marks
the file as ``diataxis=UNCLASSIFIED``.

Never raises — problems are recorded in the ``error`` field of the Doc object,
so a broken file does not kill the entire Doc-Registry.
"""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

try:  # optional dep — same pattern as skills/loader.py
    import frontmatter as _frontmatter  # type: ignore
    _HAVE_FRONTMATTER = True
except Exception:  # pragma: no cover
    _frontmatter = None  # type: ignore
    _HAVE_FRONTMATTER = False

try:
    import yaml
    _HAVE_YAML = True
except Exception:  # pragma: no cover
    yaml = None  # type: ignore
    _HAVE_YAML = False

from pydantic import ValidationError

from .schema import Doc, DocDiataxis, DocFrontmatter, DocStatus

log = logging.getLogger(__name__)

# Only ``.md`` and ``.markdown`` are accepted. ``.txt`` is intentionally
# excluded — otherwise ``requirements.txt`` and similar files would be indexed.
DOC_SUFFIXES = (".md", ".markdown")

# Heading regex — ``^#`` through ``^######``. Code fences are excluded via a
# simple state-machine pass (see ``_extract_headings``).
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_CODE_FENCE_RE = re.compile(r"^```")


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _body_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _slugify(text: str) -> str:
    """Very simple slugifier — lowercase, ASCII-only, kebab-case.

    Used for both heading slugs (TOC anchors) and missing ``slug`` frontmatter
    fields. The replacement table covers the most common German special
    characters.
    """
    s = text.strip().lower()
    s = (s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")  # i18n-allow: umlaut-folding lookup, matched in logic
           .replace("ß", "ss").replace("é", "e").replace("è", "e")  # i18n-allow: umlaut-folding lookup, matched in logic
           .replace("ê", "e").replace("á", "a").replace("à", "a"))
    # Allow underscores — they are normalised to hyphens in the next step.
    # Filtering with ``[^a-z0-9\s-]`` here would drop the underscore before the
    # second regex can treat it as a separator (``PHASE_L_P`` -> ``phaselp``).
    s = re.sub(r"[^a-z0-9\s_-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s or "untitled"


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Splits YAML frontmatter from the Markdown body.

    Returns ``({}, text)`` when no frontmatter is present — the caller decides
    whether to synthesise defaults. Prefers ``python-frontmatter``; falls back
    to a manual split otherwise.
    """
    if _HAVE_FRONTMATTER:
        post = _frontmatter.loads(text)  # type: ignore[union-attr]
        meta = dict(post.metadata)
        # python-frontmatter strips a trailing newline from ``.content``; when a
        # doc has NO frontmatter, return the body unchanged so this path agrees
        # with the manual fallback below and the round-trip contract (a bare-body
        # doc must not silently lose its trailing newline).
        if not meta:
            return {}, text
        return meta, post.content

    if not _HAVE_YAML:
        # No YAML library available — cannot parse frontmatter, but we do not
        # crash. Treat everything as body-only.
        return {}, text

    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    fm_str = parts[1]
    body = parts[2].lstrip("\n")
    try:
        meta = yaml.safe_load(fm_str) or {}
    except yaml.YAMLError:
        # Broken YAML — propagate; the caller handles it
        raise
    if not isinstance(meta, dict):
        # Not a dict (e.g. a bare list) — ignore frontmatter
        return {}, text
    return meta, body


def _extract_headings(body: str) -> tuple[tuple[int, str, str], ...]:
    """Reads ``# Heading`` through ``###### Heading`` from the body.

    Code fences are excluded so that ``# inside code-block`` is not mistakenly
    recognised as a heading. Returns ``(level, text, slug)`` tuples — the slug
    is computed deterministically via ``_slugify`` for anchor consistency with
    the UI (rehype-slug uses the same algorithm).
    """
    headings: list[tuple[int, str, str]] = []
    in_fence = False
    for line in body.splitlines():
        if _CODE_FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            headings.append((level, text, _slugify(text)))
    return tuple(headings)


def _synth_frontmatter(path: Path, root: Path) -> DocFrontmatter:
    """Builds synthetic frontmatter from the filename for legacy files.

    ``slug`` is relative to the nearest ``root`` using forward slashes — this
    prevents collisions between files in different roots. ``title`` is the
    filename without extension, with hyphens replaced by spaces.
    """
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = Path(path.name)
    raw_slug = rel.with_suffix("").as_posix()
    slug = _slugify(raw_slug.replace("/", "-"))
    title = path.stem.replace("-", " ").replace("_", " ").strip() or path.name
    return DocFrontmatter(
        title=title,
        slug=slug,
        diataxis=DocDiataxis.UNCLASSIFIED,
        status=DocStatus.ACTIVE,  # legacy files are treated as 'active' until revisited
    )


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------

def parse_doc(path: Path, root: Path | None = None) -> Doc:
    """Loads a single Markdown file.

    Never raises. Errors are stored in the ``error`` field together with
    synthetic frontmatter. ``root`` is the base directory used to derive the
    slug when no explicit slug is present in the frontmatter. Defaults to the
    file's parent directory.
    """
    path = Path(path)
    root = Path(root) if root is not None else path.parent

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        # Cannot read the file — return a stub Doc with synthetic frontmatter
        # and an error marker.
        return Doc(
            path=path,
            frontmatter=_synth_frontmatter(path, root),
            body="",
            headings=(),
            body_hash="",
            error=f"read failed: {exc}",
        )

    try:
        meta, body = _split_frontmatter(raw)
    except Exception as exc:  # noqa: BLE001  (yaml-Errors etc.)
        return Doc(
            path=path,
            frontmatter=_synth_frontmatter(path, root),
            body=raw,
            headings=_extract_headings(raw),
            body_hash=_body_hash(raw),
            error=f"frontmatter parse failed: {exc}",
        )

    # No (or empty) frontmatter: synthesise defaults
    if not meta:
        return Doc(
            path=path,
            frontmatter=_synth_frontmatter(path, root),
            body=body,
            headings=_extract_headings(body),
            body_hash=_body_hash(body),
            error=None,
        )

    # Fill in required fields when frontmatter is incomplete
    synth = _synth_frontmatter(path, root)
    if not meta.get("title"):
        meta["title"] = synth.title
    if not meta.get("slug"):
        meta["slug"] = synth.slug

    try:
        fm = DocFrontmatter.model_validate(meta)
    except ValidationError as exc:
        return Doc(
            path=path,
            frontmatter=synth,
            body=body,
            headings=_extract_headings(body),
            body_hash=_body_hash(body),
            error=f"frontmatter schema invalid: {exc}",
        )

    return Doc(
        path=path,
        frontmatter=fm,
        body=body,
        headings=_extract_headings(body),
        body_hash=_body_hash(body),
        error=None,
    )


def discover_docs(roots: list[Path]) -> list[Doc]:
    """Recursively walks all ``roots`` for ``*.md`` / ``*.markdown`` files.

    Excludes:
    - Hidden / tool directories (``.git``, ``.venv``, ``node_modules``, ``__pycache__``).
    - SKILL.md (belongs to the skill system, not here).
    - Files under ``frontend/dist/`` (build output).
    """
    excluded_dirs = {
        ".git", ".venv", "node_modules", "__pycache__", ".pytest_cache",
        ".mypy_cache", ".ruff_cache", "dist", "build",
    }
    excluded_filenames = {"SKILL.md"}

    docs: list[Doc] = []
    seen: set[Path] = set()

    for root in roots:
        root = Path(root)
        if not root.exists() or not root.is_dir():
            log.debug("doc-root not found, skipping: %s", root)
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in DOC_SUFFIXES:
                continue
            if path.name in excluded_filenames:
                continue
            # Excluded dirs: any component of the path matches
            if any(part in excluded_dirs for part in path.parts):
                continue
            # Deduplication (e.g. when roots overlap)
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                docs.append(parse_doc(path, root=root))
            except Exception as exc:  # noqa: BLE001
                log.warning("parse_doc failed hard for %s: %s", path, exc)
                # Hard-failure stub so the UI still lists the file
                docs.append(
                    Doc(
                        path=path,
                        frontmatter=_synth_frontmatter(path, root),
                        body="",
                        headings=(),
                        body_hash="",
                        error=f"hard failure: {exc}",
                    )
                )
    return docs
