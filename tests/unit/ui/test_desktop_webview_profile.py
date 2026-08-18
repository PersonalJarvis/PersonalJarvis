"""The desktop shell keeps the embedded browser's profile across restarts.

pywebview defaults to ``private_mode=True`` — a fresh WebView2 profile in
``%TEMP%`` per launch — and everything the frontend stores in ``localStorage``
(the wallpaper pick, the deck/classic surface, pane sizes, favourites, the
theme cache the boot script paints the first frame from) died with the
process. Forensic 2026-08-18: after every restart the interface wore light
chrome over the dark bundled artwork, because the light pick was gone while
the theme survived on the backend.

These tests pin the directory the shell hands pywebview: per checkout, with
the same fallbacks the credential store uses, and an honest ``None`` (→ private
mode, logged) only when nothing can be written.
"""

from __future__ import annotations

from pathlib import Path

from jarvis.ui.desktop_app import WEBVIEW_PROFILE_DIRNAME, webview_storage_dir


def test_profile_lives_under_the_data_dir(tmp_path: Path) -> None:
    got = webview_storage_dir(data_dir=tmp_path, fallback_dir=tmp_path / "unused")

    assert got == tmp_path / WEBVIEW_PROFILE_DIRNAME
    assert got.is_dir()
    # The fallback is not touched while the primary works.
    assert not (tmp_path / "unused").exists()


def test_profile_is_created_when_missing(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    assert not data_dir.exists()

    got = webview_storage_dir(data_dir=data_dir, fallback_dir=tmp_path / "user")

    assert got == data_dir / WEBVIEW_PROFILE_DIRNAME
    assert got.is_dir()


def test_profile_falls_back_to_the_user_dir_when_the_checkout_is_read_only(
    tmp_path: Path,
) -> None:
    # A FILE where the data dir should be: mkdir(parents=True) fails with an
    # OSError, which is what a read-only site-packages checkout looks like.
    blocked = tmp_path / "blocked"
    blocked.write_text("", encoding="utf-8")
    user_dir = tmp_path / "user"

    got = webview_storage_dir(data_dir=blocked, fallback_dir=user_dir)

    assert got == user_dir / WEBVIEW_PROFILE_DIRNAME
    assert got.is_dir()


def test_no_writable_dir_means_none_not_a_crash(tmp_path: Path) -> None:
    blocked_a = tmp_path / "a"
    blocked_b = tmp_path / "b"
    blocked_a.write_text("", encoding="utf-8")
    blocked_b.write_text("", encoding="utf-8")

    assert webview_storage_dir(data_dir=blocked_a, fallback_dir=blocked_b) is None


def test_default_lives_next_to_the_other_per_checkout_state() -> None:
    from jarvis.core.config import DATA_DIR
    from jarvis.core.paths import user_data_dir

    got = webview_storage_dir()

    # Either the checkout's own data dir or the per-user fallback — never a
    # temp folder, which is exactly what private mode used.
    assert got is not None
    assert got.name == WEBVIEW_PROFILE_DIRNAME
    assert got.parent in {DATA_DIR, user_data_dir()}
