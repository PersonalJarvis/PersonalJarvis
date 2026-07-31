"""Five-layer drift guard for ``ExploreReason`` (AP-4 / BUG-008).

The reason code crosses Python → REST → TypeScript → i18n → UI. Each hop is a
place where a value can be added on one side and forgotten on the other, and
the failure mode is silent: the view falls back to a blank panel with no
explanation, which is the exact problem the reason codes were introduced to
end. So the TS union and the three locale files are pinned to the Python enum
here rather than by convention.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from jarvis.ultrawiki.types import ExploreReason

FRONTEND = Path(__file__).resolve().parents[3] / "jarvis/ui/web/frontend/src"
API_TS = FRONTEND / "lib/ultrawikiExploreApi.ts"
LOCALES = FRONTEND / "i18n/locales"
SUPPORTED_LOCALES = ("de", "en", "es")


def ts_union_values() -> set[str]:
    """The quoted members of ``ULTRAWIKI_EXPLORE_REASONS``."""
    source = API_TS.read_text(encoding="utf-8")
    match = re.search(
        r"ULTRAWIKI_EXPLORE_REASONS\s*=\s*\[(.*?)\]\s*as const", source, re.DOTALL
    )
    assert match is not None, "ULTRAWIKI_EXPLORE_REASONS array not found"
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


def test_typescript_union_matches_the_python_enum():
    assert ts_union_values() == {reason.value for reason in ExploreReason}


def explore_keys_used_in_components() -> set[str]:
    """Every ``t("ultrawiki.explore.…")`` the Explore components ask for."""
    used: set[str] = set()
    for name in ("ExplorePanel.tsx", "EntityGraph.tsx", "VaultBar.tsx"):
        source = (FRONTEND / "components/ultrawiki" / name).read_text(encoding="utf-8")
        used |= set(re.findall(r't\(\s*"(ultrawiki\.explore\.[^"]+)"', source))
    return used


def test_every_string_the_explore_view_asks_for_exists_in_every_locale():
    """A missing key renders as the raw key on screen — which is exactly what
    happened once when a concurrent write dropped the vault block between the
    insert and the commit. The reason codes were covered; the rest was not."""
    used = explore_keys_used_in_components()
    assert used, "no i18n keys found — the extraction regex went stale"
    for locale in SUPPORTED_LOCALES:
        data = locale_keys(locale)
        missing = sorted(key for key in used if not isinstance(nested(data, key), str))
        assert not missing, f"{locale}.json is missing: {missing}"


def test_every_reason_has_a_message_in_every_locale():
    # "ok" is the non-empty case and needs no empty-state copy.
    explaining = [r.value for r in ExploreReason if r is not ExploreReason.OK]
    for locale in SUPPORTED_LOCALES:
        data = locale_keys(locale)
        for reason in explaining:
            key = f"ultrawiki.explore.empty.{reason}"
            message = nested(data, key)
            assert isinstance(message, str) and message.strip(), (
                f"{locale}.json is missing a message for {key}"
            )
