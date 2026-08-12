"""Load per-plugin usage cards: frontmatter (keywords) + markdown body.

A card co-locates two things: the relevance keywords (for the per-turn gate)
and the guidance prose (injected into the system prompt only when the plugin
is active this turn). No YAML dependency — we parse the tiny frontmatter by
hand. Missing card = None (a plugin without a card still works, just without
curated keywords/guidance; the relevance gate then keeps it only when the turn
NAMES it or a noun auto-derived from its own tools matches — not always-include).
"""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass, field
from pathlib import Path

_CARDS_DIR = Path(__file__).parent
# Second lookup root for cards that arrive at runtime (community marketplace
# installs). Lives under the gitignored data/ next to plugin_catalog.json —
# the package cannot be written on a user machine, data/ can.
_DATA_CARDS_DIR = Path(__file__).resolve().parents[3] / "data" / "usage_cards"


def _resolve_card_path(plugin_id: str) -> Path | None:
    """Locate the card file for ``plugin_id``, treating ``-`` and ``_`` as
    equivalent in the filename.

    Catalog ids and on-disk card filenames disagree on the separator (a catalog
    ``google-calendar`` vs the bundled ``google_calendar.md``), so a literal
    filename match silently returned ``None``. We try the id as given, then with
    ``-`` -> ``_`` and ``_`` -> ``-``, and return the first existing file. The
    swaps only touch ``-``/``_`` and so cannot introduce a path separator; the
    caller still applies the path-traversal guard beforehand.

    The bundled package directory is searched BEFORE ``data/usage_cards/`` so a
    community card can never shadow a shipped plugin's curated keywords.
    """
    for directory in (_CARDS_DIR, _DATA_CARDS_DIR):
        for candidate in (
            plugin_id,
            plugin_id.replace("-", "_"),
            plugin_id.replace("_", "-"),
        ):
            path = directory / f"{candidate}.md"
            if path.exists():
                return path
    return None


@dataclass(frozen=True, slots=True)
class UsageCard:
    plugin_id: str
    keywords: list[str] = field(default_factory=list)
    body: str = ""

    def matches(self, text: str) -> bool:
        low = text.lower()
        return any(kw and kw.lower() in low for kw in self.keywords)


@functools.lru_cache(maxsize=64)
def load_usage_card(plugin_id: str) -> UsageCard | None:
    # Cached: cards are static files bundled with the package, but this is
    # called per-plugin on every dispatcher build (the voice critical path) by
    # both the relevance gate and the prompt injector. The cache turns those
    # repeated disk reads into O(1) lookups (AP-9). The returned UsageCard is
    # read-only, so sharing the instance across callers is safe. Dev hot-reload
    # of a card requires a restart (or load_usage_card.cache_clear()).
    # plugin_id is a catalog id (validated elsewhere); guard path traversal.
    if not plugin_id or "/" in plugin_id or "\\" in plugin_id or ".." in plugin_id:
        return None
    path = _resolve_card_path(plugin_id)
    if path is None:
        return None
    raw = path.read_text(encoding="utf-8")
    keywords: list[str] = []
    body = raw
    if raw.startswith("---"):
        _, _, rest = raw.partition("---")
        front, sep, body = rest.partition("---")
        if not sep:
            front, body = "", raw
        for line in front.splitlines():
            key, _, value = line.partition(":")
            if key.strip() == "keywords":
                # Strip a trailing YAML-style comment. A card's keyword list is
                # the one place German belongs (it is the matching vocabulary a
                # German utterance has to hit), so it carries an inline
                # `i18n-allow` marker for the language gate. Exempting the whole
                # file instead would also stop checking the card's PROSE, which
                # must stay English.
                value = value.split(" #", 1)[0]
                keywords = [k.strip() for k in value.split(",") if k.strip()]
    return UsageCard(plugin_id=plugin_id, keywords=keywords, body=body.strip())


def _guarded_data_card_path(plugin_id: str) -> Path | None:
    """The data-root card path for ``plugin_id``, or None when the id fails
    the same traversal guard `load_usage_card` applies."""
    if not plugin_id or "/" in plugin_id or "\\" in plugin_id or ".." in plugin_id:
        return None
    return _DATA_CARDS_DIR / f"{plugin_id}.md"


def save_usage_card(plugin_id: str, text: str) -> Path:
    """Persist a community plugin's usage card under ``data/usage_cards/``.

    Atomic write (tmp file + replace) so a crash mid-write never leaves a
    half card that would silently change the relevance gate's vocabulary.
    Clears the loader cache — the gate runs on the voice critical path and
    must see the new card without a restart.
    """
    path = _guarded_data_card_path(plugin_id)
    if path is None:
        raise ValueError(f"invalid plugin id for usage card: {plugin_id!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    load_usage_card.cache_clear()
    return path


def delete_usage_card(plugin_id: str) -> None:
    """Remove a community card from the data root (bundled cards are never
    touched). Missing file is a no-op — uninstall must stay idempotent."""
    path = _guarded_data_card_path(plugin_id)
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        # An undeletable card only means stale keywords until the next try;
        # never let it abort an uninstall — but say so (contract §7: no
        # silent except).
        logging.getLogger(__name__).warning("usage card %s not deleted: %s", path, exc)
        return
    load_usage_card.cache_clear()
