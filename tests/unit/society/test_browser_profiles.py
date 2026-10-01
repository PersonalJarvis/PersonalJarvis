"""Profile persistence, explicit sharing and pairing revocation without browsers."""

from types import SimpleNamespace

import pytest

from jarvis.society.browser.profiles import BrowserProfiles, normalize_domains


def agent(aid="lead", domains=()):
    return SimpleNamespace(agent_id=aid, browser_mode="own", browser_allowed_domains=domains)


def test_existing_agent_keeps_its_original_profile(tmp_path):
    registry = BrowserProfiles(tmp_path)
    assert not registry.path.exists()  # Construction does not touch the boot path.
    binding = registry.resolve(agent())
    assert binding.profile_id is None
    assert binding.path == tmp_path / "society/lead/browser-profile"


def test_all_includes_future_agents_and_survives_registry_restart(tmp_path):
    registry = BrowserProfiles(tmp_path)
    profile = registry.create("Work", "managed", [])
    registry.share(profile["id"], "all", [], {"lead", "scout"})
    restarted = BrowserProfiles(tmp_path)
    assert restarted.resolve(agent()).profile_id == profile["id"]
    assert restarted.resolve(agent("future")).profile_id == profile["id"]
    restarted.assign("scout", "own", None)
    assert restarted.resolve(agent("scout")).profile_id is None
    assert restarted.resolve(agent()).profile_id == profile["id"]


def test_selected_is_exact_and_does_not_change_other_profile_bindings(tmp_path):
    registry = BrowserProfiles(tmp_path)
    first = registry.create("Work", "managed", [])
    second = registry.create("Other", "managed", [])
    registry.assign("other", "profile", second["id"])
    registry.share(first["id"], "all", [], {"lead", "scout", "other"})
    registry.assign("other", "profile", second["id"])
    registry.share(first["id"], "selected", ["scout"], {"lead", "scout", "other"})
    assert registry.resolve(agent("scout")).profile_id == first["id"]
    with pytest.raises(ValueError, match="choose a profile"):
        registry.resolve(agent())
    assert registry.resolve(agent("future")).profile_id is None
    assert registry.resolve(agent("other")).profile_id == second["id"]


def test_revocation_never_falls_back_to_old_logged_in_account(tmp_path):
    registry = BrowserProfiles(tmp_path)
    profile = registry.create("Work", "managed", [])
    registry.share(profile["id"], "all", [], {"lead"})
    folder = registry.resolve(agent()).path
    folder.mkdir(parents=True)
    marker = folder / "retained-profile"
    marker.write_text("fixture", encoding="utf-8")
    registry.remove(profile["id"])
    with pytest.raises(ValueError, match="choose a profile"):
        registry.resolve(agent())
    assert marker.read_text(encoding="utf-8") == "fixture"
    registry.assign("lead", "own", None)
    assert registry.resolve(agent()).profile_id is None


def test_pairing_is_single_use_persistent_and_bound_to_installation(tmp_path):
    registry = BrowserProfiles(tmp_path)
    pid = registry.create("Chrome", "chrome", ["x.com"])["id"]
    code = registry.pair_code(pid)
    connection = registry.pair(code, "installation-a")
    reopened = BrowserProfiles(tmp_path)
    assert reopened.authenticate(pid, connection["token"], "installation-a")
    assert not reopened.authenticate(pid, connection["token"], "installation-b")
    with pytest.raises(ValueError, match="expired or invalid"):
        reopened.pair(code, "installation-a")
    assert connection["token"].encode() not in registry.path.read_bytes()
    assert code.encode() not in registry.path.read_bytes()
    replacement = reopened.pair(reopened.pair_code(pid), "installation-b")
    assert not reopened.authenticate(pid, connection["token"], "installation-a")
    assert reopened.authenticate(pid, replacement["token"], "installation-b")
    reopened.remove(pid)
    assert not reopened.authenticate(pid, replacement["token"], "installation-b")


def test_expired_pairing_does_not_bind(tmp_path, monkeypatch):
    registry = BrowserProfiles(tmp_path)
    pid = registry.create("Chrome", "chrome", ["x.com"])["id"]
    code = registry.pair_code(pid)
    monkeypatch.setattr("jarvis.society.browser.profiles.time.time", lambda: 999999999999)
    with pytest.raises(ValueError, match="expired"):
        registry.pair(code, "installation")


def test_profile_domain_grant_cannot_expand_agent_grant(tmp_path):
    registry = BrowserProfiles(tmp_path)
    pid = registry.create("Chrome", "chrome", ["example.com"])["id"]
    registry.assign("lead", "profile", pid)
    assert registry.resolve(agent(domains=["news.example.com"])).domains == ("news.example.com",)
    with pytest.raises(ValueError, match="no allowed websites"):
        registry.resolve(agent(domains=["unrelated.org"]))


@pytest.mark.parametrize(
    "domain", ["*", "*.x.com", "localhost", "127.0.0.1", "foo.local", "https://x.com", "x.com/path"]
)
def test_domains_reject_local_or_ambiguous_access(domain):
    with pytest.raises(ValueError):
        normalize_domains([domain])


def test_unknown_assignment_is_atomic_and_snapshot_contains_no_credentials(tmp_path):
    registry = BrowserProfiles(tmp_path)
    pid = registry.create("Chrome", "chrome", ["x.com"])["id"]
    registry.pair(registry.pair_code(pid), "installation")
    with pytest.raises(ValueError):
        registry.share(pid, "selected", ["unknown"], {"lead"})
    snapshot = registry.snapshot([{"agent_id": "lead", "name": "Lead"}], {pid})
    assert snapshot["bindings"]["lead"]["effective_profile_id"] is None
    assert set(snapshot["profiles"][0]) == {
        "id",
        "name",
        "kind",
        "allowed_domains",
        "connected",
        "agent_ids",
        "is_default",
    }
