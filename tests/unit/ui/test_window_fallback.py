"""Desktop-window fallback guidance matches the install type."""

from jarvis.ui.window_fallback import default_window_fallback_remedy


def test_frozen_linux_fallback_does_not_recommend_unusable_system_packages() -> None:
    remedy = default_window_fallback_remedy(platform="linux", frozen=True)

    assert "packaged Linux build has no native window backend" in remedy
    assert "host GTK packages will not add one" in remedy
    assert "browser UI" in remedy


def test_source_linux_fallback_keeps_the_system_package_recovery_path() -> None:
    remedy = default_window_fallback_remedy(platform="linux", frozen=False)

    assert "system GTK 3 + WebKit2GTK packages pywebview needs" in remedy
    assert "source or pipx install" not in remedy


def test_non_linux_frozen_fallback_does_not_claim_a_linux_bundle_gap() -> None:
    remedy = default_window_fallback_remedy(platform="darwin", frozen=True)

    assert "packaged Linux build" not in remedy
    assert "GTK 3 + WebKit2GTK" in remedy
