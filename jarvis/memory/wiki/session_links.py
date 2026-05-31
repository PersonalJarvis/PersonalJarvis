"""Deterministic graph-connectivity post-processing for session rollups.

The ``SessionRollupWorker`` (Phase B7) used to drop the raw LLM paragraph
verbatim into the session page. The LLM, told only to "reference concrete
tools using ``[[wikilink]]`` syntax" and fed the focused-window name of each
episode, wrapped ephemeral apps in Title-Case display links
(``[[Brave Browser]]``, ``[[PowerShell]]``, ``[[Snipping Tool]]``). None of
those resolve to a real ``entities/<slug>.md`` page, so Obsidian rendered each
as an orphan placeholder node — and because nothing linked a session to a
durable hub, every session became an isolated 2-node "dust pair" in the graph.

These pure helpers turn that paragraph into graph-connected markdown, with the
same discipline as ``scrub_for_voice``: **regex only, no LLM call, no disk
write.** The worker supplies a :class:`SlugIndex` built from the live vault.

The pipeline the worker runs is:

1. :func:`strip_dangling_wikilinks` — remove a token-truncated ``[[…`` fragment
   that has no closing ``]]`` (e.g. ``[[PickerHost.`` at end of a capped
   response).
2. :func:`rewrite_body_links` — for every closed ``[[target]]``: if it resolves
   to an existing page, rewrite to the canonical ``[[dir/slug|Display]]`` form;
   otherwise demote it to plain text. This implements the schema rule
   *"A broken wikilink is a bug … refuse the link and use plain text."*
3. :func:`build_related_footer` — append a deterministic ``## Related`` block
   linking the durable hubs every session shares (the user entity, the most
   relevant project, plus any entity/concept the body genuinely referenced) so
   each session joins the network instead of floating alone.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Closed wikilink, not backslash-escaped. Inner group forbids brackets and
# newlines so we never absorb adjacent tokens. Mirrors ``wikilink._WIKILINK_RE``
# but is kept local so this pure module has no import-time coupling.
_WIKILINK_RE = re.compile(r"(?<!\\)\[\[([^\[\]\n]+)\]\]")

# Page-type directories, in the order the schema lists them. Used to render a
# bare slug into a canonical ``dir/slug`` and to recognise dir-prefixed input.
_PAGE_DIRS: tuple[str, ...] = ("entities", "concepts", "projects", "sessions")


def slugify(text: str) -> str:
    """Return the schema's kebab-case ASCII slug for ``text``.

    Lowercases, transliterates accented characters to ASCII, replaces any run
    of non-alphanumeric characters with a single hyphen, and trims leading and
    trailing hyphens. ``"Brave Browser"`` → ``"brave-browser"``,
    ``"RazerAppEngine.exe"`` → ``"razerappengine-exe"``.
    """
    normalised = unicodedata.normalize("NFKD", text)
    ascii_text = normalised.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_text.lower()
    hyphenated = re.sub(r"[^a-z0-9]+", "-", lowered)
    return hyphenated.strip("-")


def strip_dangling_wikilinks(text: str) -> str:
    """Remove any ``[[`` that has no matching ``]]`` before the next ``[[``/EOF.

    A token-truncated LLM response can end mid-link (``…via [[PickerHost.``);
    Obsidian renders the literal ``[[`` as broken text. We strip only the
    offending ``[[`` markers, keeping the trailing words as plain text. Closed
    links are left untouched.
    """
    if "[[" not in text:
        return text

    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("[[", i):
            close = text.find("]]", i + 2)
            nxt = text.find("[[", i + 2)
            # The "[[" is dangling when there is no closing "]]" at all, or the
            # next "[[" opens before this one ever closes.
            if close == -1 or (nxt != -1 and nxt < close):
                i += 2  # drop the "[[" markers, keep following text verbatim
                continue
        out.append(text[i])
        i += 1
    return "".join(out)


@dataclass(frozen=True, slots=True)
class SlugIndex:
    """Resolves a link target to a canonical ``dir/slug`` of an *existing* page.

    Built from the durable vault pages (entities, concepts, projects). A target
    that maps to no page returns ``None`` so the caller can demote it to plain
    text rather than emit a ghost node.
    """

    _by_slug: dict[str, str]   # bare slug -> "dir/slug"
    _by_alias: dict[str, str]  # lowercased alias/display -> bare slug

    @classmethod
    def from_pages(cls, pages) -> SlugIndex:
        """Build from an iterable of ``(dir, slug, aliases)`` triples.

        ``dir`` is the page-type directory (``"entities"`` …), ``slug`` the
        kebab-case stem, ``aliases`` the page's frontmatter aliases. The slug
        itself, its de-hyphenated form, and every alias all become resolvable
        display keys.
        """
        by_slug: dict[str, str] = {}
        by_alias: dict[str, str] = {}
        for directory, slug, aliases in pages:
            if not slug:
                continue
            canonical = f"{directory}/{slug}"
            by_slug[slug] = canonical
            # The slug with hyphens turned to spaces resolves a Title-Case
            # mention even without an explicit alias ("personal-jarvis" then
            # matches "Personal Jarvis" via slugify on the way in).
            by_alias.setdefault(slug.replace("-", " "), slug)
            for alias in aliases or ():
                key = str(alias).strip().lower()
                if key:
                    by_alias.setdefault(key, slug)
        return cls(_by_slug=by_slug, _by_alias=by_alias)

    def resolve(self, raw_link: str) -> str | None:
        """Return the canonical ``dir/slug`` for ``raw_link`` or ``None``.

        Accepts a dir-prefixed form (``entities/alex``), a bare slug
        (``alex``), a Title-Case display name (``Personal Jarvis``), or a
        registered alias (``the user``). Only returns a value when the target
        page actually exists in the index.
        """
        target = raw_link.split("|", 1)[0].strip()
        if not target:
            return None

        if "/" in target:
            directory, _, bare = target.rpartition("/")
            canonical = self._by_slug.get(bare)
            # Trust the prefix only when it matches the page's real directory.
            if canonical is not None and canonical == f"{directory}/{bare}":
                return canonical
            return None

        # Bare slug, exact.
        if target in self._by_slug:
            return self._by_slug[target]
        # Alias / display text (case-insensitive).
        alias_hit = self._by_alias.get(target.lower())
        if alias_hit is not None:
            return self._by_slug.get(alias_hit)
        # Title-Case display normalised through the same slug rule.
        slugged = slugify(target)
        if slugged in self._by_slug:
            return self._by_slug[slugged]
        return None


def rewrite_body_links(text: str, index: SlugIndex) -> tuple[str, list[str]]:
    """Canonicalise resolvable links; demote the rest to plain text.

    Returns ``(new_text, resolved_targets)`` where ``resolved_targets`` is the
    de-duplicated list of canonical ``dir/slug`` strings the body now links, in
    first-seen order. A resolvable link becomes ``[[dir/slug|Display]]`` (or
    the typed short form when the display already *is* the typed slug); an
    unresolvable link loses its brackets and survives as plain text.
    """
    resolved: list[str] = []
    seen: set[str] = set()

    def _replace(match: re.Match[str]) -> str:
        body = match.group(1)
        target_part, sep, alias = body.partition("|")
        display = (alias if sep else target_part).strip()
        canonical = index.resolve(target_part)
        if canonical is None:
            return display  # demote to plain text
        if canonical not in seen:
            seen.add(canonical)
            resolved.append(canonical)
        # Keep the typed short form when the author already wrote it.
        if not sep and target_part.strip() == canonical:
            return f"[[{canonical}]]"
        return f"[[{canonical}|{display}]]"

    new_text = _WIKILINK_RE.sub(_replace, text)
    return new_text, resolved


def build_related_footer(
    *,
    hub_links: list[str],
    resolved_targets: list[str],
) -> str:
    """Render the deterministic ``## Related`` backbone block.

    ``hub_links`` are the durable spine the worker always wants linked (the
    user entity first, then the most-relevant project), in caller order.
    ``resolved_targets`` are the entities/concepts the body genuinely
    referenced. The two are concatenated and de-duplicated (hubs win ordering),
    each rendered as a typed ``[[dir/slug]]`` bullet. Returns ``""`` when there
    is nothing to link, so an empty section is never appended.
    """
    ordered: list[str] = []
    seen: set[str] = set()
    for target in [*hub_links, *resolved_targets]:
        if target and target not in seen:
            seen.add(target)
            ordered.append(target)
    if not ordered:
        return ""
    lines = ["## Related", ""]
    lines.extend(f"- [[{target}]]" for target in ordered)
    return "\n".join(lines)


def relink_session_body(
    body: str,
    index: SlugIndex,
    *,
    user_slug: str | None = None,
) -> tuple[str, dict]:
    """Re-link an EXISTING session body deterministically (one-shot migration).

    Composes the same steps the live worker applies — strip dangling
    fragments, canonicalise resolvable links, demote ghosts to plain text,
    append the ``## Related`` backbone (user hub + body-resolved targets) —
    so a vault written before the graph-connectivity fix can be cleaned in
    place. Idempotent: a body that already carries a ``## Related`` block is
    not given a second one, and a fully-clean body returns unchanged.

    Returns ``(new_body, {"changed": bool})``. No LLM, no I/O — the caller
    (e.g. ``scripts/wiki_relink_sessions.py``) handles file reads/writes.
    """
    original = body
    text = strip_dangling_wikilinks(body)
    text, resolved = rewrite_body_links(text, index)

    hub_links: list[str] = []
    if user_slug:
        user_canonical = index.resolve(user_slug)
        if user_canonical:
            hub_links.append(user_canonical)

    footer = build_related_footer(hub_links=hub_links, resolved_targets=resolved)
    if footer and "## Related" not in text:
        text = text.rstrip() + "\n\n" + footer + "\n"

    return text, {"changed": text != original}


__all__ = [
    "SlugIndex",
    "build_related_footer",
    "relink_session_body",
    "rewrite_body_links",
    "slugify",
    "strip_dangling_wikilinks",
]
