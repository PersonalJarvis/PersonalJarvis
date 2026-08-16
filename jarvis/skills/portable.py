"""Read a SKILL.md that was not written for Jarvis.

The open Agent Skills format — the one `npx skills add` installs, and the one
Claude Code, Cursor, Codex and the rest consume — asks for two frontmatter
keys: ``name`` and ``description``. Everything beyond that is the publishing
agent's own vocabulary: ``allowed-tools``, ``model``, ``argument-hint``,
``metadata``, and whatever the next agent invents.

Jarvis' own :class:`~jarvis.skills.schema.SkillFrontmatter` is stricter and
sets ``extra="forbid"``, which is right for a skill Jarvis authors and wrong
for one it downloads: a single foreign key used to drop the whole file to
DRAFT, i.e. installed and dead. This module is the second reading, tried only
after the strict one failed — Postel's rule, applied at exactly one place.

Two properties make that safe rather than merely lenient:

* **Whitelist, not blacklist.** Only the fields listed in
  :data:`ADOPTED_FIELDS` are read. Everything else is dropped and *named* in
  the result, so a skill never silently gains a meaning nobody wrote.
* **Nothing that grants behaviour crosses over.** ``triggers`` (fires by
  itself), ``risk_policy`` (lowers the confirmation tier), ``auto_fire``
  (promotes into the matcher), ``execution`` (dispatches a background worker),
  ``requires_tools`` and the plugin-coupling fields are never adopted, even
  when a foreign file happens to spell them the way Jarvis does. A portable
  skill is instructions the assistant may follow — not a permission grant.

The strict schema stays the rule for skills written HERE: the authoring and
creator services validate against it unchanged, so a typo in a hand-written
skill is still caught where it is made.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from .schema import SkillFrontmatter

log = logging.getLogger(__name__)

#: The frontmatter fields a foreign skill may contribute. Descriptive only —
#: identity, prose, provenance. See the module docstring for why the
#: behaviour-granting fields are absent.
ADOPTED_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "when_to_use",
        "version",
        "author",
        "license",
        "category",
        "tags",
        "homepage_url",
        "source_url",
        "docs_url",
        "token_budget_estimate",
    }
)

#: How many times a field may be dropped before the adaptation gives up. Each
#: round removes at least one offending key, so this bounds a pathological file
#: rather than a normal one (the whitelist is smaller than this).
_MAX_ROUNDS = 16


@dataclass(frozen=True, slots=True)
class PortableAdaptation:
    """A foreign frontmatter, read as far as Jarvis can honestly read it."""

    frontmatter: SkillFrontmatter
    #: The original keys that were NOT adopted, sorted. Shown in the UI: a
    #: reader has to be able to see what Jarvis ignored in their file.
    ignored: tuple[str, ...]


def _normalise_key(key: Any) -> str:
    """``When-To-Use`` → ``when_to_use``.

    The open format writes multi-word keys with dashes, Jarvis with
    underscores. That is a spelling difference, not a meaning difference, so it
    is folded away before the whitelist decides.
    """
    return str(key).strip().replace("-", "_").lower()


def adapt_portable_frontmatter(meta: Mapping[str, Any]) -> PortableAdaptation | None:
    """Adapt a foreign frontmatter mapping, or return None if it is not one.

    None means "this is not a skill at all" — no usable ``name``, or a file
    whose very identity fields are malformed. The caller keeps its existing
    DRAFT-with-error behaviour for that case, which is the honest outcome: a
    file Jarvis cannot name is a file it cannot run.
    """
    if not isinstance(meta, Mapping):
        return None

    name = meta.get("name")
    if not isinstance(name, str) or not name.strip():
        return None

    candidate: dict[str, Any] = {}
    ignored: set[str] = set()
    # Normalised name -> the spelling the author actually used, so a later drop
    # can be reported back in their words rather than in Jarvis'.
    written_as: dict[str, str] = {}
    for raw_key, value in meta.items():
        key = _normalise_key(raw_key)
        if key in ADOPTED_FIELDS:
            candidate[key] = value
            written_as[key] = str(raw_key)
        else:
            ignored.add(str(raw_key))

    # An adopted field can still be malformed (a `description` that is a
    # mapping, a `tags` that is a string). Drop exactly what the validator
    # complains about and try again, so one bad descriptive field costs itself
    # instead of the whole file.
    for _ in range(_MAX_ROUNDS):
        try:
            frontmatter = SkillFrontmatter.model_validate(candidate)
        except ValidationError as exc:
            offenders = {
                str(error["loc"][0])
                for error in exc.errors()
                if error.get("loc")
            }
            # Without a valid name there is nothing to key a skill by, and
            # dropping the name would let it fall back to the file stem —
            # silently renaming someone's skill. Refuse instead.
            if "name" in offenders or not offenders:
                return None
            removed = False
            for field in offenders:
                if field in candidate:
                    del candidate[field]
                    ignored.add(written_as.get(field, field))
                    removed = True
            if not removed:
                return None
            continue
        return PortableAdaptation(
            frontmatter=frontmatter,
            ignored=tuple(sorted(ignored)),
        )

    return None


__all__ = ["ADOPTED_FIELDS", "PortableAdaptation", "adapt_portable_frontmatter"]
