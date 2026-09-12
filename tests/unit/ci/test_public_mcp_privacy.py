"""Public catalog filenames never exempt credentials from either privacy gate."""

from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))
import privacy_pre_push as gate  # noqa: E402
import privacy_scan_ci as ci  # noqa: E402

PATH = "jarvis/marketplace/plugins/github/mcp.json"
HTTP = {"type": "streamable-http", "url": "https://api.githubcopilot.com/mcp/"}


def manifest(server=None, name="github"):
    return json.dumps(
        {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
            "mcpServers": {name: HTTP if server is None else server},
        }
    )


def test_existing_catalog_manifests_are_public():
    paths = list((ROOT / "jarvis/marketplace/plugins").glob("*/mcp.json"))
    assert paths
    for path in paths:
        assert gate.is_public_catalog_mcp(path.relative_to(ROOT).as_posix(), path.read_text()), path


@pytest.mark.parametrize(
    "path",
    [
        "mcp.json",
        "private/mcp.json",
        "jarvis/marketplace/plugins/example/../mcp.json",
        "./jarvis/marketplace/plugins/example/mcp.json",
        PATH.replace("/", "\\"),
        "/" + PATH,
        "jarvis/marketplace/plugins/example/nested/mcp.json",
    ],
)
def test_personal_and_ambiguous_paths_stay_forbidden(path):
    assert not gate.is_public_catalog_mcp(path, manifest())


@pytest.mark.parametrize("extra", ["env", "headers", "token", "authorization", "extra"])
def test_extra_server_fields_are_forbidden(extra):
    assert gate.forbidden_pushed_file(PATH, manifest({**HTTP, extra: "private"}), {"mcp.json"})


@pytest.mark.parametrize(
    "url",
    [
        "http://example.invalid/mcp",
        "https://user:pass@example.invalid/mcp",
        "https://example.invalid/mcp?token=private",
        "https://example.invalid/mcp#private",
        "https://example.invalid/mcp?",
        "https://example.invalid/mcp#",
        "https://example.invalid/\nprivate",
        "https://example.invalid:bad/mcp",
        "https:///mcp",
        "https://example.invalid\\private",
    ],
)
def test_unsafe_urls_are_forbidden(url):
    assert not gate.is_public_catalog_mcp(PATH, manifest({**HTTP, "url": url}))


@pytest.mark.parametrize(
    "url",
    [
        "https://unknown.invalid/mcp",
        "https://api.githubcopilot.com/mcp/opaque-capability-token",
        "https://api.githubcopilot.com/another-endpoint",
        "https://mcp.agentmail.to/mcp",
    ],
)
def test_unreviewed_endpoint_or_capability_path_is_forbidden(url):
    assert gate.forbidden_pushed_file(PATH, manifest({**HTTP, "url": url}), {"mcp.json"})


def test_reviewed_endpoint_cannot_be_reused_by_unknown_plugin():
    assert not gate.is_public_catalog_mcp(
        PATH.replace("github", "unknown"), manifest(name="unknown")
    )


@pytest.mark.parametrize(
    "text",
    [
        "null",
        "[]",
        "{",
        manifest(name="other"),
        manifest().replace('"mcpServers":', '"token": "private", "mcpServers":'),
        manifest().replace('"mcpServers":', '"mcpServers": {}, "mcpServers":'),
        manifest().replace("1.0.0/mcp.schema.json", "other.json"),
    ],
)
def test_invalid_structure_is_forbidden(text):
    assert not gate.is_public_catalog_mcp(PATH, text)


@pytest.mark.parametrize(
    "args",
    [
        ["-c", "print('private')"],
        ["-m", "other"],
        ["-m", "jarvis.plugins.tool.connected_server", "other"],
        ["-m", "jarvis.marketplace.amd_mcp"],
        ["-m", "jarvis.plugins.tool.connected_server", "example", "private"],
    ],
)
def test_arbitrary_stdio_commands_are_forbidden(args):
    assert not gate.is_public_catalog_mcp(
        PATH, manifest({"type": "stdio", "command": "python", "args": args})
    )


def test_normalized_stdio_identity_is_allowed():
    server = {
        "type": "stdio",
        "command": "python",
        "args": ["-m", "jarvis.plugins.tool.connected_server", "some_plugin"],
    }
    assert gate.is_public_catalog_mcp(
        PATH.replace("github", "some-plugin"), manifest(server, "some_plugin")
    )


@pytest.mark.parametrize("runner", ["local", "ci"])
@pytest.mark.parametrize("secret", [False, True])
def test_both_gates_scan_public_manifest_content(monkeypatch, runner, secret):
    text = manifest()
    assert gate.is_public_catalog_mcp(PATH, text)
    # A synthetic detector matches safe public text to prove that filename
    # exemption never skips content scanning in either caller.
    patterns = {"test_detector": re.compile("githubcopilot")} if secret else {}
    monkeypatch.setattr(
        gate,
        "load_secret_scanner",
        lambda root: (patterns, {"mcp.json"}, set()),
    )
    monkeypatch.setattr(gate, "pushed_text_files", lambda *args: [(PATH, text)])
    monkeypatch.setattr(gate, "load_private_emails", lambda: set())
    monkeypatch.setattr(gate, "_git", lambda *args: "")
    monkeypatch.delenv("PRIVACY_PRIVATE_EMAILS", raising=False)
    if runner == "ci":
        result = ci.main(["gate", "--base", "HEAD~1"])
    else:
        result = gate.main(
            ["gate", "origin", "url"],
            io.StringIO(f"refs/heads/main {'1' * 40} refs/heads/main {'2' * 40}\n"),
        )
    assert result == int(secret)
