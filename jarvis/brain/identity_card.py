"""Identity card — the standing half of ambient personal knowledge.

The per-turn wiki context (``jarvis.brain.wiki_context``) answers "is there a
note about what was just asked?". This module answers the other, cheaper
question: "who is the person asking?". A short, deterministic distillation of
the user's own profile that rides in the CACHED system-prompt prefix, so the
assistant knows whom it is talking to without paying for a retrieval, a model
call, or a per-turn prompt change.

Design constraints, all of them load-bearing:

* **No model call, ever.** The distillation is regex + string work over text
  the user already owns (AP-9/AP-11). There is no summarizer here and none may
  be added — the whole point is that ambient knowledge costs nothing per turn.
* **Hard character budget.** :data:`MAX_IDENTITY_CARD_CHARS` caps the card
  itself; the framing header is a constant. Personal knowledge that is always
  present must be small enough to always be affordable.
* **Byte-stable across turns.** The card is recomputed only when the SOURCE
  content hash changes, and the result is cached on disk under the data
  directory, so a restart does not change the cached prefix either. A block
  that changed every turn would break the provider prompt cache and cost more
  than it delivers.
* **Off the boot path (AP-26).** Nothing here runs at import time. The first
  read happens on the first prompt build; every later read is served from
  memory and re-checks its sources at most every
  :data:`DEFAULT_RECHECK_INTERVAL_S` seconds.
* **Silence beats a personal fact nobody asked for.** The mandate the wiki
  context injector carries applies unchanged here, and harder: this block is
  present on EVERY turn, so its framing has to be blunt about being background
  only. See :data:`IDENTITY_BLOCK_HEADER`.

Sources, in priority order — whichever exist, all optional:

1. The UltraWiki entity profile, through a capability-probed public seam
   (``service.user_profile_markdown()``). The seam does not exist yet; the
   probe is a ``getattr`` + ``callable`` check, so this degrades to "absent"
   until the UltraWiki side ships it. Nothing in this module reaches into an
   UltraWiki store.
2. The normal wiki's living profile page ``entities/<user-slug>.md``
   (``jarvis.memory.wiki.profile``), resolved through the canonical vault-root
   resolver.
3. ``data/core_memory.json``.

Core memory is deliberately consumed LAST. Its content is already injected
separately by the prompt builder, so it only fills whatever budget the wiki
profile left over — which for a user with no wiki at all is the whole card,
and for a user with a rich profile is nothing. Every candidate line is deduped
against what the card already says.

No profile and empty core memory produce an empty card, and an empty card
produces no block at all — a fresh install is silent, not apologetic.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

__all__ = [
    "IDENTITY_BLOCK_HEADER",
    "IDENTITY_CARD_FILENAME",
    "MAX_IDENTITY_CARD_CHARS",
    "PROFILE_SECTION_PRIORITY",
    "IdentityCard",
    "IdentityCardCache",
    "collect_sources",
    "distill_identity_card",
    "identity_card_block",
    "identity_card_text",
    "render_identity_block",
    "reset_identity_card_cache",
]


#: Hard cap on the distilled card (the framing header is extra and constant).
#: Chosen so the always-present block stays in the same order of magnitude as
#: one retrieved wiki snippet: standing knowledge must never out-cost the
#: knowledge that was actually asked for.
MAX_IDENTITY_CARD_CHARS = 600

#: Disk cache, under the data directory. Holds the rendered card plus the hash
#: of the sources it was built from — nothing that is not already in the
#: user's own notes.
IDENTITY_CARD_FILENAME = "identity_card.json"

#: How long a built card is trusted before its sources are re-read. The
#: re-read is two small file reads, and it only rebuilds when the content hash
#: actually moved, so a stale-by-minutes card is the deliberate trade against
#: touching the disk on every turn.
DEFAULT_RECHECK_INTERVAL_S = 300.0

#: Sections of the living profile page that carry identity, in the order they
#: are allowed to spend the character budget. A strict subset of
#: ``jarvis.memory.wiki.profile.PROFILE_SECTIONS`` (guarded by a parity test):
#: "Decisions" and "Sources" are episodic bookkeeping, not who someone is.
PROFILE_SECTION_PRIORITY: tuple[str, ...] = (
    "Summary",
    "Identity",
    "Preferences",
    "Work style",
    "Values",
    "Active projects",
    "Relationships",
)

#: Entries per group. One verbose section must not be able to eat the card.
_MAX_ENTRIES_PER_GROUP = 3

#: Bounded read of any single source file — a runaway profile page must not
#: turn a prompt build into a large read.
_MAX_SOURCE_CHARS = 64_000

#: Bumped when the cache-file layout changes; an older file is ignored rather
#: than misread.
_CACHE_SCHEMA_VERSION = 1

_FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---[ \t]*\r?\n", re.DOTALL)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_BULLET_RE = re.compile(r"\A\s*(?:[-*+]|\d+[.)])\s+")
_HEADING_RE = re.compile(r"\A\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*\Z")
_EMPHASIS_RE = re.compile(r"[*_`~]{1,3}")
_WS_RE = re.compile(r"\s+")
_KEY_NOISE_RE = re.compile(r"[^\w]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class IdentityCard:
    """A built card plus the fingerprint of the sources it came from."""

    text: str
    source_hash: str
    sources: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "version": _CACHE_SCHEMA_VERSION,
            "text": self.text,
            "source_hash": self.source_hash,
            "sources": list(self.sources),
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> IdentityCard | None:
        if int(raw.get("version", 0) or 0) != _CACHE_SCHEMA_VERSION:
            return None
        text = raw.get("text")
        source_hash = raw.get("source_hash")
        if not isinstance(text, str) or not isinstance(source_hash, str):
            return None
        sources = raw.get("sources")
        labels: tuple[str, ...] = ()
        if isinstance(sources, list):
            labels = tuple(s for s in sources if isinstance(s, str))
        return cls(text=text, source_hash=source_hash, sources=labels)


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------


def _clean(value: object) -> str:
    """Flatten one Markdown-ish line into plain prose, or "" when it carries none."""
    if value is None:
        return ""
    text = str(value)
    text = _HTML_COMMENT_RE.sub(" ", text)
    text = _WIKILINK_RE.sub(lambda m: m.group(2) or m.group(1), text)
    text = _MD_LINK_RE.sub(lambda m: m.group(1), text)
    text = _BULLET_RE.sub("", text)
    text = _EMPHASIS_RE.sub("", text)
    text = text.replace("|", " ")
    text = _WS_RE.sub(" ", text).strip(" \t-–—:;")
    return text


def _key(text: str) -> str:
    """Fold a line to a comparison key (case- and punctuation-insensitive)."""
    return _KEY_NOISE_RE.sub("", text.casefold())


class _Collector:
    """Ordered, deduplicated groups of card entries."""

    def __init__(self) -> None:
        self._groups: dict[str, list[str]] = {}
        self._keys: list[str] = []

    def add(self, label: str, value: object) -> None:
        text = _clean(value)
        if not text:
            return
        key = _key(text)
        if not key or self._is_duplicate(key):
            return
        bucket = self._groups.setdefault(label, [])
        if len(bucket) >= _MAX_ENTRIES_PER_GROUP:
            return
        self._keys.append(key)
        bucket.append(text)

    def _is_duplicate(self, key: str) -> bool:
        for seen in self._keys:
            if key == seen:
                return True
            # Containment only counts for substantial strings: "de" inside
            # "dentist" is a coincidence, "runs a solo business" inside "runs a
            # solo business in Berlin" is a restatement.
            if min(len(key), len(seen)) >= 12 and (key in seen or seen in key):
                return True
        return False

    def groups(self) -> list[tuple[str, list[str]]]:
        return [(label, list(entries)) for label, entries in self._groups.items() if entries]


# ---------------------------------------------------------------------------
# Source parsing
# ---------------------------------------------------------------------------


def _strip_frontmatter(markdown: str) -> str:
    return _FRONTMATTER_RE.sub("", markdown, count=1)


def _section_lookup() -> dict[str, str]:
    return {name.casefold(): name for name in PROFILE_SECTION_PRIORITY}


def _collect_profile(collector: _Collector, markdown: str) -> None:
    """Read the living profile page: its H1 name plus the identity sections."""
    if not markdown.strip():
        return
    known = _section_lookup()
    section: str | None = None
    # Section content is gathered first, then spent in PRIORITY order — the
    # page's own order must not decide who gets the budget.
    gathered: dict[str, list[str]] = {}
    name = ""
    for raw_line in _strip_frontmatter(markdown).splitlines():
        heading = _HEADING_RE.match(raw_line)
        if heading is not None:
            level, title = len(heading.group(1)), _clean(heading.group(2))
            if level == 1 and not name:
                name = title
            section = known.get(title.casefold())
            continue
        if section is None:
            continue
        line = _clean(raw_line)
        if line:
            gathered.setdefault(section, []).append(line)
    if name:
        collector.add("Name", name)
    for label in PROFILE_SECTION_PRIORITY:
        for entry in gathered.get(label, []):
            collector.add(label, entry)


def _flatten(value: Any) -> list[str]:
    """One value of a core-memory section as zero or more display strings."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [f"{k}: {v}" for k, v in value.items() if v not in (None, "", [], {})]
    if isinstance(value, Sequence):
        out: list[str] = []
        for item in value:
            out.extend(_flatten(item))
        return out
    if value is None:
        return []
    return [str(value)]


def _collect_core_memory(collector: _Collector, data: Mapping[str, Any] | None) -> None:
    """Fill whatever budget the wiki profile left with core-memory facts.

    ``persona`` is skipped on purpose: it describes the ASSISTANT, and this
    card is about the user.
    """
    if not data:
        return
    facts = data.get("user_facts")
    if isinstance(facts, Mapping):
        for items in facts.values():
            for entry in _flatten(items):
                collector.add("Facts", entry)
    elif facts:
        for entry in _flatten(facts):
            collector.add("Facts", entry)

    prefs = data.get("preferences")
    if isinstance(prefs, Mapping):
        for pref_key, value in prefs.items():
            collector.add("Preferences", f"{pref_key}: {value}")

    projects = data.get("current_projects")
    if isinstance(projects, Mapping):
        for project, description in projects.items():
            collector.add("Active projects", f"{project}: {description}")
    elif projects:
        for entry in _flatten(projects):
            collector.add("Active projects", entry)


# ---------------------------------------------------------------------------
# Distillation
# ---------------------------------------------------------------------------


def distill_identity_card(
    *,
    profile_markdown: str = "",
    core_memory: Mapping[str, Any] | None = None,
    max_chars: int = MAX_IDENTITY_CARD_CHARS,
) -> str:
    """Deterministically distill the sources into a card of ``<= max_chars``.

    Pure function: same input, same output, no IO, no model call. Returns ""
    when the sources carry nothing — the caller then emits no block at all.
    """
    collector = _Collector()
    _collect_profile(collector, profile_markdown or "")
    _collect_core_memory(collector, core_memory)

    lines: list[str] = []
    used = 0
    for label, entries in collector.groups():
        prefix = f"- {label}: "
        line = ""
        for entry in entries:
            candidate = entry if not line else f"{line}; {entry}"
            projected = used + (1 if lines else 0) + len(prefix) + len(candidate)
            if projected > max_chars:
                break
            line = candidate
        if not line:
            continue
        used += (1 if lines else 0) + len(prefix) + len(line)
        lines.append(prefix + line)
    return "\n".join(lines)


#: Framing for the always-present block. Two jobs: say that this is background
#: about the person (not a topic), and grant explicit permission to say nothing
#: about it — the standing mandate of this whole surface.
IDENTITY_BLOCK_HEADER = (
    "## About the user (ambient personal knowledge — background, never a topic)\n"
    "Standing facts about the person you are speaking with, distilled from "
    "their own notes. Let them shape an answer only where they genuinely make "
    "it better — a recommendation, a plan, a decision that depends on who is "
    "asking. Never read this block out, never mention that you have it, and "
    "never volunteer a personal detail the question did not ask for: silence "
    "beats a personal fact nobody asked for."
)


def render_identity_block(card_text: str) -> str:
    """Wrap a card in its usage contract; "" for an empty card."""
    if not card_text or not card_text.strip():
        return ""
    return IDENTITY_BLOCK_HEADER + "\n\n" + card_text.strip()


# ---------------------------------------------------------------------------
# Source resolution (all optional, all silent when absent)
# ---------------------------------------------------------------------------


def _read_text(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(_MAX_SOURCE_CHARS)
    except OSError:
        return ""


def _ultrawiki_profile_markdown() -> str:
    """The UltraWiki entity profile, when that store exposes one.

    Capability probe, never a version check (AP-21): if the live service has a
    callable ``user_profile_markdown`` returning text, that text wins; anything
    else — no service, no seam, a raising seam, a non-string — is simply absent.
    """
    try:
        from jarvis.ultrawiki.service import active_search_service  # noqa: PLC0415

        service = active_search_service()
    except Exception:  # noqa: BLE001 — an unavailable mode is not an error
        return ""
    if service is None:
        return ""
    seam = getattr(service, "user_profile_markdown", None)
    if not callable(seam):
        return ""
    try:
        value = seam()
    except Exception:  # noqa: BLE001 — a broken seam degrades to absent
        log.debug("identity card: UltraWiki profile seam failed", exc_info=True)
        return ""
    if isinstance(value, str):
        return value
    # An async seam would leak an un-awaited coroutine; close it and move on.
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001
            log.debug("identity card: could not close a non-text seam result", exc_info=True)
    return ""


def _user_entity_slug(config: Any) -> str:
    raw = ""
    try:
        rollup = config.memory.wiki.session_rollup
        raw = str(getattr(rollup, "user_entity_slug", "") or "")
    except Exception:  # noqa: BLE001 — an older/absent config is just "user"
        raw = ""
    try:
        from jarvis.memory.wiki.prompt import resolve_user_entity_slug  # noqa: PLC0415

        return resolve_user_entity_slug(raw)
    except Exception:  # noqa: BLE001
        slug = re.sub(r"[^a-z0-9-]+", "-", raw.strip().casefold()).strip("-")
        return slug or "user"


def _vault_profile_path(config: Any) -> Path | None:
    try:
        from jarvis.memory.wiki.vault_root import resolve_vault_root  # noqa: PLC0415

        raw = getattr(getattr(config, "wiki_integration", None), "vault_root", None)
        if raw is not None and str(raw).strip() == "":
            raw = None
        root = resolve_vault_root(raw).path
    except Exception:  # noqa: BLE001 — no vault resolvable means no profile
        log.debug("identity card: vault root unresolvable", exc_info=True)
        return None
    return root / "entities" / f"{_user_entity_slug(config)}.md"


def _core_memory_path(config: Any) -> Path | None:
    # ``[wiki_context].core_memory_path`` is an optional relocation seam for
    # hosts that keep state outside the data directory (and for tests). Absent
    # in a normal config, where the standard data-dir location applies.
    override = getattr(getattr(config, "wiki_context", None), "core_memory_path", None)
    if override:
        return Path(str(override))
    try:
        from jarvis.core.config import DATA_DIR  # noqa: PLC0415
        from jarvis.memory.core_memory import CORE_MEMORY_FILENAME  # noqa: PLC0415

        return Path(DATA_DIR) / CORE_MEMORY_FILENAME
    except Exception:  # noqa: BLE001
        return None


def collect_sources(config: Any = None) -> tuple[str, Mapping[str, Any] | None, tuple[str, ...]]:
    """Read every available source once. Returns ``(profile, core, labels)``.

    Never raises: an unreadable, missing or malformed source is absent, and an
    install with none of them yields ``("", None, ())``.
    """
    labels: list[str] = []
    profile = _ultrawiki_profile_markdown()
    if profile.strip():
        labels.append("ultrawiki:profile")
    else:
        path = _vault_profile_path(config)
        if path is not None:
            profile = _read_text(path)
            if profile.strip():
                labels.append(f"vault:entities/{path.stem}.md")

    core: Mapping[str, Any] | None = None
    core_path = _core_memory_path(config)
    if core_path is not None:
        raw = _read_text(core_path)
        if raw.strip():
            try:
                parsed = json.loads(raw)
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, Mapping):
                core = parsed
                labels.append("core_memory")
    return profile, core, tuple(labels)


def _hash_sources(profile: str, core: Mapping[str, Any] | None) -> str:
    digest = hashlib.sha256()
    digest.update(profile.encode("utf-8", "replace"))
    digest.update(b"\x00")
    if core is not None:
        try:
            digest.update(
                json.dumps(core, sort_keys=True, ensure_ascii=False, default=str).encode(
                    "utf-8", "replace"
                )
            )
        except (TypeError, ValueError):  # pragma: no cover — default=str covers it
            digest.update(repr(core).encode("utf-8", "replace"))
    return digest.hexdigest()[:32]


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class IdentityCardCache:
    """Builds the card at most once per source change, and holds it.

    Thread-safe and IO-free on the common path: after the first build every
    call inside ``recheck_interval_s`` returns the held string without touching
    the filesystem. The disk file carries the card across restarts, so a fresh
    process serves the same bytes as the one before it and the provider prompt
    cache keeps hitting.
    """

    def __init__(
        self,
        *,
        config: Any = None,
        cache_path: Path | str | None = None,
        max_chars: int = MAX_IDENTITY_CARD_CHARS,
        recheck_interval_s: float = DEFAULT_RECHECK_INTERVAL_S,
        clock: Any = time.monotonic,
    ) -> None:
        self._config = config
        self._cache_path = Path(cache_path) if cache_path else None
        self._max_chars = int(max_chars)
        self._recheck_interval_s = float(recheck_interval_s)
        self._clock = clock
        self._lock = threading.Lock()
        self._card: IdentityCard | None = None
        self._checked_at: float | None = None
        self._disk_read = False

    # -- paths ---------------------------------------------------------

    def _path(self) -> Path | None:
        if self._cache_path is not None:
            return self._cache_path
        try:
            from jarvis.core.config import DATA_DIR  # noqa: PLC0415

            self._cache_path = Path(DATA_DIR) / IDENTITY_CARD_FILENAME
        except Exception:  # noqa: BLE001 — no data dir means memory-only cache
            return None
        return self._cache_path

    def _load_from_disk(self) -> IdentityCard | None:
        path = self._path()
        if path is None:
            return None
        raw = _read_text(path)
        if not raw.strip():
            return None
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return None
        if not isinstance(parsed, Mapping):
            return None
        return IdentityCard.from_json(parsed)

    def _store_to_disk(self, card: IdentityCard) -> None:
        path = self._path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(card.to_json(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:  # read-only install / headless container — memory is enough
            log.debug("identity card: cache not writable at %s", path, exc_info=True)

    # -- public --------------------------------------------------------

    def card(self, *, force: bool = False) -> IdentityCard:
        """The current card, rebuilding only when the source hash moved."""
        now = float(self._clock())
        with self._lock:
            if (
                not force
                and self._card is not None
                and self._checked_at is not None
                and (now - self._checked_at) < self._recheck_interval_s
            ):
                return self._card
            try:
                profile, core, labels = collect_sources(self._config)
                source_hash = _hash_sources(profile, core)
            except Exception:  # noqa: BLE001 — a prompt build must never break here
                log.debug("identity card: source read failed", exc_info=True)
                self._checked_at = now
                return self._card or IdentityCard(text="", source_hash="")

            if self._card is not None and self._card.source_hash == source_hash:
                self._checked_at = now
                return self._card

            if not self._disk_read:
                self._disk_read = True
                cached = self._load_from_disk()
                if cached is not None and cached.source_hash == source_hash:
                    self._card = cached
                    self._checked_at = now
                    return cached

            text = distill_identity_card(
                profile_markdown=profile,
                core_memory=core,
                max_chars=self._max_chars,
            )
            card = IdentityCard(text=text, source_hash=source_hash, sources=labels)
            self._store_to_disk(card)
            self._card = card
            self._checked_at = now
            log.info(
                "identity card rebuilt chars=%d sources=%s",
                len(text),
                ",".join(labels) or "none",
            )
            return card

    def text(self, *, force: bool = False) -> str:
        return self.card(force=force).text

    def block(self, *, force: bool = False) -> str:
        return render_identity_block(self.card(force=force).text)


# ---------------------------------------------------------------------------
# Process-wide accessor (what the prompt builder calls)
# ---------------------------------------------------------------------------

_default_cache: IdentityCardCache | None = None
_default_lock = threading.Lock()


def _cache_for(config: Any) -> IdentityCardCache:
    global _default_cache
    with _default_lock:
        if _default_cache is None:
            max_chars = MAX_IDENTITY_CARD_CHARS
            raw_max = getattr(
                getattr(config, "wiki_context", None), "identity_card_max_chars", None
            )
            try:
                if raw_max is not None:
                    max_chars = max(0, min(int(raw_max), MAX_IDENTITY_CARD_CHARS))
            except (TypeError, ValueError):
                max_chars = MAX_IDENTITY_CARD_CHARS
            _default_cache = IdentityCardCache(config=config, max_chars=max_chars)
        return _default_cache


def reset_identity_card_cache() -> None:
    """Drop the process-wide cache (tests, and an in-app source switch)."""
    global _default_cache
    with _default_lock:
        _default_cache = None


def identity_card_text(config: Any = None) -> str:
    """The current card text, or "" when there is nothing to say."""
    try:
        return _cache_for(config).text()
    except Exception:  # noqa: BLE001 — ambient knowledge never breaks a turn
        log.debug("identity card: unavailable", exc_info=True)
        return ""


def identity_card_block(config: Any = None) -> str:
    """The framed block for the cached system-prompt prefix, or "".

    Disabled by ``[wiki_context].identity_card = false``. Safe to call on every
    prompt build: after the first call it is a lock, a clock read and a string
    return.
    """
    try:
        wiki_cfg = getattr(config, "wiki_context", None)
        if wiki_cfg is not None and not bool(getattr(wiki_cfg, "identity_card", True)):
            return ""
        return _cache_for(config).block()
    except Exception:  # noqa: BLE001 — ambient knowledge never breaks a turn
        log.debug("identity card: unavailable", exc_info=True)
        return ""
