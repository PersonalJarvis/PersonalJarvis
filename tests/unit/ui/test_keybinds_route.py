"""GET/PUT /api/settings/keybinds — the editable voice keybinds.

The exact-dict assertions below are a deliberate tripwire, not an oversight: a
keybind action that reaches ``KEYBIND_ACTIONS`` but not this route is the AP-4
drift class (the UI renders a row whose value and default are both undefined).
Extend them when an action is added; never relax them to a subset check.

The route serves the FULL action set on purpose, and must keep doing so even
though the Settings panel now renders only Call and Hangup. The two surfaces
show different ROWS; they must not be given different DATA. A Settings panel
that could not see the dictation combos would lose collision detection against
them, and the user could bind Call onto a dictation chord with no warning until
the server rejected the save.
"""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.core.config import TriggerConfig
from jarvis.core.config_writer import KEYBIND_ACTIONS, KEYBIND_TOML_KEY
from jarvis.trigger.hotkey import validate_hotkey
from jarvis.ui.web.settings_routes import router


def _client(**trig) -> TestClient:
    defaults = dict(
        hotkey="ctrl+right_alt+j",
        hotkey_call="f3+f4",
        hotkey_hangup="f1+f2",
        push_to_talk=True,
    )
    defaults.update(trig)
    app = FastAPI()
    app.include_router(router)
    app.state.config = SimpleNamespace(trigger=SimpleNamespace(**defaults))
    return TestClient(app)


def test_get_returns_every_action_plus_defaults() -> None:
    body = _client().get("/api/settings/keybinds").json()
    # The namespace above carries no dictation fields, so both dictation values
    # fall back to the TriggerConfig defaults — which is exactly the fresh
    # install a downloader gets.
    assert body["keybinds"] == {
        "call": "f3+f4",
        "hangup": "f1+f2",
        "dictate": "ctrl+right_alt+j",
        "dictate_toggle": "ctrl+right_alt+space",
        "paste_last": "ctrl+alt+v",
    }
    assert body["defaults"] == {
        "call": "f3+f4",
        "hangup": "f1+f2",
        "dictate": "ctrl+right_alt+j",
        "dictate_toggle": "ctrl+right_alt+space",
        "paste_last": "ctrl+alt+v",
    }
    assert "push_to_talk" not in body
    assert body["restart_required"] is True
    assert len(body["suggestions"]) >= 3


def test_get_covers_exactly_the_registered_actions() -> None:
    """No action may exist in the registry without a value AND a default."""
    body = _client().get("/api/settings/keybinds").json()
    assert set(body["keybinds"]) == set(KEYBIND_ACTIONS)
    assert set(body["defaults"]) == set(KEYBIND_ACTIONS)


def test_defaults_are_derived_from_the_config_model() -> None:
    """A hardcoded defaults dict is the AP-4 trap this route used to carry."""
    body = _client().get("/api/settings/keybinds").json()
    model = TriggerConfig()
    assert body["defaults"] == {
        action: getattr(model, field) for action, field in KEYBIND_TOML_KEY.items()
    }


def test_shipped_defaults_are_valid_on_every_supported_platform() -> None:
    """A default that fails validation is a shortcut nobody can re-save."""
    model = TriggerConfig()
    for field in KEYBIND_TOML_KEY.values():
        combo = getattr(model, field)
        if not combo:
            continue
        for platform in ("win32", "darwin", "linux"):
            ok, reason = validate_hotkey(combo, platform=platform)
            assert ok is True, f"{field}={combo} rejected on {platform}: {reason}"


def test_shipped_defaults_do_not_collide_with_each_other() -> None:
    """The polling backend matches subsets, so any subset/superset pair fires
    two actions at once. All four shipped combos must be mutually disjoint."""
    model = TriggerConfig()
    sets = {
        action: {p for p in getattr(model, field).split("+") if p}
        for action, field in KEYBIND_TOML_KEY.items()
        if getattr(model, field)
    }
    for left, left_keys in sets.items():
        for right, right_keys in sets.items():
            if left >= right:
                continue
            assert not (left_keys <= right_keys or right_keys <= left_keys), (
                f"{left} and {right} overlap"
            )


def test_suggestions_exclude_combos_that_would_be_rejected() -> None:
    """A quick-pick that collides with a bound action is a guaranteed 400."""
    body = _client().get("/api/settings/keybinds").json()
    bound = [
        {p for p in combo.split("+") if p}
        for combo in body["keybinds"].values()
        if combo
    ]
    for suggestion in body["suggestions"]:
        keys = {p for p in suggestion.split("+") if p}
        assert not any(keys <= other or other <= keys for other in bound), suggestion


def test_suggestions_are_valid_on_every_supported_platform() -> None:
    from jarvis.ui.web.settings_routes import _KEYBIND_SUGGESTIONS

    assert len(_KEYBIND_SUGGESTIONS) >= 6
    for suggestion in _KEYBIND_SUGGESTIONS:
        for platform in ("win32", "darwin", "linux"):
            ok, reason = validate_hotkey(suggestion, platform=platform)
            assert ok is True, f"{suggestion} rejected on {platform}: {reason}"


def test_put_dictate_toggle_is_accepted() -> None:
    body = _client().put(
        "/api/settings/keybinds",
        json={"action": "dictate_toggle", "hotkey": "CTRL+SHIFT+D", "persist": False},
    ).json()
    assert body["ok"] is True
    assert body["action"] == "dictate_toggle"
    assert body["hotkey"] == "ctrl+shift+d"


def test_put_dictate_toggle_live_applies_under_its_action_name() -> None:
    """The pipeline kwarg must be spelled exactly like the action, or a
    successful save silently re-arms nothing."""
    calls: list[dict] = []

    class _FakePipeline:
        def set_keybinds(self, **kw):  # noqa: ANN003
            calls.append(kw)

    client = _client()
    client.app.state.speech_pipeline = _FakePipeline()
    resp = client.put(
        "/api/settings/keybinds",
        json={"action": "dictate_toggle", "hotkey": "ctrl+shift+d", "persist": False},
    )
    assert resp.json()["applied_live"] is True
    assert calls == [{"dictate_toggle": ["ctrl+shift+d"]}]


def test_put_rejects_a_dictation_combo_that_collides_with_the_other_one() -> None:
    """Both dictation rows are bound by default, so they must guard each other."""
    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "dictate", "hotkey": "ctrl+right_alt+space", "persist": False},
    )
    assert resp.status_code == 400
    assert "dictate_toggle" in resp.json()["detail"]


def test_put_paste_last_is_accepted_and_live_applies_under_its_action_name() -> None:
    """"Insert the last dictation again" is a first-class action, not a rider
    on the dictation keys: it needs no microphone and no provider, so it stays
    useful on a host where dictation itself cannot run."""
    calls: list[dict] = []

    class _FakePipeline:
        def set_keybinds(self, **kw):  # noqa: ANN003
            calls.append(kw)

    client = _client()
    client.app.state.speech_pipeline = _FakePipeline()
    resp = client.put(
        "/api/settings/keybinds",
        json={"action": "paste_last", "hotkey": "CTRL+SHIFT+B", "persist": False},
    )
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "paste_last"
    assert body["hotkey"] == "ctrl+shift+b"
    assert calls == [{"paste_last": ["ctrl+shift+b"]}]


def test_put_paste_last_guards_against_colliding_with_dictation() -> None:
    """All five ship bound, so every pair has to guard every other pair."""
    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "paste_last", "hotkey": "ctrl+right_alt+j", "persist": False},
    )
    assert resp.status_code == 400
    assert "dictate" in resp.json()["detail"]


def test_paste_last_can_be_cleared_like_any_other_action() -> None:
    """Unbound is a valid state: the action is also
    ``jarvis api dictation paste-last``, which is the Wayland path."""
    client = _client()
    resp = client.put(
        "/api/settings/keybinds",
        json={"action": "paste_last", "hotkey": "", "persist": False},
    )
    assert resp.status_code == 200
    assert client.get("/api/settings/keybinds").json()["keybinds"]["paste_last"] == ""


def test_the_route_serves_every_action_even_though_settings_shows_two() -> None:
    """The Settings panel renders Call + Hangup only; the PAYLOAD stays whole.

    Trimming it there would strip Call/Hangup of collision detection against
    the dictation combos — the surfaces differ in rows, never in data.
    """
    body = _client().get("/api/settings/keybinds").json()
    for action in ("dictate", "dictate_toggle", "paste_last"):
        assert action in body["keybinds"], action
        assert action in body["defaults"], action


def test_put_rejects_a_left_alt_spelling_of_a_bound_right_alt_shortcut() -> None:
    """THE collision bug: the route compared RAW tokens, so two spellings of
    ONE registration both passed. ``ctrl+left_alt+k`` and ``ctrl+right_alt+k``
    normalize to the same chord (Windows cannot tell the Alt keys apart), so
    saving both left the user with two bound-looking rows, one of them silently
    dead. The route must delegate to ``combos_collide`` and refuse with a
    reason that names the other action."""
    client = _client()
    assert (
        client.put(
            "/api/settings/keybinds",
            json={"action": "hangup", "hotkey": "ctrl+right_alt+k", "persist": False},
        ).status_code
        == 200
    )

    resp = client.put(
        "/api/settings/keybinds",
        json={"action": "call", "hotkey": "ctrl+left_alt+k", "persist": False},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "hangup" in detail
    assert "ctrl+right_alt+k" in detail


def test_put_rejects_a_super_spelling_of_a_bound_win_shortcut() -> None:
    """Same root cause, other fold: win / super / meta are one key."""
    client = _client()
    assert (
        client.put(
            "/api/settings/keybinds",
            json={"action": "hangup", "hotkey": "ctrl+win+k", "persist": False},
        ).status_code
        == 200
    )
    resp = client.put(
        "/api/settings/keybinds",
        json={"action": "call", "hotkey": "ctrl+super+k", "persist": False},
    )
    assert resp.status_code == 400
    assert "hangup" in resp.json()["detail"]


def test_suggestions_are_filtered_on_the_normalized_collision_rule(monkeypatch) -> None:
    """A quick-pick that merely SPELLS a bound chord differently is still a
    guaranteed 400 on click, so it must not be offered."""
    from jarvis.ui.web import settings_routes

    monkeypatch.setattr(
        settings_routes, "_KEYBIND_SUGGESTIONS", ["ctrl+left_alt+j", "ctrl+shift+j"]
    )
    body = _client().get("/api/settings/keybinds").json()
    # dictate ships as ctrl+right_alt+j — the same registration.
    assert body["suggestions"] == ["ctrl+shift+j"]


# ----------------------------------------------------------------------
# Mouse-button shortcuts: offered only where the host can actually fire one.
# ----------------------------------------------------------------------


def _patch_mouse_probe(monkeypatch, supported: bool, reason: str = "") -> None:
    """Pin the capability probe so the assertions do not depend on the host."""
    from jarvis.trigger import hotkey as hotkey_mod

    monkeypatch.setattr(hotkey_mod, "mouse_hotkeys_available", lambda *a, **k: (supported, reason))


def test_get_reports_mouse_button_support_from_the_capability_probe(monkeypatch) -> None:
    """The UI hides the mouse cluster on a host that cannot fire one; it can
    only do that if the route SERVES the probe's verdict."""
    _patch_mouse_probe(monkeypatch, True)
    body = _client().get("/api/settings/keybinds").json()
    assert body["mouse_buttons"] == {"supported": True, "reason": ""}


def test_get_reports_an_honest_english_reason_when_mouse_is_unsupported(
    monkeypatch,
) -> None:
    _patch_mouse_probe(
        monkeypatch,
        False,
        "Wayland does not let an application watch the mouse buttons globally.",
    )
    body = _client().get("/api/settings/keybinds").json()
    assert body["mouse_buttons"]["supported"] is False
    assert "Wayland" in body["mouse_buttons"]["reason"]


def test_put_refuses_a_mouse_shortcut_the_host_can_never_fire(monkeypatch) -> None:
    """Accepting it would leave the user with a shortcut that looks bound and
    does nothing — the exact silent dishonesty the probe exists to prevent."""
    _patch_mouse_probe(monkeypatch, False, "Mouse-button shortcuts need the pynput package.")
    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "call", "hotkey": "ctrl+alt+mouse_x1", "persist": False},
    )
    assert resp.status_code == 400
    assert "pynput" in resp.json()["detail"]


def test_put_refuses_an_ALIAS_spelling_of_an_unsupported_mouse_button(
    monkeypatch,
) -> None:
    """``mouse_back`` is ``mouse_x1`` — the check reads the NORMALIZED tokens,
    so a friendlier spelling cannot walk around the probe."""
    _patch_mouse_probe(monkeypatch, False, "This system cannot watch mouse buttons.")
    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "call", "hotkey": "ctrl+alt+mouse_back", "persist": False},
    )
    assert resp.status_code == 400


def test_put_accepts_a_mouse_shortcut_where_the_probe_says_it_works(monkeypatch) -> None:
    _patch_mouse_probe(monkeypatch, True)
    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "call", "hotkey": "ctrl+alt+mouse_x1", "persist": False},
    )
    assert resp.status_code == 200
    assert resp.json()["hotkey"] == "ctrl+alt+mouse_x1"


def test_put_does_not_consult_the_mouse_probe_for_a_key_only_combo(monkeypatch) -> None:
    """A keyboard shortcut must stay saveable on a host without mouse support —
    the probe gates the mouse cluster, never the whole route."""
    _patch_mouse_probe(monkeypatch, False, "No global mouse buttons here.")
    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "call", "hotkey": "f7+f8", "persist": False},
    )
    assert resp.status_code == 200


def test_retired_ptt_hotkey_route_is_not_mounted() -> None:
    assert _client().get("/api/settings/ptt-hotkey").status_code == 404


def test_put_call_accepts_and_normalizes_case() -> None:
    body = _client().put(
        "/api/settings/keybinds",
        json={"action": "call", "hotkey": "F7+F8", "persist": False},
    ).json()
    assert body["ok"] is True
    assert body["action"] == "call"
    assert body["hotkey"] == "f7+f8"
    assert body["restart_required"] is True


def test_put_accepts_a_risky_combo_but_says_what_it_costs() -> None:
    """A bare typing key is allowed now, and the answer explains the cost.

    The maintainer's requirement is that ANY combination be usable. What used
    to be a refusal is a caution: the save goes through and the response
    carries a finished sentence the UI shows.
    """
    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "hangup", "hotkey": "j", "persist": False},
    )
    assert resp.status_code == 200
    assert resp.json()["cautions"], "a risky combo must never be accepted silently"


def test_put_rejects_unknown_action() -> None:
    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "mute", "hotkey": "f7+f8", "persist": False},
    )
    assert resp.status_code == 400


def test_put_rejects_retired_ptt_action() -> None:
    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "ptt", "hotkey": "ctrl+alt+m", "persist": False},
    )
    assert resp.status_code == 400


def test_put_rejects_collision_with_other_action() -> None:
    # call defaults to f3+f4; binding hangup to the same combo must be rejected.
    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "hangup", "hotkey": "f3+f4", "persist": False},
    )
    assert resp.status_code == 400
    assert "call" in resp.json()["detail"]


def test_put_rejects_subset_collision() -> None:
    """A combo CONTAINED in another action's combo is accepted with a caution.

    It used to be refused, and that refusal is what broke the headline
    requirement: a modifier-only chord like ctrl+alt is a subset of nearly
    every other shortcut, so almost nothing could be saved. The overlap is
    real — pressing the longer chord fires both — so the answer names the
    other action instead of hiding it.
    """
    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "call", "hotkey": "f1", "persist": False},
    )
    assert resp.status_code == 200
    assert any("hangup" in c for c in resp.json()["cautions"])


def test_put_cautions_on_a_superset_collision() -> None:
    """Superset direction too: with call=f3+f4, binding hangup to f3+f4+f5
    fires call as soon as F3+F4 land mid-chord. Allowed, and said out loud."""
    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "hangup", "hotkey": "f3+f4+f5", "persist": False},
    )
    assert resp.status_code == 200
    assert any("call" in c for c in resp.json()["cautions"])


def test_put_still_refuses_the_very_same_combo_twice() -> None:
    """The ONE overlap nothing can resolve stays a refusal.

    Two actions on the identical registration give the backend one chord and
    no way to say which action was meant. A caution would be useless there —
    there is no behaviour for the user to accept, only an ambiguity.
    """
    client = _client()
    current = client.get("/api/settings/keybinds").json()["keybinds"]["call"]
    resp = client.put(
        "/api/settings/keybinds",
        json={"action": "hangup", "hotkey": current, "persist": False},
    )
    assert resp.status_code == 400
    assert "call" in resp.json()["detail"]


def test_put_allows_disjoint_combo_sharing_a_modifier() -> None:
    """Sharing a MODIFIER is fine — ctrl+shift+h vs ctrl+right_alt+j do not
    overlap as chords; only key-set subset/superset relations collide."""
    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "hangup", "hotkey": "ctrl+shift+h", "persist": False},
    )
    assert resp.status_code == 200


def test_put_in_memory_update_reflects_in_get() -> None:
    client = _client()
    client.put(
        "/api/settings/keybinds",
        json={"action": "call", "hotkey": "f7+f8", "persist": False},
    )
    body = client.get("/api/settings/keybinds").json()
    assert body["keybinds"]["call"] == "f7+f8"


def test_put_live_applies_to_running_pipeline() -> None:
    """When a voice pipeline is live, the PUT re-arms it immediately (no
    restart) and reports applied_live + restart_required False."""
    calls: list[dict] = []

    class _FakePipeline:
        def set_keybinds(self, **kw):  # noqa: ANN003
            calls.append(kw)

    client = _client()
    client.app.state.speech_pipeline = _FakePipeline()
    resp = client.put(
        "/api/settings/keybinds",
        json={"action": "call", "hotkey": "f7+f8", "persist": False},
    )
    body = resp.json()
    assert body["applied_live"] is True
    assert body["restart_required"] is False
    # Only the changed action is re-armed, as a single-combo list.
    assert calls == [{"call": ["f7+f8"]}]


def test_put_live_apply_failure_still_persists(monkeypatch) -> None:
    """A live-apply hiccup must NOT fail the save — it falls back to restart."""
    from jarvis.core import config_writer

    monkeypatch.setattr(config_writer, "set_keybind", lambda *a, **k: None)

    class _BoomPipeline:
        def set_keybinds(self, **kw):  # noqa: ANN003
            raise RuntimeError("pipeline busy")

    client = _client()
    client.app.state.speech_pipeline = _BoomPipeline()
    resp = client.put(
        "/api/settings/keybinds",
        json={"action": "call", "hotkey": "ctrl+alt+m", "persist": True},
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["persisted"] is True
    assert body["applied_live"] is False
    assert body["restart_required"] is True


def test_put_persist_calls_config_writer(monkeypatch) -> None:
    from jarvis.core import config_writer

    captured: dict = {}

    def _fake_set_keybind(action, hotkey, *, path=None):  # noqa: ANN001
        captured["action"] = action
        captured["hotkey"] = hotkey

    monkeypatch.setattr(config_writer, "set_keybind", _fake_set_keybind)

    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "hangup", "hotkey": "ctrl+shift+h", "persist": True},
    )
    assert resp.status_code == 200
    assert resp.json()["persisted"] is True
    assert captured == {"action": "hangup", "hotkey": "ctrl+shift+h"}


def test_put_empty_hotkey_unbinds_without_validation_error() -> None:
    """An explicit empty hotkey clears the action instead of being rejected
    as an incomplete recording (validate_hotkey normally rejects '')."""
    body = _client().put(
        "/api/settings/keybinds",
        json={"action": "hangup", "hotkey": "", "persist": False},
    ).json()
    assert body["ok"] is True
    assert body["hotkey"] == ""


def test_put_empty_hotkey_skips_collision_check() -> None:
    """Clearing hangup must never be rejected as 'overlapping' with call —
    there is nothing left to collide with."""
    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "hangup", "hotkey": "", "persist": False},
    )
    assert resp.status_code == 200


def test_put_after_clearing_other_action_still_allows_a_new_combo() -> None:
    """Regression for the false-positive collision bug: an unbound OTHER
    action's empty key-set must not be treated as a subset of every new
    combo (an empty set is a mathematical subset of everything), which would
    otherwise reject every future save once any one action is cleared."""
    client = _client()
    client.put(
        "/api/settings/keybinds",
        json={"action": "hangup", "hotkey": "", "persist": False},
    )
    resp = client.put(
        "/api/settings/keybinds",
        json={"action": "call", "hotkey": "f7+f8", "persist": False},
    )
    assert resp.status_code == 200


def test_put_empty_hotkey_live_applies_empty_list() -> None:
    """The running pipeline is re-armed with an empty list (not [\"\"])."""
    calls: list[dict] = []

    class _FakePipeline:
        def set_keybinds(self, **kw):  # noqa: ANN003
            calls.append(kw)

    client = _client()
    client.app.state.speech_pipeline = _FakePipeline()
    resp = client.put(
        "/api/settings/keybinds",
        json={"action": "call", "hotkey": "", "persist": False},
    )
    assert resp.json()["applied_live"] is True
    assert calls == [{"call": []}]


def test_get_reflects_cleared_keybind() -> None:
    client = _client()
    client.put(
        "/api/settings/keybinds",
        json={"action": "call", "hotkey": "", "persist": False},
    )
    body = client.get("/api/settings/keybinds").json()
    assert body["keybinds"]["call"] == ""
