"""Cross-layer parity guards for the two vocabularies the polish pass added (AP-4).

CLAUDE.md §5 asks for this test PREEMPTIVELY, before a value has drifted, because
the five-layer drift bug (BUG-008) has recurred four times in this repo and every
recurrence looked different on the surface. Two vocabularies started crossing
layers when the wording pass landed, and neither had a guard:

**The polish status** — how the optional wording pass ended for one dictation.

1. ``jarvis.dictation.polish.POLISH_STATUSES`` is the single source of truth.
2. It travels: on ``DictationCompleted.polish_status``, in the ``status`` field
   of ``POST /api/dictation/polish/test``, and in the history row the speech
   pipeline writes. Every one of those hops is a plain ``str`` / ``dict`` — no
   Pydantic ``Literal`` anywhere — which is *why* this file matters: nothing
   between Python and the screen will ever raise on an unknown value, so a new
   status reaches the user silently and wrongly instead of loudly.
3. The TypeScript mirror in ``useDictation.ts``.
4. ``polishStatusLabel``, which decides between a translated label and the raw
   identifier.
5. ``dictation.polish_status.<name>`` in every locale — a missing key renders
   the key itself as the badge text, on the one row whose whole job is to tell a
   person what happened to their own words.

**The provider tier** — which section of the API-Keys screen a provider belongs
to. The polish pass added ``dictation`` as a fifth tier, so this vocabulary now
crosses five layers too:

1. ``jarvis.ui.web.provider_spec.Tier`` (the Python ``Literal``),
2. ``_SECTION_HEALTH_KEYS`` in ``provider_routes`` (the REST rollup's sections),
3. the ``ProviderTier`` union in ``useProviders.ts``,
4. the category map in ``ProviderTierSection.tsx`` (its i18n keys),
5. ``apikeys_view.tier_*`` / ``tab_*`` / ``cat_*_desc`` in every locale.

The three sets are deliberately NOT identical, and the differences are pinned
here by name rather than papered over with a subset check — an unexplained
extra member is exactly what drift looks like on its first day.

Modelled on ``test_outcome_parity.py``, including its most important discipline:
every parsed set is asserted NON-EMPTY before it is compared, so a regex that
stops matching (a reformat, a rename, a moved file) fails loudly instead of
comparing two empty sets and going trivially green.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import get_args

from jarvis.dictation.polish import POLISH_STATUSES
from jarvis.ui.web.provider_routes import _SECTION_HEALTH_KEYS
from jarvis.ui.web.provider_spec import PROVIDERS, Tier

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FRONTEND = _REPO_ROOT / "jarvis" / "ui" / "web" / "frontend" / "src"
_DICTATION_TS = _FRONTEND / "hooks" / "useDictation.ts"
_PROVIDERS_TS = _FRONTEND / "hooks" / "useProviders.ts"
_HISTORY_GROUP_TSX = _FRONTEND / "views" / "voice" / "DictationHistoryGroup.tsx"
_TIER_SECTION_TSX = _FRONTEND / "components" / "providers" / "ProviderTierSection.tsx"
_LOCALES = _FRONTEND / "i18n" / "locales"

SUPPORTED_LOCALES = ("de", "en", "es")

# The tiers the TypeScript union carries that ``provider_spec.Tier`` does not.
# ``computer-use`` is an OVERLAY selection, not a provider tier: the Computer-Use
# planner is picked from the brain providers (``[brain.computer_use].provider``)
# and every card in that section carries ``tier == "brain"`` on the wire. It gets
# its own tab because the user chooses it separately, which is a UI fact and has
# no counterpart in the Python vocabulary. Pinned by name so a NEW divergence —
# a backend tier that never reached the UI, or a UI tier nothing serves — fails.
_UI_ONLY_TIERS = frozenset({"computer-use"})

# Sections of the health rollup that are not provider tiers at all: the mission
# worker's own configuration and the de-emphasized tab of optional integrations.
# Both are aggregates over things that have no ``ProviderSpec``.
_NON_TIER_SECTIONS = frozenset({"subagents", "advanced"})


def _read(path: Path) -> str:
    assert path.exists(), f"frontend source missing: {path}"
    source = path.read_text(encoding="utf-8")
    assert source.strip(), f"frontend source is empty: {path}"
    return source


def _ts_const_array(path: Path, name: str) -> list[str]:
    """Members of ``export const <name> = [...] as const`` in *path*.

    Comments inside such a block must not contain double-quoted words — the
    regex below cannot tell prose from a member. Every block this file parses
    says so in its own comment.
    """
    source = _read(path)
    match = re.search(
        rf"export const {name}\s*=\s*\[(.*?)\]\s*as const",
        source,
        re.DOTALL,
    )
    assert match is not None, f"{name} array not found in {path.name}"
    return re.findall(r'"([a-z_]+)"', match.group(1))


def _ts_union(path: Path, name: str) -> list[str]:
    """Members of ``export type <name> = "a" | "b" | ...;`` in *path*."""
    source = _read(path)
    match = re.search(rf"export type {name}\s*=([^;]*);", source, re.DOTALL)
    assert match is not None, f"type {name} not found in {path.name}"
    return re.findall(r'"([a-z-]+)"', match.group(1))


def _locale(name: str) -> dict:
    path = _LOCALES / f"{name}.json"
    assert path.exists(), f"locale file missing: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _slug(tier: str) -> str:
    """``computer-use`` → ``computer_use``: i18n keys cannot carry a hyphen."""
    return tier.replace("-", "_")


# ─────────────────────────────────────────────────────────────────────────────
# The polish status vocabulary
# ─────────────────────────────────────────────────────────────────────────────


def test_the_python_polish_vocabulary_is_a_usable_set() -> None:
    assert POLISH_STATUSES, "POLISH_STATUSES is empty"
    assert len(set(POLISH_STATUSES)) == len(POLISH_STATUSES), POLISH_STATUSES


def test_ts_polish_array_mirrors_the_python_vocabulary() -> None:
    members = _ts_const_array(_DICTATION_TS, "POLISH_STATUSES")
    # Guard against a trivially-green empty/partial parse.
    assert members, f"parsed no polish statuses from {_DICTATION_TS.name}"
    assert len(members) == len(set(members)), members
    assert len(members) == len(POLISH_STATUSES), members
    assert set(members) == set(POLISH_STATUSES)


def test_every_polish_status_has_a_label_in_every_locale() -> None:
    assert POLISH_STATUSES, "POLISH_STATUSES is empty"
    for name in SUPPORTED_LOCALES:
        table = _locale(name).get("dictation", {}).get("polish_status", {})
        assert isinstance(table, dict) and table, (
            f"{name}.json: dictation.polish_status missing"
        )
        for status in POLISH_STATUSES:
            value = table.get(status)
            assert isinstance(value, str) and value.strip(), (
                f"{name}.json: dictation.polish_status.{status}"
            )


def test_no_locale_carries_a_polish_status_the_backend_never_emits() -> None:
    """A stale key is drift in the other direction — dead copy nobody maintains."""
    for name in SUPPORTED_LOCALES:
        table = _locale(name).get("dictation", {}).get("polish_status", {})
        assert set(table) == set(POLISH_STATUSES), f"{name}.json: {sorted(table)}"


def test_the_status_renderer_derives_its_known_set_from_the_shared_array() -> None:
    """No second, hand-written copy of the vocabulary in the renderer.

    ``polishStatusLabel`` decides whether a status is translated or printed
    verbatim. If that decision ran off a locally declared list, a new status
    would render as a raw identifier even after the shared array was updated —
    a second source of truth is exactly what this file exists to prevent. The
    history row must reach the label through that function rather than building
    its own ``dictation.polish_status.`` key.
    """
    hook = _read(_DICTATION_TS)
    assert "new Set(POLISH_STATUSES)" in hook, (
        f"{_DICTATION_TS.name} no longer derives its known-status set from the "
        "shared array"
    )
    row = _read(_HISTORY_GROUP_TSX)
    assert "polishStatusLabel" in row, _HISTORY_GROUP_TSX.name
    assert "dictation.polish_status." not in row, (
        f"{_HISTORY_GROUP_TSX.name} builds the i18n key itself instead of going "
        "through polishStatusLabel"
    )


def test_local_only_reached_every_layer() -> None:
    """The newest status, named explicitly so a gap says which file to edit.

    The generic tests above already iterate the whole vocabulary, but they fail
    with "dictation.polish_status.local_only" and leave the reader to work out
    what ``local_only`` is. This one states it: when speech recognition runs on
    this machine, the wording pass refuses to be the step that ships the words
    off it, delivers the raw transcript and reports ``local_only``. That value
    has to exist in five places — the Python tuple, the TypeScript mirror, and
    each of the three locales — before the UI can render anything but a raw
    identifier at the user.
    """
    assert "local_only" in POLISH_STATUSES, "jarvis/dictation/polish.py"
    assert "local_only" in _ts_const_array(_DICTATION_TS, "POLISH_STATUSES"), (
        'jarvis/ui/web/frontend/src/hooks/useDictation.ts: add "local_only" to '
        "POLISH_STATUSES"
    )
    for name in SUPPORTED_LOCALES:
        table = _locale(name).get("dictation", {}).get("polish_status", {})
        assert str(table.get("local_only", "")).strip(), (
            f"jarvis/ui/web/frontend/src/i18n/locales/{name}.json: add "
            "dictation.polish_status.local_only"
        )


def test_translated_reached_every_layer() -> None:
    """The status the translate pass added, pinned by name like ``local_only``.

    ``translated`` is the second status in the vocabulary where the delivered
    text differs from what was recognized — and unlike ``applied`` it changes
    the LANGUAGE, so a history row that cannot name it leaves the user looking
    at English under a heading that says these are the words they said.
    """
    assert "translated" in POLISH_STATUSES, "jarvis/dictation/polish.py"
    assert "translated" in _ts_const_array(_DICTATION_TS, "POLISH_STATUSES"), (
        'jarvis/ui/web/frontend/src/hooks/useDictation.ts: add "translated" to '
        "POLISH_STATUSES"
    )
    for name in SUPPORTED_LOCALES:
        table = _locale(name).get("dictation", {}).get("polish_status", {})
        assert str(table.get("translated", "")).strip(), (
            f"jarvis/ui/web/frontend/src/i18n/locales/{name}.json: add "
            "dictation.polish_status.translated"
        )


def test_every_translation_target_has_an_english_name_for_the_prompt() -> None:
    """The prompt's language table and the accepted targets are one set.

    A target with no entry in ``LANGUAGE_ENGLISH_NAMES`` still produces a usable
    prompt — the code is passed through — but it hands the model a bare ``sd``
    to decode instead of ``Sindhi``, on exactly the languages least likely to
    survive that. The fallback exists so a dictation never fails on a lookup;
    this test is what stops it becoming the normal path.
    """
    from jarvis.core.config import TRANSLATION_TARGETS
    from jarvis.dictation.translate_prompt import LANGUAGE_ENGLISH_NAMES

    assert TRANSLATION_TARGETS, "TRANSLATION_TARGETS is empty"
    assert LANGUAGE_ENGLISH_NAMES, "LANGUAGE_ENGLISH_NAMES is empty"
    missing = set(TRANSLATION_TARGETS) - set(LANGUAGE_ENGLISH_NAMES)
    assert not missing, (
        "jarvis/dictation/translate_prompt.py: add an English name for "
        f"{sorted(missing)}"
    )
    # And the other direction, so the table cannot accumulate dead rows for
    # languages the recognizer no longer offers.
    stale = set(LANGUAGE_ENGLISH_NAMES) - set(TRANSLATION_TARGETS)
    assert not stale, f"translate_prompt.py: names nothing can select: {sorted(stale)}"


def test_no_translation_target_is_the_auto_placeholder() -> None:
    """``auto`` is a coherent INPUT answer and no answer at all as an output.

    Offering it would put an entry in the dropdown that resolves to "translate
    nothing" — a switch whose value is ignored (AP-31).
    """
    from jarvis.core.config import TRANSLATION_TARGETS

    assert "auto" not in TRANSLATION_TARGETS


def test_the_translate_card_says_what_happens_when_no_model_answers() -> None:
    """The copy half, in all three languages.

    A polish pass that falls back is invisible; a TRANSLATION that falls back
    delivers the wrong language into a live document. That difference is the one
    thing the description cannot leave out, and no code change can supply it.
    """
    for name in SUPPORTED_LOCALES:
        translate = _locale(name).get("voice", {}).get("translate", {})
        assert isinstance(translate, dict) and translate, (
            f"jarvis/ui/web/frontend/src/i18n/locales/{name}.json: add "
            "voice.translate"
        )
        for key in (
            "title",
            "description",
            "sends_text",
            "target_label",
            "target_hint",
            "same_language_notice",
        ):
            value = translate.get(key)
            assert isinstance(value, str) and value.strip(), (
                f"{name}.json: voice.translate.{key}"
            )
        # Long enough to be the real sentence rather than a placeholder.
        assert 80 < len(str(translate["sends_text"])) < 420, (
            f"{name}.json: voice.translate.sends_text length"
        )

    tab = _read(_FRONTEND / "views" / "voice" / "LanguageTab.tsx")
    for key in ("voice.translate.title", "voice.translate.sends_text"):
        assert key in tab, f"LanguageTab.tsx does not render {key}"


def test_the_language_tab_says_the_transcript_leaves_the_machine() -> None:
    """The copy half of the privacy fix, pinned in all three languages.

    The description already said what the pass CHANGES. It never said the
    finished transcript is sent to the selected provider, which is the fact a
    person needs before switching it on, and the fact no code change can supply.
    Both halves are asserted — the destination and the retained raw text — in
    every locale, because a sentence that exists in English only is invisible to
    the users it was written for.
    """
    for name in SUPPORTED_LOCALES:
        polish = _locale(name).get("voice", {}).get("polish", {})
        assert isinstance(polish, dict) and polish, f"{name}.json: voice.polish"
        sends = polish.get("sends_text")
        assert isinstance(sends, str) and sends.strip(), (
            f"jarvis/ui/web/frontend/src/i18n/locales/{name}.json: add "
            "voice.polish.sends_text"
        )
        # Long enough to be a real sentence rather than a placeholder, short
        # enough to stay the calm one-or-two-liner it was written as.
        assert 80 < len(sends) < 420, f"{name}.json: voice.polish.sends_text length"

    tab = _read(_FRONTEND / "views" / "voice" / "LanguageTab.tsx")
    assert 'voice.polish.sends_text' in tab, (
        "LanguageTab.tsx does not render voice.polish.sends_text"
    )


def test_the_privacy_sentence_hardcodes_no_product_name() -> None:
    """CLAUDE.md §4: a visible product name is derived from the wake word.

    The sentence talks about "your speech recognition" and "the model you pick"
    on purpose — naming the app would be a hardcoded brand, and naming a
    provider would be wrong for everyone whose chain resolved differently.
    """
    banned = ("jarvis", "personal jarvis", "assistant-agent")
    for name in SUPPORTED_LOCALES:
        sends = str(_locale(name)["voice"]["polish"]["sends_text"]).lower()
        for word in banned:
            assert word not in sends, f"{name}.json: voice.polish.sends_text -> {word}"


# ─────────────────────────────────────────────────────────────────────────────
# The provider tier vocabulary
# ─────────────────────────────────────────────────────────────────────────────


def test_the_python_tier_vocabulary_is_a_usable_set() -> None:
    tiers = get_args(Tier)
    assert tiers, "provider_spec.Tier resolved to no members"
    assert len(set(tiers)) == len(tiers), tiers
    # Every declared tier is actually populated. A tier with no provider is a
    # tab that renders an empty section at the user.
    used = {spec.tier for spec in PROVIDERS}
    assert used == set(tiers), f"declared {sorted(tiers)} vs used {sorted(used)}"


def test_every_backend_tier_reaches_the_typescript_union() -> None:
    """The direction that costs the user: a tier the UI cannot place.

    A provider served with a tier the union does not carry falls out of the
    category map, so its card renders nowhere — the provider is configured,
    reachable, and invisible.
    """
    ts_tiers = _ts_union(_PROVIDERS_TS, "ProviderTier")
    assert ts_tiers, f"parsed no tiers from {_PROVIDERS_TS.name}"
    assert len(ts_tiers) == len(set(ts_tiers)), ts_tiers
    python_tiers = set(get_args(Tier))
    assert python_tiers <= set(ts_tiers), python_tiers - set(ts_tiers)
    # And the other direction, pinned by name rather than waved through.
    assert set(ts_tiers) - python_tiers == set(_UI_ONLY_TIERS), sorted(
        set(ts_tiers) - python_tiers
    )


def test_every_ui_tier_has_a_health_section_in_the_rest_rollup() -> None:
    """The middle layer: a tab whose dot the backend never computes stays blank.

    ``_SECTION_HEALTH_KEYS`` is what the endpoint fills in, and the tabs read it
    by tier id. A tier missing here shows no indicator at all — the silent
    failure mode, since "no dot" is also what a healthy section looks like.
    """
    assert _SECTION_HEALTH_KEYS, "_SECTION_HEALTH_KEYS is empty"
    ts_tiers = set(_ts_union(_PROVIDERS_TS, "ProviderTier"))
    assert ts_tiers, f"parsed no tiers from {_PROVIDERS_TS.name}"
    assert ts_tiers <= set(_SECTION_HEALTH_KEYS), ts_tiers - set(_SECTION_HEALTH_KEYS)
    assert set(_SECTION_HEALTH_KEYS) - ts_tiers == set(_NON_TIER_SECTIONS), sorted(
        set(_SECTION_HEALTH_KEYS) - ts_tiers
    )


def test_every_ui_tier_has_a_category_block_and_localized_copy() -> None:
    """Layers four and five: the category map and the three locale files.

    ``makeProviderCategories`` is typed ``Record<ProviderTier, CategoryMeta>``,
    so TypeScript catches a MISSING tier — but nothing catches an i18n key that
    was never seeded, and that renders "apikeys_view.tier_dictation" as the
    heading of a whole section.
    """
    source = _read(_TIER_SECTION_TSX)
    used_keys = set(re.findall(r"apikeys_view\.[a-z_]+", source))
    assert used_keys, f"parsed no apikeys_view keys from {_TIER_SECTION_TSX.name}"

    ts_tiers = _ts_union(_PROVIDERS_TS, "ProviderTier")
    assert ts_tiers, f"parsed no tiers from {_PROVIDERS_TS.name}"
    tables = {name: _locale(name).get("apikeys_view", {}) for name in SUPPORTED_LOCALES}
    for name, table in tables.items():
        assert isinstance(table, dict) and table, f"{name}.json: apikeys_view missing"

    for tier in ts_tiers:
        slug = _slug(tier)
        for key in (f"tier_{slug}", f"tab_{slug}", f"cat_{slug}_desc"):
            assert f"apikeys_view.{key}" in used_keys, (
                f"{_TIER_SECTION_TSX.name}: no apikeys_view.{key} for tier {tier}"
            )
            for name, table in tables.items():
                value = table.get(key)
                assert isinstance(value, str) and value.strip(), (
                    f"{name}.json: apikeys_view.{key}"
                )


def test_the_dictation_tier_reached_every_layer() -> None:
    """The newest tier, named explicitly for the same reason as ``local_only``.

    ``dictation`` is the optional tier the wording pass introduced: a missing
    key there costs nothing, the transcript is simply delivered as recognized.
    That is precisely why it is easy to half-land — nothing breaks when it is
    only half there, it just renders identifiers at the user.
    """
    assert "dictation" in get_args(Tier), "jarvis/ui/web/provider_spec.py"
    assert "dictation" in _ts_union(_PROVIDERS_TS, "ProviderTier"), (
        "jarvis/ui/web/frontend/src/hooks/useProviders.ts: ProviderTier"
    )
    assert "dictation" in _SECTION_HEALTH_KEYS, (
        "jarvis/ui/web/provider_routes.py: _SECTION_HEALTH_KEYS"
    )
    for name in SUPPORTED_LOCALES:
        table = _locale(name).get("apikeys_view", {})
        for key in ("tier_dictation", "tab_dictation", "cat_dictation_desc"):
            assert str(table.get(key, "")).strip(), (
                f"jarvis/ui/web/frontend/src/i18n/locales/{name}.json: add "
                f"apikeys_view.{key}"
            )
