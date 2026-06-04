"""Tests for Unix shell discovery (Wave 1.2, AD-9).

These run on the Windows dev box by monkeypatching ``detect_platform`` (as
imported into ``jarvis.terminal.shells``), ``$SHELL``, ``os.path.isfile``,
``os.path.realpath``, ``shutil.which``, and a fake ``/etc/shells`` reader. No
real POSIX host required — the logic (preference order + dedup + ``argv``
shape) is fully assertable cross-platform.
"""

from __future__ import annotations

import io

import jarvis.terminal.shells as shells_mod
from jarvis.terminal.shells import ShellInfo, discover_shells, get_shell


def _patch_unix(monkeypatch) -> None:
    monkeypatch.setattr(shells_mod, "detect_platform", lambda: "linux")


def _patch_filesystem(
    monkeypatch,
    *,
    on_disk: set[str],
    etc_shells: list[str] | None,
    which: dict[str, str | None] | None = None,
    shell_env: str | None = None,
    symlinks: dict[str, str] | None = None,
) -> None:
    """Install a fake POSIX filesystem view for shell discovery."""
    if shell_env is None:
        monkeypatch.delenv("SHELL", raising=False)
    else:
        monkeypatch.setenv("SHELL", shell_env)

    monkeypatch.setattr(
        shells_mod.os.path, "isfile", lambda p: p in on_disk
    )

    sym = symlinks or {}
    monkeypatch.setattr(
        shells_mod.os.path, "realpath", lambda p: sym.get(p, p)
    )

    which = which or {}
    monkeypatch.setattr(shells_mod.shutil, "which", lambda name: which.get(name))

    if etc_shells is None:
        def _no_etc_shells(*_a, **_k):
            raise OSError("no /etc/shells")

        monkeypatch.setattr("builtins.open", _no_etc_shells)
    else:
        content = "\n".join(etc_shells) + "\n"

        def _fake_open(path, *args, **kwargs):
            if path == "/etc/shells":
                return io.StringIO(content)
            raise OSError(f"unexpected open: {path}")

        monkeypatch.setattr("builtins.open", _fake_open)


def test_shell_env_first_in_order(monkeypatch):
    _patch_unix(monkeypatch)
    _patch_filesystem(
        monkeypatch,
        on_disk={"/usr/bin/zsh", "/bin/bash"},
        etc_shells=["/bin/bash"],
        shell_env="/usr/bin/zsh",
    )
    shells = discover_shells()
    ids = [s.id for s in shells]
    # $SHELL (zsh) must come before /etc/shells entries (bash).
    assert ids[0] == "zsh"
    assert "bash" in ids


def test_argv_is_interactive_not_login(monkeypatch):
    _patch_unix(monkeypatch)
    _patch_filesystem(
        monkeypatch,
        on_disk={"/usr/bin/zsh"},
        etc_shells=[],
        shell_env="/usr/bin/zsh",
    )
    shells = discover_shells()
    assert shells[0].argv == ("/usr/bin/zsh", "-i")
    # Never an unconditional -l (slow login shell on every spawn).
    assert "-l" not in shells[0].argv


def test_dedup_by_resolved_path(monkeypatch):
    _patch_unix(monkeypatch)
    # /bin/sh is a symlink to /bin/bash; $SHELL also points at /bin/bash.
    _patch_filesystem(
        monkeypatch,
        on_disk={"/bin/bash", "/bin/sh"},
        etc_shells=["/bin/bash", "/bin/sh"],
        shell_env="/bin/bash",
        symlinks={"/bin/sh": "/bin/bash", "/bin/bash": "/bin/bash"},
    )
    shells = discover_shells()
    # Only one entry — all three references resolve to /bin/bash.
    assert len(shells) == 1
    assert shells[0].argv == ("/bin/bash", "-i")


def test_etc_shells_parsing_skips_comments_and_blanks(monkeypatch):
    _patch_unix(monkeypatch)
    _patch_filesystem(
        monkeypatch,
        on_disk={"/bin/bash", "/usr/bin/fish"},
        etc_shells=["# a comment", "", "  ", "/bin/bash", "/usr/bin/fish"],
        shell_env=None,
    )
    ids = [s.id for s in discover_shells()]
    assert ids == ["bash", "fish"]


def test_which_fallback_when_no_shell_and_no_etc_shells(monkeypatch):
    _patch_unix(monkeypatch)
    _patch_filesystem(
        monkeypatch,
        on_disk={"/usr/local/bin/bash"},
        etc_shells=None,  # OSError -> skip
        shell_env=None,
        which={"bash": "/usr/local/bin/bash", "zsh": None, "fish": None},
    )
    shells = discover_shells()
    assert [s.id for s in shells] == ["bash"]
    assert shells[0].argv == ("/usr/local/bin/bash", "-i")


def test_shell_env_not_on_disk_is_skipped(monkeypatch):
    _patch_unix(monkeypatch)
    _patch_filesystem(
        monkeypatch,
        on_disk={"/bin/bash"},  # $SHELL points somewhere that does not exist
        etc_shells=["/bin/bash"],
        shell_env="/opt/exotic/shell",
    )
    ids = [s.id for s in discover_shells()]
    assert ids == ["bash"]  # the missing $SHELL was skipped


def test_empty_when_nothing_found(monkeypatch):
    _patch_unix(monkeypatch)
    _patch_filesystem(
        monkeypatch,
        on_disk=set(),
        etc_shells=None,
        shell_env=None,
        which={"bash": None, "zsh": None, "fish": None},
    )
    assert discover_shells() == []


def test_get_shell_finds_by_id(monkeypatch):
    _patch_unix(monkeypatch)
    _patch_filesystem(
        monkeypatch,
        on_disk={"/bin/bash", "/usr/bin/zsh"},
        etc_shells=["/bin/bash", "/usr/bin/zsh"],
        shell_env=None,
    )
    found = get_shell("zsh")
    assert isinstance(found, ShellInfo)
    assert found.argv == ("/usr/bin/zsh", "-i")
    assert get_shell("nonesuch") is None


def test_windows_branch_uses_windows_factories_unchanged(monkeypatch):
    """AD-7: on win32 discover_shells iterates the 4 Windows factories."""
    monkeypatch.setattr(shells_mod, "detect_platform", lambda: "win32")

    sentinel = ShellInfo(id="pwsh", label="PowerShell 7", argv=("pwsh", "-NoLogo"))
    monkeypatch.setattr(shells_mod, "_powershell_7", lambda: sentinel)
    monkeypatch.setattr(shells_mod, "_windows_powershell", lambda: None)
    monkeypatch.setattr(shells_mod, "_cmd", lambda: None)
    monkeypatch.setattr(shells_mod, "_git_bash", lambda: None)

    shells = discover_shells()
    assert shells == [sentinel]


def test_macos_uses_unix_branch(monkeypatch):
    monkeypatch.setattr(shells_mod, "detect_platform", lambda: "darwin")
    _patch_filesystem(
        monkeypatch,
        on_disk={"/bin/zsh"},
        etc_shells=["/bin/zsh"],
        shell_env="/bin/zsh",
    )
    ids = [s.id for s in discover_shells()]
    assert ids == ["zsh"]
