"""Per-client MCP credentials: issue, scope, verify, revoke.

The properties that matter are the ones a leak or a mistake would expose: the
secret is never persisted, a revoked token is dead immediately, and a scope
cannot be talked past — a `read` token does not merely get refused, it is never
offered the tools it may not call.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.mcp.agents import tokens as tk


@pytest.fixture
def store(tmp_path: Path) -> tk.TokenStore:
    return tk.TokenStore(tmp_path / "mcp_tokens.json")


def test_a_token_is_returned_once_and_stored_only_as_a_hash(store: tk.TokenStore) -> None:
    token, secret = store.issue(name="Claude Desktop - MacBook", scope="work")

    assert secret.startswith("jarvis_mcp_")
    assert token.token_id in secret
    assert secret not in json.dumps(json.loads(store.path.read_text(encoding="utf-8")))
    # The public view is what a UI or a log may show.
    public = token.to_public()
    assert "secret_hash" not in public
    assert public["name"] == "Claude Desktop - MacBook"
    assert public["revoked"] is False


def test_verify_accepts_the_real_secret_and_nothing_else(store: tk.TokenStore) -> None:
    token, secret = store.issue(name="Cursor", scope="read")

    assert store.verify(secret) is not None
    assert store.verify(secret).token_id == token.token_id
    assert store.verify(secret + "x") is None
    assert store.verify(f"jarvis_mcp_{token.token_id}_wrong") is None
    assert store.verify("some-control-key") is None
    assert store.verify(None) is None
    assert store.verify("") is None


def test_verify_stamps_last_use(store: tk.TokenStore) -> None:
    _token, secret = store.issue(name="Phone")
    assert store.list()[0].last_used_ms == 0

    store.verify(secret)

    assert store.list()[0].last_used_ms > 0, "the owner must be able to see what is in use"


def test_revoking_one_client_leaves_the_others(store: tk.TokenStore) -> None:
    keep, keep_secret = store.issue(name="Laptop")
    drop, drop_secret = store.issue(name="Old phone")

    store.revoke(drop.token_id)

    assert store.verify(drop_secret) is None
    assert store.verify(keep_secret) is not None
    assert {t.token_id for t in store.list()} == {keep.token_id}
    # The record survives revocation, so "what happened to that client?" has an answer.
    assert drop.token_id in {t.token_id for t in store.list(include_revoked=True)}


def test_tokens_survive_a_reload(tmp_path: Path) -> None:
    first = tk.TokenStore(tmp_path / "mcp_tokens.json")
    _token, secret = first.issue(name="Desktop")

    second = tk.TokenStore(tmp_path / "mcp_tokens.json")

    assert second.verify(secret) is not None


def test_a_corrupt_file_does_not_lock_anyone_out(tmp_path: Path) -> None:
    path = tmp_path / "mcp_tokens.json"
    path.write_text("{ not json at all", encoding="utf-8")
    store = tk.TokenStore(path)

    assert store.list() == []
    # And it can still be written to — the owner re-pairs and moves on.
    _token, secret = store.issue(name="Fresh")
    assert store.verify(secret) is not None


def test_issue_refuses_a_nameless_or_unknown_scope(store: tk.TokenStore) -> None:
    with pytest.raises(tk.TokenError, match="needs a name"):
        store.issue(name="   ")
    with pytest.raises(tk.TokenError, match="scope must be"):
        store.issue(name="X", scope="admin")


# ------------------------------------------------------------------ scopes


def test_read_scope_gets_no_spending_tool() -> None:
    assert tk.allows("read", "agents_list", dangerous=False) is True
    assert tk.allows("read", "ecosystem_status", dangerous=False) is True
    assert tk.allows("read", "agent_chat", dangerous=True) is False
    assert tk.allows("read", "quest_post", dangerous=True) is False
    assert tk.allows("read", "kill_switch", dangerous=True) is False


def test_work_scope_spends_but_does_not_govern() -> None:
    assert tk.allows("work", "agent_chat", dangerous=True) is True
    assert tk.allows("work", "quest_post", dangerous=True) is True
    assert tk.allows("work", "agent_assign", dangerous=True) is True
    # Governance decides what the ecosystem may do — that stays with the owner.
    assert tk.allows("work", "kill_switch", dangerous=True) is False
    assert tk.allows("work", "approval_resolve", dangerous=True) is False


def test_full_scope_is_everything() -> None:
    for name in ("kill_switch", "approval_resolve", "agent_chat", "agents_list"):
        assert tk.allows("full", name, dangerous=True) is True


def test_every_dangerous_tool_is_classified() -> None:
    """No tool may be dangerous and yet fall outside all three scopes."""
    from jarvis.mcp.agents import TOOLS

    for tool in TOOLS:
        reachable = [s for s in tk.SCOPES if tk.allows(s, tool.name, dangerous=tool.dangerous)]
        assert reachable, f"{tool.name} is callable by no scope at all"
        if not tool.dangerous:
            assert reachable == list(tk.SCOPES), f"{tool.name} is safe but not readable"


def test_looks_like_token_separates_credentials(store: tk.TokenStore) -> None:
    _token, secret = store.issue(name="X")
    assert tk.looks_like_token(secret) is True
    assert tk.looks_like_token("a-plain-control-key") is False
    assert tk.looks_like_token(None) is False
