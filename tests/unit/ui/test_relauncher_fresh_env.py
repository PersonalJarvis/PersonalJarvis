"""The relauncher must NOT fossilize the ``JARVIS__*`` env config layer.

Live case (2026-07-17): every restarted app inherited the ``JARVIS__TTS__*``
values captured when the FIRST tray process started. A voice fix that updated
all three pinned config layers (jarvis.toml + config-soll.json + the user's  # i18n-allow: config-soll.json is a filename
persisted environment) kept being overridden by the stale inherited copy on
every ``restart-app`` — the replaced TTS voice resurrected after each restart.

``fresh_user_env`` re-reads the persisted ``JARVIS__*`` overrides (Windows:
HKCU Environment) for the NEW launcher; ``main`` passes that environment to
the spawn. On hosts with no persisted user env (POSIX) the inherited env is
already the freshest source and stays untouched.
"""
from __future__ import annotations

from types import SimpleNamespace

from jarvis.ui import relauncher


def test_fresh_env_replaces_stale_jarvis_overrides():
    base = {
        "PATH": r"C:\bin",
        "JARVIS__TTS__VOICE_DE": "Kore",   # fossilized in the dying process
        "JARVIS__TTS__VOICE_EN": "Kore",
    }
    persisted = {
        "JARVIS__TTS__VOICE_DE": "Charon",  # what the user's env says NOW
        "JARVIS__TTS__VOICE_EN": "Charon",
    }
    env = relauncher.fresh_user_env(base, _read_persisted=lambda: persisted)
    assert env["JARVIS__TTS__VOICE_DE"] == "Charon"
    assert env["JARVIS__TTS__VOICE_EN"] == "Charon"
    assert env["PATH"] == r"C:\bin"  # non-JARVIS keys stay inherited


def test_fresh_env_drops_jarvis_keys_deleted_from_the_persisted_env():
    base = {"JARVIS__TTS__SEED": "7", "HOME": "/home/u"}
    env = relauncher.fresh_user_env(base, _read_persisted=dict)
    assert "JARVIS__TTS__SEED" not in env
    assert env["HOME"] == "/home/u"


def test_fresh_env_is_a_no_op_without_a_persisted_source():
    base = {"JARVIS__TTS__VOICE_DE": "Kore", "PATH": "/bin"}
    env = relauncher.fresh_user_env(base, _read_persisted=lambda: None)
    assert env == base  # POSIX / unreadable registry → inherited env unchanged


def test_fresh_env_key_matching_is_case_insensitive():
    base = {"jarvis__tts__voice_de": "Kore"}
    persisted = {"JARVIS__TTS__VOICE_DE": "Charon"}
    env = relauncher.fresh_user_env(base, _read_persisted=lambda: persisted)
    assert env.get("JARVIS__TTS__VOICE_DE") == "Charon"
    assert "jarvis__tts__voice_de" not in env


def test_main_spawns_the_new_launcher_with_the_fresh_env(monkeypatch):
    fresh = {"JARVIS__TTS__VOICE_DE": "Charon", "PATH": "/bin"}
    monkeypatch.setattr(relauncher, "fresh_user_env", lambda *a, **k: dict(fresh))
    spawned: list[dict] = []

    def fake_spawn(cmd, **kwargs):
        spawned.append({"cmd": cmd, "kwargs": kwargs})
        return SimpleNamespace(pid=999)

    rc = relauncher.main(
        ["4242", "repo"],
        _wait=lambda pid, **_kw: True,
        _spawn=fake_spawn,
        _sleep=lambda _s: None,
        _alive=lambda _p: True,
        _finalize_update=lambda _cwd: True,
    )
    assert rc == 0
    assert spawned[0]["kwargs"]["env"] == fresh
