"""Focus and approval rules are derived from the description, no model call."""

from __future__ import annotations

from types import SimpleNamespace

from jarvis.society.capabilities import build_catalog
from jarvis.society.focus import derive_approval_rules, derive_focus


def _tool(name: str, desc: str = "x.") -> SimpleNamespace:
    return SimpleNamespace(name=name, description=desc, risk_tier="monitor", schema={})


CATALOG = build_catalog(
    {
        "gmail": _tool("gmail", "Read and send mail."),
        "google-calendar": _tool("google-calendar", "Calendar events."),
        "cli_gh": _tool("cli_gh", "GitHub CLI."),
        "search-web": _tool("search-web", "Search the web."),
        "wiki-recall": _tool("wiki-recall", "Search the wiki."),
        "notebooklm/ask": _tool("notebooklm/ask", "Ask NotebookLM."),
    }
)


def test_gmail_agent_gets_gmail_first():
    focus = derive_focus(
        "Gmail agent",
        "You read, sort and answer my mail; external mail only after approval.",
        CATALOG,
    )
    assert focus[0] == "plugin:gmail"


def test_german_description_works_too():
    focus = derive_focus(
        "Mail-Assistent",  # i18n-allow: sample agent title
        "Du liest mein Postfach und trägst Termine in den Kalender ein.",  # i18n-allow: sample
        CATALOG,
    )
    assert focus[:2] == ["plugin:gmail", "plugin:google-calendar"] or set(focus[:2]) == {
        "plugin:gmail",
        "plugin:google-calendar",
    }


def test_mcp_server_name_is_reachable_without_a_table_entry():
    focus = derive_focus("Notebook helper", "Use NotebookLM for every question.", CATALOG)
    assert focus[0] == "mcp:notebooklm/ask"


def test_unknown_capabilities_never_come_back():
    focus = derive_focus("DJ", "Play spotify playlists all day.", CATALOG)
    assert "plugin:spotify" not in focus


def test_empty_text_yields_nothing():
    assert derive_focus("", "", CATALOG) == []


def test_focus_is_deterministic_and_limited():
    text = "mail calendar github web wiki notebooklm search inbox pr issues"
    a = derive_focus("all", text, CATALOG, limit=3)
    b = derive_focus("all", text, CATALOG, limit=3)
    assert a == b and len(a) == 3


def test_approval_rules_from_the_boundary_phrase():
    rules = derive_approval_rules("External mail only after approval.", ["plugin:gmail"])
    assert rules == {"require_approval": ["plugin:gmail:send"], "always_allow": []}
    rules_de = derive_approval_rules(
        "Externe Mails nur nach Freigabe.",  # i18n-allow: sample description
        ["plugin:gmail", "cli:gh"],
    )
    assert rules_de["require_approval"] == [
        "cli:gh:close",
        "cli:gh:create",
        "cli:gh:merge",
        "plugin:gmail:send",
    ]


def test_no_boundary_no_rules():
    assert derive_approval_rules("Just do it.", ["plugin:gmail"]) == {
        "require_approval": [],
        "always_allow": [],
    }


def test_boundary_without_known_verbs_gates_the_whole_capability():
    rules = derive_approval_rules("Ask me before anything.", ["plugin:drive", "core:search-web"])
    assert rules["require_approval"] == ["plugin:drive:*"]
