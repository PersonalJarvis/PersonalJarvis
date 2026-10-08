import sysconfig

from jarvis.setup import onboarding_meta as m


def test_meta_constants():
    assert m.CURRENT_TERMS_VERSION == "1.1"
    # Setup is one window with three steps (2026-10-05) — the name (the wake
    # word), connecting an AI, how the user talks to it — then the walk
    # through the app, which ends in the single completion restart.
    assert m.ONBOARDING_STEPS == ["name", "connect", "voice", "tour"]
    assert m.ONBOARDING_STEPS[-1] == "tour"
    # Retired step ids must not come back through a partial revert.
    for retired in (
        "terms",
        "language",
        "api-keys",
        "wake-word",
        "finish",
        "brain",
        "agents",
        "welcome",
        "how",
        "keys",
        "subscriptions",
        "permissions",
        "ready",
    ):
        assert retired not in m.ONBOARDING_STEPS
    assert len(m.WAKE_WORD_LEGAL_REFERENCES) >= 3
    for ref in m.WAKE_WORD_LEGAL_REFERENCES:
        assert ref["label"] and ref["url"].startswith("https://")


def test_read_terms_text_returns_versioned_body():
    text = m.read_terms_text()
    assert "Personal Jarvis" in text
    assert f"v{m.CURRENT_TERMS_VERSION}" in text
    # The 'no affiliation' clause must be present (legal core).
    assert "affiliat" in text.lower()


def _assert_authentication_disclosure(text: str) -> None:
    assert "token.personaljarvis.ai" in text
    assert "Cloudflare" in text
    assert "Google and Slack" in text
    assert "authorization codes" in text
    assert "PKCE verifiers" in text
    assert "refresh tokens" in text
    assert "provider token responses" in text
    assert "logging, storage, or retention" in text
    assert "authors operate no server" not in text
    assert "receive none of your data" not in text


def test_canonical_terms_disclose_project_authentication_service():
    _assert_authentication_disclosure(m.read_terms_text())


def test_read_terms_text_uses_installed_document_when_source_is_absent(
    tmp_path,
    monkeypatch,
):
    canonical = m.read_terms_text()
    installed = tmp_path / "share" / "personal-jarvis" / "docs" / "legal" / "TERMS.md"
    installed.parent.mkdir(parents=True)
    installed.write_text(canonical, encoding="utf-8")
    monkeypatch.setattr(m, "_TERMS_PATH", tmp_path / "absent-source" / "TERMS.md")
    monkeypatch.setattr(sysconfig, "get_path", lambda name, **kwargs: str(tmp_path))

    assert m.read_terms_text() == canonical


def test_fallback_terms_keep_authentication_disclosure(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_TERMS_PATH", tmp_path / "absent-source" / "TERMS.md")
    monkeypatch.setattr(sysconfig, "get_path", lambda name, **kwargs: str(tmp_path))

    text = m.read_terms_text()
    assert f"v{m.CURRENT_TERMS_VERSION}" in text
    assert "without warranty" in text
    assert "not liable" in text
    assert "English is authoritative" in text
    _assert_authentication_disclosure(text)


def test_read_terms_text_finds_user_install_data(tmp_path, monkeypatch):
    user_data = tmp_path / "user"
    installed = user_data / "share" / "personal-jarvis" / "docs" / "legal" / "TERMS.md"
    installed.parent.mkdir(parents=True)
    installed.write_text("User-installed canonical terms", encoding="utf-8")
    monkeypatch.setattr(m, "_TERMS_PATH", tmp_path / "missing" / "TERMS.md")
    monkeypatch.setattr(
        sysconfig,
        "get_path",
        lambda name, **kwargs: str(user_data if kwargs.get("scheme") else tmp_path / "system"),
    )

    assert m.read_terms_text() == "User-installed canonical terms"
