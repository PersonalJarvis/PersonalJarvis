"""Fresh control CLI processes read the headless file fallback without warnings."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from jarvis.core import config
from jarvis.core.control_key import KEYRING_SLOT
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

_CHILD = """
import sys
import httpx
import keyring
from keyring.backends import fail
from jarvis.cli_ctl.client import JarvisClient

keyring.set_keyring(fail.Keyring())
original_init = JarvisClient.__init__

def respond(request):
    assert request.headers['Authorization'] == 'Bearer jctl_headless_fixture'
    return httpx.Response(200, json={'ok': True})

def initialize(self, *args, **kwargs):
    kwargs['transport'] = httpx.MockTransport(respond)
    original_init(self, *args, **kwargs)

JarvisClient.__init__ = initialize
entrypoint, *arguments = sys.argv[1:]
sys.argv = [entrypoint, '--json', *arguments]
if entrypoint == 'jarvis':
    from jarvis.__main__ import main
    raise SystemExit(main())
else:
    from jarvis.cli_ctl.__main__ import main
    main()
"""


@pytest.mark.parametrize("entrypoint", ["jarvis", "jarvisctl"])
@pytest.mark.parametrize("command", [
    ["auth", "status"], ["wiki", "health"], ["config", "list"], ["permissions", "status"],
])
def test_headless_control_commands_are_quiet_across_processes(tmp_path, entrypoint, command):
    data = tmp_path / "data"
    data.mkdir()
    store_path = data / "credentials.json"
    config._FileCredStore(path=store_path).set(
        config.KEYRING_SERVICE, KEYRING_SLOT, "jctl_headless_fixture"
    )
    original = store_path.read_bytes()
    env = {key: value for key, value in os.environ.items() if not key.startswith("JARVIS")}
    env.update({
        "JARVIS_DATA_DIR": str(data),
        "JARVISCTL_CONFIG_HOME": str(tmp_path / "cli-config"),
        "JARVISCTL_CACHE_HOME": str(tmp_path / "cli-cache"),
        "JARVIS_CLI_SESSION_FILE": str(tmp_path / "no-session.json"),
        "PYTHONIOENCODING": "utf-8",
    })
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-c", _CHILD, entrypoint, *command],
            cwd=Path(__file__).resolve().parents[3], env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, creationflags=NO_WINDOW_CREATIONFLAGS,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload.get("reachable", payload.get("ok")) is True
        assert "OS credential store unusable" not in result.stderr
        assert "jctl_headless_fixture" not in result.stdout + result.stderr
    assert store_path.read_bytes() == original
    if os.name != "nt":
        assert store_path.stat().st_mode & 0o777 == 0o600
