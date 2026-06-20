"""Resolver order + fallback for the official Google agent CLI."""
from __future__ import annotations

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

    cli = resolve_google_cli(which=which, npm_bundle=lambda: None)
    assert cli is not None
    assert cli.kind == "gemini"
    assert cli.argv_prefix == ["/usr/bin/gemini"]


def test_falls_back_to_npm_bundle(tmp_path):
    bundle = tmp_path / "gemini.js"
    bundle.write_text("// stub")

    def which(name: str) -> str | None:
        return "/usr/bin/node" if name == "node" else None

    cli = resolve_google_cli(which=which, npm_bundle=lambda: str(bundle))
    assert cli is not None
    assert cli.kind == "gemini"
    assert cli.argv_prefix == ["/usr/bin/node", str(bundle)]


def test_none_when_nothing_available():
    assert resolve_google_cli(which=lambda name: None, npm_bundle=lambda: None) is None
