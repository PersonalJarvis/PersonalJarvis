"""The Install button must install — on every OS, and all the way to connected.

Regression cover for the ``gam`` failure: a catalog entry that declared no
package manager turned Install into a link to a GitHub wiki, and the entry
probed a binary (``gam``) that was not the CLI the rest of the app drives
(``gws``), so an installed CLI still showed as missing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.clis.catalog import CliCatalog
from jarvis.clis.external_terminal import chain_commands, path_refresh_command
from jarvis.clis.prober import _apply_parse_strategy

SEED_PATH = Path(__file__).resolve().parents[3] / "jarvis/clis/catalog/seed_catalog.json"


def _seed_entries() -> list[dict]:
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))["entries"]


# --------------------------------------------------------------------------
# Every shipped CLI installs
# --------------------------------------------------------------------------


@pytest.mark.parametrize("entry", _seed_entries(), ids=lambda e: e["name"])
def test_every_seed_cli_has_a_runnable_install_method(entry: dict) -> None:
    """No shipped entry may fall back to "manual", which only opens a web page."""
    install = entry["install"]
    declared = [
        field
        for field in (
            "winget_id", "scoop_package", "npm_package",
            "pip_package", "cargo_package", "script_url",
        )
        if install.get(field)
    ]
    assert declared, (
        f"{entry['name']} declares no install method — the Install button would "
        f"open {install.get('manual_url')!r} instead of installing anything."
    )


@pytest.mark.parametrize("entry", _seed_entries(), ids=lambda e: e["name"])
def test_recommended_method_is_actually_declared(entry: dict) -> None:
    install = entry["install"]
    recommended = install.get("recommended")
    if not recommended:
        return
    field = {
        "winget": "winget_id", "scoop": "scoop_package", "npm": "npm_package",
        "pip": "pip_package", "cargo": "cargo_package", "script": "script_url",
    }[recommended]
    assert install.get(field), (
        f"{entry['name']}: recommended={recommended!r} has no {field} — the "
        "install dialog would preselect a dead option."
    )


def test_google_workspace_entry_is_the_cli_the_app_actually_drives(tmp_path: Path) -> None:
    """The catalog probes ``gws``, the CLI the skills and workflows call.

    It used to describe GAM, a different tool with a different binary: an
    installed Google Workspace CLI therefore reported "not installed" forever.
    """
    # Seed only — a custom catalog on the developer's box must not decide this.
    specs = CliCatalog(custom_path=tmp_path / "custom.json").all()
    assert "gam" not in specs, "the uninstallable GAM entry must not come back"
    gws = specs["gws"]
    assert gws.binary_name == "gws"
    assert gws.install.npm_package == "@googleworkspace/cli"
    assert gws.install.has_automatic_method()
    assert gws.auth.login_command == ("gws", "auth", "login")
    assert gws.auth.status_parse == "json_token_valid"


# --------------------------------------------------------------------------
# Stored credentials are not the same thing as a working login
# --------------------------------------------------------------------------


def test_dead_token_over_stored_credentials_reads_as_expired() -> None:
    """The exact `gws auth status` shape that used to look connected."""
    stdout = json.dumps({
        "has_refresh_token": True,
        "encrypted_credentials_exists": True,
        "token_error": "Bad Request",
        "token_valid": False,
    })
    assert _apply_parse_strategy("json_token_valid", stdout, "", 0) == "expired"


def test_valid_token_reads_as_connected() -> None:
    stdout = json.dumps({"has_refresh_token": True, "token_valid": True})
    assert _apply_parse_strategy("json_token_valid", stdout, "", 0) == "connected"


def test_no_credentials_at_all_reads_as_not_connected() -> None:
    stdout = json.dumps({"token_valid": False, "has_refresh_token": False})
    assert _apply_parse_strategy("json_token_valid", stdout, "", 0) == "not_connected"


def test_unparseable_status_is_unknown_not_connected() -> None:
    assert _apply_parse_strategy("json_token_valid", "not json", "", 0) == "unknown"


# --------------------------------------------------------------------------
# Install chains into login without stranding the shell on a stale PATH
# --------------------------------------------------------------------------


def test_chain_stops_at_the_first_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed install must not be followed by a login attempt."""
    monkeypatch.setattr("jarvis.clis.external_terminal.sys.platform", "linux")
    assert chain_commands(["a", "b", "c"]) == "a && b && c"

    # Windows PowerShell 5.1 is a possible fallback shell and has no `&&`,
    # so the Windows spelling must not contain one.
    monkeypatch.setattr("jarvis.clis.external_terminal.sys.platform", "win32")
    chained = chain_commands(["a", "b", "c"])
    assert "&&" not in chained
    assert chained == "a; if ($?) { b; if ($?) { c } }"


def test_chain_of_one_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    for platform in ("win32", "linux", "darwin"):
        monkeypatch.setattr("jarvis.clis.external_terminal.sys.platform", platform)
        assert chain_commands(["npm install -g x"]) == "npm install -g x"
        assert chain_commands([]) == ""


def test_path_refresh_exists_for_every_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without it, `gcloud auth login` right after installing gcloud is not found."""
    for platform in ("win32", "linux", "darwin"):
        monkeypatch.setattr("jarvis.clis.external_terminal.sys.platform", platform)
        assert path_refresh_command().strip()
