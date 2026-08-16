"""The MSIX container escape that decides whether the shell sees a launcher.

Everything here runs on every OS: the redirection arithmetic is a pure function
and the escape itself is exercised through an injected helper, so the behaviour
that broke Windows Search is covered on a Linux CI runner too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.ui import msix_redirection as mr

FAMILY = "PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0"


@pytest.fixture
def roots(tmp_path: Path) -> dict[str, Path]:
    """A profile shaped like the redirected one, on the running OS's paths."""
    local = tmp_path / "Local"
    roaming = tmp_path / "Roaming"
    programs = roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    programs.mkdir(parents=True)
    return {
        "local": local,
        "roaming": roaming,
        "programs": programs,
        "cache": local / "Packages" / FAMILY / "LocalCache",
    }


def _shadow(path: Path, roots: dict[str, Path], *, family: str | None = FAMILY):
    return mr.shadow_path_for(
        path,
        family=family,
        local_appdata=roots["local"],
        roaming_appdata=roots["roaming"],
    )


def test_start_menu_write_is_diverted_into_the_package_container(roots) -> None:
    """The bug: the launcher never reaches the folder the shell reads."""
    lnk = roots["programs"] / "Personal Jarvis.lnk"

    shadow = _shadow(lnk, roots)

    assert shadow is not None
    expected = (
        roots["cache"]
        / "Roaming"
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Personal Jarvis.lnk"
    )
    assert shadow == expected


def test_local_appdata_is_redirected_too(roots) -> None:
    target = roots["local"] / "PersonalJarvis" / "bin" / "PersonalJarvis.exe"

    shadow = _shadow(target, roots)

    assert shadow == roots["cache"] / "Local" / "PersonalJarvis" / "bin" / ("PersonalJarvis.exe")


def test_an_unpackaged_interpreter_redirects_nothing(roots) -> None:
    """The ordinary python.org install must take the plain write path."""
    lnk = roots["programs"] / "Personal Jarvis.lnk"

    assert _shadow(lnk, roots, family=None) is None


def test_the_desktop_is_not_redirected(tmp_path: Path, roots) -> None:
    """Why the Desktop icon kept working while the Start-Menu entry vanished."""
    desktop = tmp_path / "Desktop" / "Personal Jarvis.lnk"

    assert _shadow(desktop, roots) is None


def test_a_path_inside_the_container_is_not_shadowed_again(roots) -> None:
    """The shadow is addressed directly; it must not resolve to a shadow of one."""
    shadow = roots["cache"] / "Roaming" / "Microsoft" / "x.lnk"

    assert _shadow(shadow, roots) is None


def test_publish_moves_the_trapped_file_and_clears_the_shadow(
    roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The escape: file lands where the shell reads, container copy goes away."""
    lnk = roots["programs"] / "Personal Jarvis.lnk"
    shadow = _shadow(lnk, roots)
    assert shadow is not None
    shadow.parent.mkdir(parents=True)
    shadow.write_bytes(b"shortcut")

    monkeypatch.setattr(mr, "container_shadow_path", lambda p: shadow)

    def _fake_helper(script: str, **paths: Path) -> bool:
        # Stands in for the unpackaged process: a real copy into the real tree.
        assert paths["src"] == shadow
        assert paths["dst"] == lnk
        paths["dst"].write_bytes(paths["src"].read_bytes())
        return True

    monkeypatch.setattr(mr, "_run_helper", _fake_helper)

    assert mr.publish_out_of_container(lnk) is True
    assert lnk.read_bytes() == b"shortcut"
    # Without this the next is_file() answers from the container and every
    # staleness check upstream reasons about a file the shell cannot see.
    assert not shadow.exists()


def test_publish_reports_failure_when_the_file_stays_trapped(
    roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A launcher only the container can see must never count as installed."""
    lnk = roots["programs"] / "Personal Jarvis.lnk"
    shadow = _shadow(lnk, roots)
    assert shadow is not None
    shadow.parent.mkdir(parents=True)
    shadow.write_bytes(b"shortcut")

    monkeypatch.setattr(mr, "container_shadow_path", lambda p: shadow)
    monkeypatch.setattr(mr, "_run_helper", lambda *a, **k: False)

    assert mr.publish_out_of_container(lnk) is False
    # The trapped copy is kept: it is the only version that exists.
    assert shadow.is_file()


def test_publish_is_a_noop_without_redirection(roots, monkeypatch) -> None:
    lnk = roots["programs"] / "Personal Jarvis.lnk"
    monkeypatch.setattr(mr, "container_shadow_path", lambda p: None)
    monkeypatch.setattr(mr, "_run_helper", lambda *a, **k: pytest.fail("helper must not run"))

    assert mr.publish_out_of_container(lnk) is True


def test_reveal_drops_a_stale_container_copy(roots, monkeypatch) -> None:
    """So the idempotence checks upstream see what the shell sees."""
    lnk = roots["programs"] / "Personal Jarvis.lnk"
    shadow = _shadow(lnk, roots)
    assert shadow is not None
    shadow.parent.mkdir(parents=True)
    shadow.write_bytes(b"stale")
    monkeypatch.setattr(mr, "container_shadow_path", lambda p: shadow)

    mr.reveal_real_path(lnk)

    assert not shadow.exists()


def test_remove_deletes_the_copy_the_user_actually_clicks(roots, monkeypatch) -> None:
    """An uninstall must not leave a launcher for a deleted install."""
    lnk = roots["programs"] / "Personal Jarvis.lnk"
    lnk.write_bytes(b"real")
    shadow = _shadow(lnk, roots)
    assert shadow is not None
    shadow.parent.mkdir(parents=True)
    shadow.write_bytes(b"container")
    monkeypatch.setattr(mr, "container_shadow_path", lambda p: shadow)

    removed: list[Path] = []

    def _fake_helper(script: str, **paths: Path) -> bool:
        paths["dst"].unlink()
        removed.append(paths["dst"])
        return True

    monkeypatch.setattr(mr, "_run_helper", _fake_helper)

    assert mr.remove_outside_container(lnk) is True
    assert removed == [lnk]
    assert not lnk.exists()
    assert not shadow.exists()


def test_helper_scripts_take_their_paths_from_the_environment() -> None:
    """A path holding a quote or a space must never become script."""
    for script in (mr._PUBLISH_SCRIPT, mr._REMOVE_SCRIPT):
        assert "$env:JARVIS_MSIX_" in script
    # Each script proves its own outcome from outside the container instead of
    # trusting that the command did not throw.
    assert "Test-Path" in mr._PUBLISH_SCRIPT
    assert "Test-Path" in mr._REMOVE_SCRIPT
