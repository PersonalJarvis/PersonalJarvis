"""The gate that keeps credentials out of the knowledge base.

Two properties matter more than any individual pattern, and both are pinned
here: a credential FILE never reaches storage with its content, and an item is
never DROPPED — because a memory that silently loses files is a memory nobody
can reason about, and because the sync checkpoint is the last item's id.

The false-positive tests are not politeness. A redactor that eats ordinary
prose makes the knowledge base worse in a way nobody notices until a search
comes back empty, so the ordinary sentences below must survive untouched.
"""

from __future__ import annotations

from jarvis.ultrawiki.secret_scrub import (
    looks_like_credential_file,
    redact_secrets,
    scrub_item,
)
from jarvis.ultrawiki.types import RawItem


def _item(body: str, *, title: str = "note.md", **metadata: object) -> RawItem:
    return RawItem(
        external_id="x1",
        body=body,
        permalink="https://example.test/x1",
        timestamp_utc="2026-03-01T10:00:00Z",
        title=title,
        metadata=dict(metadata),
    )


# ---------------------------------------------------------------------------
# Credential files: withheld whole, never dropped
# ---------------------------------------------------------------------------


def test_the_files_whose_purpose_is_credentials_are_recognised():
    for name in (
        ".env",
        ".env.production",
        "id_rsa",
        "server.pem",
        "signing.key",
        "credentials.json",
        "service-account-prod.json",
        "secrets.yaml",
        ".npmrc",
        "vault.kdbx",
        "deploy/.git-credentials",
        "C:\\Users\\Someone\\.aws\\credentials",
    ):
        assert looks_like_credential_file(name), name


def test_ordinary_files_are_not_mistaken_for_credential_stores():
    for name in (
        "README.md",
        "keyboard-shortcuts.md",
        "environment.md",
        "notes/secret-santa.md",
        "Invoice 2026-03.pdf",
        "monkey.png",
    ):
        assert not looks_like_credential_file(name), name


def test_a_credential_file_keeps_its_name_and_loses_its_content():
    """Withheld, not dropped: "there is a .env in this project" is true and
    useful, and removing the item would move the sync's resume point."""
    result = scrub_item(
        _item("DATABASE_URL=postgres://user:hunter2@db/prod", title=".env")
    )
    assert result.withheld is True
    assert "hunter2" not in result.item.body
    assert "postgres" not in result.item.body
    assert ".env" in result.item.body
    assert result.item.external_id == "x1"
    assert result.item.metadata["secret_withheld"] is True
    assert result.item.metadata["content_missing"] is True


def test_the_real_filename_is_read_from_metadata_not_only_the_title():
    """An attachment's title is a display name; the filename lives in
    metadata, and that is what the rule has to see."""
    result = scrub_item(
        _item(
            "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----",
            title="Key for the staging box",
            filename="staging.pem",
        )
    )
    assert result.withheld is True
    assert "MIIabc" not in result.item.body


# ---------------------------------------------------------------------------
# Redaction inside ordinary content
# ---------------------------------------------------------------------------


def test_published_credential_formats_are_replaced_by_a_named_marker():
    cases = {
        "AKIAIOSFODNN7EXAMPLE": "AWS access key id",
        "ghp_" + "a" * 36: "GitHub token",
        "xoxb-1234567890-abcdefghijkl": "Slack token",
        "AIza" + "b" * 35: "Google API key",
        "sk_live_" + "c" * 24: "Stripe key",
        "sk-ant-" + "d" * 40: "API key",
    }
    for secret, label in cases.items():
        cleaned, count = redact_secrets(f"the token is {secret} — keep it safe")
        assert count == 1, secret
        assert secret not in cleaned
        assert f"[redacted: {label}]" in cleaned
        # The sentence around it survives, so the record still reads.
        assert "keep it safe" in cleaned


def test_a_private_key_block_is_removed_entirely():
    body = (
        "Here is the deploy key:\n"
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXktdjEAAAAA\nmore lines\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
        "Use it on the staging box."
    )
    cleaned, count = redact_secrets(body)
    assert count == 1
    assert "b3BlbnNzaC1rZXktdjEAAAAA" not in cleaned
    assert "Use it on the staging box." in cleaned


def test_a_password_in_a_connection_string_goes_but_the_host_stays():
    """The host and the user are how a person recognises which system this
    was; only the password is dangerous."""
    cleaned, count = redact_secrets("postgres://appuser:s3cr3tpass@db.internal/prod")
    assert count == 1
    assert "s3cr3tpass" not in cleaned
    assert "appuser" in cleaned
    assert "db.internal" in cleaned


def test_a_connection_string_TEMPLATE_survives_intact():
    """Every setup guide contains one of these. Redacting the WORD "password"
    out of documentation teaches nobody anything and makes the instructions
    unreadable."""
    for template in (
        "postgresql://<user>:<password>@<host>:5432/<database>",
        "scheme://user:password@host",
        "postgresql://postgres.<ref>:…@<region>.pooler.supabase.com",
    ):
        cleaned, count = redact_secrets(template)
        assert count == 0, template
        assert cleaned == template


def test_an_assignment_keeps_its_name_and_loses_its_value():
    cleaned, count = redact_secrets('OPENAI_API_KEY="qX7-longEnoughValue123"')
    assert count == 1
    assert "qX7-longEnoughValue123" not in cleaned
    # The record still says a key was configured HERE, which is the useful part.
    assert "OPENAI_API_KEY" in cleaned


def test_ordinary_prose_about_secrets_is_left_alone():
    """The expensive failure mode. A redactor that eats sentences makes the
    knowledge base worse in a way nobody notices until a search comes back
    empty."""
    for sentence in (
        "The password is stored in the password manager, never in the repo.",
        "api_key: <your-key-here>",
        "SECRET_TOKEN=${DEPLOY_TOKEN}",
        "password = changeme",
        "Our token strategy is documented in the onboarding handbook.",
    ):
        cleaned, count = redact_secrets(sentence)
        assert count == 0, sentence
        assert cleaned == sentence


def test_an_item_without_secrets_passes_through_untouched():
    original = _item("Nothing sensitive here, just notes about the roadmap.")
    result = scrub_item(original)
    assert result.touched is False
    assert result.item is original


def test_a_secret_inside_an_ordinary_note_is_redacted_and_counted():
    result = scrub_item(_item(f"Deploy with {'ghp_' + 'z' * 36} today"))
    assert result.withheld is False
    assert result.redactions == 1
    assert "ghp_" not in result.item.body
    assert result.item.metadata["secret_redactions"] == 1
