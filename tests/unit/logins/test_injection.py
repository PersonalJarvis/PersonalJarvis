"""The rule that makes the vault safe: a password never reaches the model.

These tests guard the substitution boundary in both directions — the value goes
INTO the harness script without appearing in anything the model wrote, and it
does not come back OUT through the script's output.
"""

from __future__ import annotations

import pytest

from jarvis.logins.injection import (
    SecretResolutionError,
    has_secret_placeholder,
    needs_confirmation,
    resolve_secrets,
    scrub_secrets,
)
from jarvis.logins.store import Credential, CredentialStore, LoginStatus
from jarvis.marketplace.token_store import InMemoryBackend

_PASSWORD = "s3cr3t-value-here"  # noqa: S105
_TOTP = "JBSWY3DPEHPK3PXP"  # noqa: S105


@pytest.fixture
def store() -> CredentialStore:
    store = CredentialStore(backend=InMemoryBackend())
    store.save(
        Credential(
            service_id="github",
            label="GitHub",
            domains=("github.com",),
            username="someone@example.com",
            password=_PASSWORD,
            totp_secret=_TOTP,
        )
    )
    return store


class TestDetection:
    @pytest.mark.parametrize(
        "script",
        [
            'fill(node, SECRET("github", "password"))',
            "fill(node, SECRET('github', 'password'))",
            'SECRET( "github" , "password" )',
        ],
    )
    def test_recognises_a_placeholder(self, script: str) -> None:
        assert has_secret_placeholder(script) is True

    @pytest.mark.parametrize("script", ["print(page_info())", "", "SECRET = 1"])
    def test_ignores_scripts_without_one(self, script: str) -> None:
        assert has_secret_placeholder(script) is False


class TestResolution:
    def test_the_value_replaces_the_placeholder(self, store: CredentialStore) -> None:
        result = resolve_secrets('fill(node, SECRET("github", "password"))', store)

        assert _PASSWORD in result.script
        assert "SECRET(" not in result.script

    def test_a_script_without_placeholders_is_untouched(
        self, store: CredentialStore
    ) -> None:
        script = "print(page_info())"

        result = resolve_secrets(script, store)

        assert result.script == script
        assert result.secrets == ()
        assert result.touched_vault is False

    def test_the_username_resolves_too(self, store: CredentialStore) -> None:
        result = resolve_secrets('type(SECRET("github", "username"))', store)

        assert "someone@example.com" in result.script

    def test_the_totp_seed_resolves(self, store: CredentialStore) -> None:
        result = resolve_secrets('totp(SECRET("github", "totp"))', store)

        assert _TOTP in result.script

    def test_several_placeholders_resolve_in_one_script(
        self, store: CredentialStore
    ) -> None:
        result = resolve_secrets(
            'type(SECRET("github", "username")); type(SECRET("github", "password"))',
            store,
        )

        assert "someone@example.com" in result.script
        assert _PASSWORD in result.script
        assert result.services == ("github",)

    def test_the_touched_service_is_reported(self, store: CredentialStore) -> None:
        result = resolve_secrets('SECRET("github", "password")', store)

        assert result.services == ("github",)
        assert result.touched_vault is True


class TestQuotingSafety:
    @pytest.mark.parametrize(
        "password",
        [
            "has'a'quote",
            'has"double"quotes',
            "has\\a\\backslash",
            "has\nnewline",
            "unicode-ümläut-密码",  # i18n-allow: non-ASCII password fixture
        ],
    )
    def test_an_awkward_password_still_produces_valid_python(
        self, password: str
    ) -> None:
        # A naive string splice would produce a syntax error here, and the
        # failure would look like a harness bug rather than a quoting bug.
        store = CredentialStore(backend=InMemoryBackend())
        store.save(
            Credential(
                service_id="awkward",
                label="Awkward",
                domains=("example.com",),
                username="u",
                password=password,
            )
        )

        result = resolve_secrets('pw = SECRET("awkward", "password")', store)

        compile(result.script, "<resolved>", "exec")
        namespace: dict[str, object] = {}
        exec(result.script, namespace)  # noqa: S102 -- verifying the literal round-trips
        assert namespace["pw"] == password


class TestFailures:
    def test_an_unknown_service_fails_loudly(self, store: CredentialStore) -> None:
        with pytest.raises(SecretResolutionError, match="No stored login"):
            resolve_secrets('SECRET("nosuchsite", "password")', store)

    def test_an_unknown_field_fails_loudly(self, store: CredentialStore) -> None:
        with pytest.raises(SecretResolutionError, match="Unknown credential field"):
            resolve_secrets('SECRET("github", "pin")', store)

    def test_an_empty_field_fails_loudly(self) -> None:
        store = CredentialStore(backend=InMemoryBackend())
        store.save(
            Credential(
                service_id="halfdone",
                label="Half done",
                domains=("example.com",),
                username="u",
                password="",
            )
        )

        with pytest.raises(SecretResolutionError, match="has no password"):
            resolve_secrets('SECRET("halfdone", "password")', store)

    def test_the_failure_message_never_quotes_a_stored_value(
        self, store: CredentialStore
    ) -> None:
        with pytest.raises(SecretResolutionError) as excinfo:
            resolve_secrets('SECRET("github", "pin")', store)

        assert _PASSWORD not in str(excinfo.value)


class TestScrubbing:
    def test_an_injected_value_is_redacted_from_output(self) -> None:
        cleaned = scrub_secrets(f"the field held {_PASSWORD} after typing", (_PASSWORD,))

        assert _PASSWORD not in cleaned
        assert "[redacted]" in cleaned

    def test_ordinary_output_is_left_alone(self) -> None:
        text = "Signed in as someone@example.com"

        assert scrub_secrets(text, (_PASSWORD,)) == text

    def test_nothing_to_scrub_returns_the_text(self) -> None:
        assert scrub_secrets("hello", ()) == "hello"

    def test_a_shorter_secret_cannot_partially_redact_a_longer_one(self) -> None:
        # "abcd" is a substring of "abcdefgh": redacting the short one first
        # would leave "efgh" of the long one readable in the output.
        cleaned = scrub_secrets("value=abcdefgh", ("abcd", "abcdefgh"))

        assert cleaned == "value=[redacted]"

    def test_very_short_values_are_not_redacted(self) -> None:
        # Blanking every "ab" would shred the page text while protecting
        # nothing that was not already trivially guessable.
        text = "about a table"

        assert scrub_secrets(text, ("ab",)) == text

    def test_the_username_is_not_scrubbed(self, store: CredentialStore) -> None:
        result = resolve_secrets('type(SECRET("github", "username"))', store)

        assert result.secrets == ()


class TestConfirmation:
    def test_an_unproven_credential_asks_first(self, store: CredentialStore) -> None:
        assert needs_confirmation(("github",), store) is True

    def test_a_proven_credential_runs_silently(self, store: CredentialStore) -> None:
        store.mark_used("github", LoginStatus.OK)

        assert needs_confirmation(("github",), store) is False

    def test_a_rejected_credential_asks_again(self, store: CredentialStore) -> None:
        store.mark_used("github", LoginStatus.OK)
        store.mark_used("github", LoginStatus.REJECTED)

        assert needs_confirmation(("github",), store) is True

    def test_an_unknown_service_asks(self, store: CredentialStore) -> None:
        assert needs_confirmation(("nosuchsite",), store) is True

    def test_one_unproven_service_makes_the_whole_call_ask(
        self, store: CredentialStore
    ) -> None:
        store.mark_used("github", LoginStatus.OK)
        store.save(
            Credential(
                service_id="fresh",
                label="Fresh",
                domains=("fresh.example",),
                username="u",
                password="p",  # noqa: S106
            )
        )

        assert needs_confirmation(("github", "fresh"), store) is True

    def test_no_services_needs_no_confirmation(self, store: CredentialStore) -> None:
        assert needs_confirmation((), store) is False
