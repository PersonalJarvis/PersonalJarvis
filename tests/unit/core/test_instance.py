"""The *instance* contract (``jarvis.core.instance``): two desktop apps from one
checkout — the default app and a freely restartable ``dev`` app — must never
collide on data, ports, lock or OS identity, and the dev app must leave the
ambient duties (wake word, hotkeys, channels, autostart) to the default one.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jarvis.core import instance as inst
from jarvis.core.branding import (
    PRODUCT_NAME,
    WINDOWS_APP_USER_MODEL_ID,
    WINDOWS_BRANDED_LAUNCHER_FILE_NAME,
    WINDOWS_MUTEX_NAME,
    WINDOWS_SHORTCUT_FILE_NAME,
)
from jarvis.core.instance_data import SEED_FILES, ensure_instance_data_dir

# ---------------------------------------------------------------------------
# Name resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [None, "", "  ", "default", "DEFAULT"])
def test_blank_or_default_means_default(raw) -> None:
    assert inst.normalize_instance_name(raw) == inst.DEFAULT_INSTANCE_NAME


@pytest.mark.parametrize("raw", ["dev", "Dev", " DEV "])
def test_dev_is_case_insensitive(raw) -> None:
    assert inst.normalize_instance_name(raw) == inst.DEV_INSTANCE_NAME


@pytest.mark.parametrize("raw", ["dev2", "prod", "../x", "default app"])
def test_unknown_name_fails_loudly(raw) -> None:
    # A typo must not silently boot a second DEFAULT app that fights the live
    # one over its lock and port.
    with pytest.raises(inst.InstanceNameError):
        inst.normalize_instance_name(raw)


def test_resolve_reads_the_env_var(monkeypatch) -> None:
    monkeypatch.delenv(inst.INSTANCE_ENV_VAR, raising=False)
    assert inst.current_instance().is_default
    monkeypatch.setenv(inst.INSTANCE_ENV_VAR, "dev")
    assert inst.current_instance().is_dev


def test_select_instance_pins_and_clears_the_env(monkeypatch) -> None:
    monkeypatch.delenv(inst.INSTANCE_ENV_VAR, raising=False)
    dev = inst.select_instance("dev")
    assert dev.is_dev
    assert inst.resolve_instance().is_dev
    default = inst.select_instance(None)
    assert default.is_default
    assert inst.INSTANCE_ENV_VAR not in __import__("os").environ


def test_env_var_is_single_underscore_so_restarts_keep_it() -> None:
    # The relauncher strips every JARVIS__* (double underscore) config override
    # on an in-app restart; the instance must survive exactly that restart.
    assert inst.INSTANCE_ENV_VAR.startswith("JARVIS_")
    assert not inst.INSTANCE_ENV_VAR.startswith("JARVIS__")


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_default_identity_is_the_stable_branding() -> None:
    d = inst.InstanceIdentity(inst.DEFAULT_INSTANCE_NAME)
    assert d.display_name == PRODUCT_NAME
    assert d.windows_aumid == WINDOWS_APP_USER_MODEL_ID
    assert d.windows_mutex_name == WINDOWS_MUTEX_NAME
    assert d.windows_shortcut_file_name == WINDOWS_SHORTCUT_FILE_NAME
    assert d.windows_branded_launcher_file_name == WINDOWS_BRANDED_LAUNCHER_FILE_NAME
    assert d.data_dir_name == "data"
    assert d.port_offset == 0 and d.port(47821) == 47821
    assert d.icon_file_name == inst.DEFAULT_ICON_FILE_NAME
    assert d.label == "" and d.state_file_suffix == ""
    assert d.launcher_args == ()
    assert d.owns_ambient_duties


def test_dev_identity_differs_on_every_colliding_surface() -> None:
    d = inst.InstanceIdentity(inst.DEFAULT_INSTANCE_NAME)
    v = inst.InstanceIdentity(inst.DEV_INSTANCE_NAME)
    assert v.display_name == f"{PRODUCT_NAME} Dev"
    for attr in (
        "display_name",
        "compact_name",
        "slug",
        "data_dir_name",
        "icon_file_name",
        "windows_aumid",
        "windows_mutex_name",
        "windows_shortcut_file_name",
        "windows_branded_launcher_file_name",
        "linux_wm_class",
        "linux_desktop_entry_file_name",
        "macos_bundle_id",
        "state_file_suffix",
    ):
        assert getattr(v, attr) != getattr(d, attr), attr
    assert v.data_dir_name == "data-dev"
    assert v.port(47821) == 47821 + inst.DEV_PORT_OFFSET
    assert v.launcher_args == ("--instance", "dev")
    assert not v.owns_ambient_duties


def test_environ_pins_or_clears_the_variable() -> None:
    dev = inst.InstanceIdentity("dev")
    env = dev.environ({"PATH": "x", inst.INSTANCE_ENV_VAR: "stale"})
    assert env[inst.INSTANCE_ENV_VAR] == "dev" and env["PATH"] == "x"
    env = inst.InstanceIdentity("default").environ({inst.INSTANCE_ENV_VAR: "dev"})
    assert inst.INSTANCE_ENV_VAR not in env


def test_dev_icons_ship_in_both_icon_homes() -> None:
    # Rendered by scripts/make_dev_icon.py; the taskbar/tray/dock resolve them.
    root = Path(inst.__file__).resolve().parents[2]
    for home in (root / "jarvis" / "assets" / "icons", root / "assets" / "icons"):
        assert (home / inst.DEV_ICON_FILE_NAME).is_file(), home
        assert (home / "jarvis-dev.png").is_file(), home


# ---------------------------------------------------------------------------
# Data-dir seeding
# ---------------------------------------------------------------------------


def test_seed_copies_only_the_allowlisted_files(tmp_path: Path) -> None:
    src = tmp_path / "data"
    src.mkdir()
    (src / "setup_state.json").write_text("{}", encoding="utf-8")
    (src / "identity_card.json").write_text("{}", encoding="utf-8")
    (src / "sessions.db").write_text("live state, never copied", encoding="utf-8")
    with sqlite3.connect(src / "chats.db") as db:
        db.execute("CREATE TABLE t (x)")
        db.execute("INSERT INTO t VALUES (1)")
    seeded = ensure_instance_data_dir(tmp_path, inst.InstanceIdentity("dev"))
    assert sorted(seeded) == ["chats.db", "identity_card.json", "setup_state.json"]
    dst = tmp_path / "data-dev"
    assert (dst / "setup_state.json").is_file()
    assert not (dst / "sessions.db").exists()
    with sqlite3.connect(dst / "chats.db") as db:
        assert db.execute("SELECT x FROM t").fetchall() == [(1,)]
    assert set(seeded) <= set(SEED_FILES)


def test_seed_runs_only_once_and_never_for_default(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "setup_state.json").write_text("{}", encoding="utf-8")
    assert ensure_instance_data_dir(tmp_path, inst.InstanceIdentity("default")) == []
    assert not (tmp_path / "data-default").exists()
    assert ensure_instance_data_dir(tmp_path, inst.InstanceIdentity("dev")) == ["setup_state.json"]
    (tmp_path / "data-dev" / "setup_state.json").unlink()
    # Second start: the directory exists → nothing is re-seeded.
    assert ensure_instance_data_dir(tmp_path, inst.InstanceIdentity("dev")) == []
    assert not (tmp_path / "data-dev" / "setup_state.json").exists()
