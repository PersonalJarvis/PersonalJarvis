"""Five-layer drift guard for the identity UI (AP-4 / BUG-008).

``test_identity_parity.py`` pins the identity value sets down to the two SQL
dialects. This pins the two layers ABOVE them: the TypeScript unions the
People view is written against, and the i18n keys it derives from those
values.

Both are silent when they drift. A missing TS member simply never renders —
an identifier kind added in Python would show up as an empty group nobody
notices. A missing locale key renders as the raw key on screen
("ultrawiki.people.noun_alias"), which is how the layer that exists to explain
a merge to a human ends up printing matcher jargon at them.

The derived keys are the point here: the components build
``ultrawiki.people.facts_<kind>``, ``noun_<kind>`` and ``tier_<tier>`` from
the enum values at runtime, so the enum is the only place a new key can be
demanded from, and nothing in the frontend can notice that it was never
written.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from jarvis.ultrawiki.identity import (
    EntityKind,
    IdentifierKind,
    MatchTier,
    QueueStatus,
)

FRONTEND = Path(__file__).resolve().parents[3] / "jarvis/ui/web/frontend/src"
API_TS = FRONTEND / "lib/ultrawikiIdentityApi.ts"
LOCALES = FRONTEND / "i18n/locales"
SUPPORTED_LOCALES = ("de", "en", "es")

#: Every component that renders an ``ultrawiki.people.*`` string.
PEOPLE_COMPONENTS = (
    FRONTEND / "components/ultrawiki/PeoplePanel.tsx",
    FRONTEND / "components/ultrawiki/MergeQueuePanel.tsx",
    FRONTEND / "views/ultrawiki/UltraWikiPanel.tsx",
)


def ts_const_values(name: str) -> set[str]:
    """The quoted members of an ``export const NAME = [...] as const`` array."""
    source = API_TS.read_text(encoding="utf-8")
    match = re.search(rf"{name}\s*=\s*\[(.*?)\]\s*as const", source, re.DOTALL)
    assert match is not None, f"{name} array not found in {API_TS.name}"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def locale_keys(locale: str) -> dict:
    return json.loads((LOCALES / f"{locale}.json").read_text(encoding="utf-8"))


def nested(data: dict, dotted: str) -> object | None:
    node: object = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def assert_in_every_locale(keys: set[str]) -> None:
    for locale in SUPPORTED_LOCALES:
        data = locale_keys(locale)
        missing = sorted(
            key for key in keys if not (isinstance(nested(data, key), str) and nested(data, key))
        )
        assert not missing, f"{locale}.json is missing: {missing}"


# ---------------------------------------------------------------------------
# Python -> TypeScript
# ---------------------------------------------------------------------------


def test_typescript_entity_kinds_match_the_python_enum():
    assert ts_const_values("ULTRAWIKI_ENTITY_KINDS") == {kind.value for kind in EntityKind}


def test_typescript_identifier_kinds_match_the_python_enum():
    assert ts_const_values("ULTRAWIKI_IDENTIFIER_KINDS") == {kind.value for kind in IdentifierKind}


def test_typescript_match_tiers_match_the_python_enum():
    assert ts_const_values("ULTRAWIKI_MATCH_TIERS") == {tier.value for tier in MatchTier}


def test_typescript_queue_statuses_match_the_python_enum():
    assert ts_const_values("ULTRAWIKI_QUEUE_STATUSES") == {status.value for status in QueueStatus}


# ---------------------------------------------------------------------------
# Enum -> i18n (the keys no grep can find, because they are built at runtime)
# ---------------------------------------------------------------------------


def test_every_identifier_kind_has_a_heading_and_a_noun_in_every_locale():
    """The profile groups facts by kind and the evidence line names the kind
    in a sentence — two different words for the same value, both required."""
    keys = set()
    for kind in IdentifierKind:
        keys.add(f"ultrawiki.people.facts_{kind.value}")
        keys.add(f"ultrawiki.people.noun_{kind.value}")
    assert_in_every_locale(keys)


def test_every_match_tier_has_a_label_in_every_locale():
    assert_in_every_locale({f"ultrawiki.people.tier_{tier.value}" for tier in MatchTier})


# ---------------------------------------------------------------------------
# Component -> i18n
# ---------------------------------------------------------------------------


def people_keys_used_in_components() -> set[str]:
    """Every literal ``t("ultrawiki.people.…")`` the People view asks for."""
    used: set[str] = set()
    for path in PEOPLE_COMPONENTS:
        source = path.read_text(encoding="utf-8")
        used |= set(re.findall(r't\(\s*"(ultrawiki\.people\.[^"]+)"', source))
    return used


def test_every_string_the_people_view_asks_for_exists_in_every_locale():
    used = people_keys_used_in_components()
    assert used, "no i18n keys found — the extraction regex went stale"
    assert_in_every_locale(used)


def test_the_people_locale_blocks_are_identical_across_locales():
    """A key added to one locale only is the same silent failure from the
    other direction: English renders, German prints the raw key."""
    reference = set(locale_keys("en")["ultrawiki"]["people"])
    for locale in SUPPORTED_LOCALES:
        assert set(locale_keys(locale)["ultrawiki"]["people"]) == reference, (
            f"{locale}.json has a different ultrawiki.people key set"
        )


def test_the_view_never_hardcodes_a_person_string():
    """Placeholder-bearing copy is the usual leak: a sentence assembled in TSX
    around a name looks harmless, reads fine in English, and is untranslatable.

    The signature is a literal that CONTAINS a placeholder being filled in
    place — ``"Same {0}".replace(…)`` — as opposed to the legitimate form,
    where the template comes out of a ``t(…)`` lookup and only the VALUE is a
    literal (``.replace("{0}", name)``).
    """
    template_literal = re.compile(r'"[^"]*\{\d\}[^"]*"\s*\.replace')
    for path in PEOPLE_COMPONENTS:
        source = path.read_text(encoding="utf-8")
        hits = template_literal.findall(source)
        assert not hits, f"{path.name}: placeholder copy outside i18n: {hits}"
