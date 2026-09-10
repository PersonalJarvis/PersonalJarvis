"""One catalog over plugins, CLIs, MCPs, skills and core; dispatch never inside."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.society.capabilities import (
    NEVER_GRANTED,
    CapabilityKind,
    build_catalog,
    capability_id_for_tool,
    select_tools,
    tool_name_for_capability,
)


def _tool(name: str, desc: str = "does things.", tier: str = "monitor") -> SimpleNamespace:
    return SimpleNamespace(name=name, description=desc, risk_tier=tier, schema={})


def _skill(slug: str, state: str = "active", desc: str = "A skill.") -> SimpleNamespace:
    return SimpleNamespace(
        slug=slug,
        state=SimpleNamespace(value=state),
        frontmatter=SimpleNamespace(name=slug, description=desc, title=None),
        path=SimpleNamespace(stem=slug),
    )


TOOLS = {
    "gmail": _tool("gmail", "Read and send mail via Gmail. Also drafts."),
    "cli_gh": _tool("cli_gh", "Run gh commands."),
    "github/create_issue": _tool(
        "github/create_issue", "[ACTION-ONLY · MCP: github] Create an issue."
    ),
    "search-web": _tool("search-web", "Search the web.", "safe"),
    "spawn-worker": _tool("spawn-worker", "Spawn a worker."),
    "navigate": _tool("navigate", "Switch sidebar section."),
    "wiki-recall": _tool("wiki-recall", "Search the wiki.", "safe"),
}


def test_ids_by_kind():
    assert capability_id_for_tool("gmail") == "plugin:gmail"
    assert capability_id_for_tool("cli_gh") == "cli:gh"
    assert capability_id_for_tool("github/create_issue") == "mcp:github/create_issue"
    assert capability_id_for_tool("search-web") == "core:search-web"
    assert capability_id_for_tool("spawn-worker") is None
    for name in NEVER_GRANTED:
        assert capability_id_for_tool(name) is None


def test_ids_roundtrip_to_tool_names():
    for name in ("gmail", "cli_gh", "github/create_issue", "search-web"):
        cap = capability_id_for_tool(name)
        assert cap is not None
        assert tool_name_for_capability(cap) == name
    assert tool_name_for_capability("skill:daily-brief") is None


def test_catalog_groups_orders_and_filters():
    rows = build_catalog(TOOLS, [_skill("daily-brief"), _skill("draft-one", state="draft")])
    ids = [r.id for r in rows]
    assert ids == [
        "plugin:gmail",
        "cli:gh",
        "mcp:github/create_issue",
        "skill:daily-brief",
        "core:search-web",
        "core:wiki-recall",
    ]
    gmail = rows[0]
    assert gmail.kind is CapabilityKind.PLUGIN
    assert gmail.one_liner == "Read and send mail via Gmail."
    mcp = rows[2]
    assert mcp.one_liner == "Create an issue."
    assert mcp.aliases == ("github",)
    assert all("spawn" not in r.id and "navigate" not in r.id for r in rows)


def test_catalog_marks_disconnected_and_sorts_them_last():
    tools = {"gmail": _tool("gmail"), "spotify": _tool("spotify")}
    rows = build_catalog(tools, connected=lambda name, kind: name != "gmail")
    assert [(r.id, r.connected) for r in rows] == [
        ("plugin:spotify", True),
        ("plugin:gmail", False),
    ]


@pytest.mark.parametrize("state", ["active", "validated", "draft", "disabled", "unknown"])
@pytest.mark.parametrize("plain_state", [False, True])
def test_catalog_includes_all_usable_skill_states(state, plain_state):
    skill = _skill("installed-skill", state=state)
    if plain_state:
        skill.state = state
    rows = build_catalog({}, [skill])
    expected = ["skill:installed-skill"] if state in {"active", "validated"} else []
    assert [row.id for row in rows] == expected


def test_select_tools_all_mode_focus_first_denies_out():
    picked = select_tools(
        TOOLS,
        grant_mode="all",
        grants=[],
        focus=["plugin:gmail"],
        denies=["core:wiki-recall"],
    )
    names = list(picked)
    assert names[0] == "gmail"
    assert "wiki-recall" not in names
    assert "spawn-worker" not in names and "navigate" not in names
    assert names[1:] == sorted(names[1:], key=lambda n: capability_id_for_tool(n) or "")


def test_select_tools_allowlist_mode():
    picked = select_tools(
        TOOLS,
        grant_mode="allowlist",
        grants=["plugin:gmail", "core:search-web", "plugin:spawn-worker"],
        focus=[],
        denies=[],
    )
    assert set(picked) == {"gmail", "search-web"}


def test_select_tools_is_deterministic():
    a = list(select_tools(TOOLS, grant_mode="all", grants=[], focus=["cli:gh"], denies=[]))
    b = list(
        select_tools(
            dict(reversed(list(TOOLS.items()))),
            grant_mode="all",
            grants=[],
            focus=["cli:gh"],
            denies=[],
        )
    )
    assert a == b
