"""Official install scripts must pick the file that this OS can actually run."""

from __future__ import annotations

import sys

from jarvis.clis.installer import CliInstaller
from jarvis.clis.spec import InstallMethods
from jarvis.workspace.agents import get_agent


def test_antigravity_install_uses_the_host_official_script() -> None:
    """A POSIX host must not be handed a PowerShell file, and vice versa."""
    entry = get_agent("antigravity")
    assert entry is not None and entry.spec is not None
    methods = entry.spec.install
    assert methods.script_url == "https://antigravity.google/cli/install.sh"
    assert methods.windows_script_url == ("https://antigravity.google/cli/install.ps1")
    argv = CliInstaller().build_command(entry.spec, "script")
    assert argv is not None
    joined = " ".join(argv)
    if sys.platform == "win32":
        assert "install.ps1" in joined
        assert "install.sh" not in joined
    else:
        assert "install.sh" in joined
        assert "install.ps1" not in joined


def test_a_windows_only_script_is_not_offered_on_posix() -> None:
    methods = InstallMethods(
        windows_script_url="https://example.invalid/install.ps1",
        recommended="script",
    )
    if sys.platform == "win32":
        assert methods.available_methods() == ("script",)
        assert methods.script_url_for_host() == "https://example.invalid/install.ps1"
    else:
        assert "script" not in methods.available_methods()
        assert methods.script_url_for_host() is None
