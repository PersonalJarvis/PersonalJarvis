"""Every capability id the society hard-codes must name a real brain tool.

The alias table and the seed proposals once spelled ``plugin:google-calendar``
while the tool registers as ``google_calendar``: calendar never became an
agent's focus and the Planner seed was never offered. This pins the ids to the
``jarvis.tool`` entry points so a rename fails here instead of silently.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from jarvis.society import focus, seeds
from jarvis.society.capabilities import capability_id_for_tool

_PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"
# Folder tools are added per chat session, not through entry points.
_SESSION_TOOLS = {"core:Read"}


def _registered_ids() -> set[str]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    names = data["project"]["entry-points"]["jarvis.tool"]
    return {cap for cap in map(capability_id_for_tool, names) if cap is not None}


def _hard_coded_ids() -> set[str]:
    ids = set(focus._ALIASES) | set(focus._SEND_VERBS)
    ids |= {cap_id for cap_id, _ in seeds._PROPOSALS}
    return {cap for cap in ids if cap.startswith(("plugin:", "core:"))}


def test_every_hard_coded_capability_id_names_a_registered_tool() -> None:
    missing = _hard_coded_ids() - _registered_ids() - _SESSION_TOOLS
    assert not missing, f"capability ids without a jarvis.tool entry point: {sorted(missing)}"


def test_briefing_words_reach_mail_calendar_and_search() -> None:
    catalog_tools = {name: None for name in ("gmail", "google_calendar", "search-web")}
    from jarvis.society.capabilities import build_catalog

    catalog = build_catalog(catalog_tools)
    derived = focus.derive_focus(
        "Morgenbriefing",  # i18n-allow: input vocab
        "Jeden Morgen ein kurzer Überblick",  # i18n-allow: input vocab
        catalog,
    )
    assert set(derived) >= {"plugin:gmail", "plugin:google_calendar", "core:search-web"}


def test_runtime_catalog_marks_an_unauthorised_plugin_unconnected(tmp_path: Path) -> None:
    from jarvis.society.runtime import SocietyRuntime

    tools = {"gmail": None, "google_calendar": None, "search-web": None}
    runtime = SocietyRuntime(
        tmp_path,
        brain_tools=lambda: tools,
        plugin_state=lambda: (["gmail", "google_calendar"], ["gmail"]),
    )
    rows = {row.id: row.connected for row in runtime.catalog()}
    assert rows["plugin:gmail"] is True
    assert rows["plugin:google_calendar"] is False
    assert rows["core:search-web"] is True


def test_runtime_catalog_without_a_probe_keeps_every_plugin_connected(tmp_path: Path) -> None:
    from jarvis.society.runtime import SocietyRuntime

    runtime = SocietyRuntime(tmp_path, brain_tools=lambda: {"gmail": None})
    assert all(row.connected for row in runtime.catalog() if row.id == "plugin:gmail")
