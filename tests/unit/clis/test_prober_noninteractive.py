"""Background probes must not initiate browser authentication."""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from jarvis.clis.catalog import CliCatalog
from jarvis.clis.prober import CliStatusProber


@pytest.mark.asyncio
@pytest.mark.parametrize("parent_ci", [None, "false"])
@pytest.mark.parametrize("credentials", ["missing", "expired", "valid"])
async def test_repeated_probes_do_not_launch_login(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    parent_ci: str | None,
    credentials: str,
) -> None:
    """Exercise real child environments with a CLI simulating Neon's auth fallback."""
    if parent_ci is None:
        monkeypatch.delenv("CI", raising=False)
    else:
        monkeypatch.setenv("CI", parent_ci)
    monkeypatch.setenv("PROBE_TEST_CREDENTIALS", credentials)
    browser_marker = tmp_path / "browser-opened"
    cli = tmp_path / "cli.py"
    cli.write_text(
        "import os, sys\n"
        "from pathlib import Path\n"
        "if os.environ.get('CI') != 'true':\n"
        "    Path(sys.argv[1]).touch()\n"
        "    sys.exit(1)\n"
        "if sys.stdin.read() != '':\n"
        "    sys.exit(2)\n"
        "if sys.argv[2] == '--version':\n"
        "    print('1.2.3')\n"
        "elif os.environ['PROBE_TEST_CREDENTIALS'] == 'valid':\n"
        "    print('{\"id\": \"test-user\"}')\n"
        "else:\n"
        "    print('Cannot run interactive auth in CI', file=sys.stderr)\n"
        "    sys.exit(1)\n",
        encoding="utf-8",
    )
    neon = CliCatalog(custom_path=tmp_path / "custom.json").get("neonctl")
    assert neon is not None
    command = (sys.executable, str(cli), str(browser_marker))
    spec = replace(
        neon,
        binary_name=sys.executable,
        check_command=(*command, "--version"),
        auth=replace(neon.auth, status_command=(*command, "me")),
    )
    for _ in range(2):
        status = await CliStatusProber().probe(spec)
        assert status.installed
        assert status.version == "1.2.3"
        assert status.auth_status == (
            "connected" if credentials == "valid" else "not_connected"
        )
        assert not browser_marker.exists()
    # An explicit login in the parent must retain its interactive environment.
    assert os.environ.get("CI") == parent_ci
