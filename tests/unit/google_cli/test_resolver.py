"""Resolver order + fallback for the official Google agent CLI."""
from __future__ import annotations

import os

from jarvis.google_cli.resolver import GoogleCli, resolve_google_cli


def test_prefers_agy_when_on_path():
    def which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in ("agy", "agy.exe") else None

    cli = resolve_google_cli(which=which, npm_bundle=lambda: None)
    assert isinstance(cli, GoogleCli)
    assert cli.kind == "agy"
    assert cli.argv_prefix == ["/usr/bin/agy"]


def test_falls_back_to_gemini_on_path():
    def which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in ("gemini", "gemini.cmd") else None

    cli = resolve_google_cli(which=which, npm_bundle=lambda: None, agy_path=lambda: None)
    assert cli is not None
    assert cli.kind == "gemini"
    assert cli.argv_prefix == ["/usr/bin/gemini"]


def test_falls_back_to_npm_bundle(tmp_path):
    bundle = tmp_path / "gemini.js"
    bundle.write_text("// stub")

    def which(name: str) -> str | None:
        return "/usr/bin/node" if name == "node" else None

    cli = resolve_google_cli(
        which=which, npm_bundle=lambda: str(bundle), agy_path=lambda: None
    )
    assert cli is not None
    assert cli.kind == "gemini"
    assert cli.argv_prefix == ["/usr/bin/node", str(bundle)]


def test_none_when_nothing_available():
    assert (
        resolve_google_cli(
            which=lambda name: None, npm_bundle=lambda: None, agy_path=lambda: None
        )
        is None
    )


def test_default_npm_bundle_finds_known_root(tmp_path):
    """The default bundle finder probes known npm-global roots WITHOUT calling
    npm (on Windows ``npm`` is a .cmd that subprocess can't run directly)."""
    from jarvis.google_cli.resolver import _default_npm_bundle

    root = tmp_path / "node_modules"
    bundle = root / "@google" / "gemini-cli" / "bundle" / "gemini.js"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("// stub")
    found = _default_npm_bundle(roots=[str(root)], which=lambda n: None)
    assert found == str(bundle)


def test_default_npm_bundle_none_when_absent(tmp_path):
    from jarvis.google_cli.resolver import _default_npm_bundle

    assert _default_npm_bundle(roots=[str(tmp_path)], which=lambda n: None) is None


def test_prefers_agy_via_native_location_when_not_on_path(tmp_path):
    """A native install can be absent from the live GUI PATH after installation.

    The resolver must probe the known location and still prefer Antigravity over
    a Gemini CLI that happens to be on PATH.
    """
    agy = tmp_path / "agy.exe"
    agy.write_text("// stub")

    def which(name: str) -> str | None:
        # agy not on PATH; the gemini CLI IS on PATH (must still lose to agy).
        return "/usr/bin/gemini" if name == "gemini" else None

    cli = resolve_google_cli(
        which=which, npm_bundle=lambda: None, agy_path=lambda: str(agy)
    )
    assert cli is not None
    assert cli.kind == "agy"
    assert cli.argv_prefix == [str(agy)]


def test_default_agy_path_finds_native_binary(tmp_path):
    from jarvis.google_cli.resolver import _default_agy_path

    links = tmp_path / "WinGet" / "Links"
    links.mkdir(parents=True)
    (links / "agy.exe").write_text("// stub")
    found = _default_agy_path(roots=[str(links)])
    assert found == str(links / "agy.exe")


def test_agy_roots_include_official_windows_location(tmp_path, monkeypatch):
    from jarvis.google_cli.resolver import _agy_install_roots

    local = tmp_path / "LocalAppData"
    monkeypatch.setenv("LOCALAPPDATA", str(local))

    assert os.path.join(str(local), "agy", "bin") in _agy_install_roots()
