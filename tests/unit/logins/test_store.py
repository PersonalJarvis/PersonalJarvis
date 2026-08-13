"""The login vault's storage contract.

Every test drives an in-memory backend — the real keychain is never touched, so
these run on a headless CI box exactly as they do on the maintainer's Windows
machine.
"""

from __future__ import annotations

import pytest

from jarvis.logins.store import (
    Credential,
    CredentialStore,
    LoginStatus,
    normalize_domain,
    normalize_service_id,
)
from jarvis.marketplace.token_store import InMemoryBackend

# Fixture values, not real secrets — named once so the linter's hardcoded-password
# rule is answered in one place instead of at every use site.
_PASSWORD = "hunter2-correct-horse"  # noqa: S105
_ROTATED = "rotated-password"  # noqa: S105
_TOTP = "JBSWY3DPEHPK3PXP"  # noqa: S105


@pytest.fixture
def store() -> CredentialStore:
    return CredentialStore(backend=InMemoryBackend())


def _github(**overrides: object) -> Credential:
    base = {
        "service_id": "github",
        "label": "GitHub",
        "domains": ("github.com",),
        "username": "someone@example.com",
        "password": _PASSWORD,
        "notes": "# GitHub\n\nDeveloper account. 2FA prompts on a new device.",
    }
    base.update(overrides)
    return Credential(**base)  # type: ignore[arg-type]


class TestRoundTrip:
    def test_save_then_load_returns_every_field(self, store: CredentialStore) -> None:
        store.save(_github())

        loaded = store.load("github")

        assert loaded is not None
        assert loaded.username == "someone@example.com"
        assert loaded.password == _PASSWORD
        assert loaded.notes.startswith("# GitHub")
        assert loaded.domains == ("github.com",)

    def test_save_stamps_created_and_updated(self, store: CredentialStore) -> None:
        stored = store.save(_github())

        assert stored.created_at is not None
        assert stored.updated_at is not None

    def test_resaving_keeps_the_original_creation_time(
        self, store: CredentialStore
    ) -> None:
        first = store.save(_github())

        second = store.save(_github(password=_ROTATED))

        assert second.created_at == first.created_at
        assert store.load("github").password == _ROTATED  # type: ignore[union-attr]

    def test_a_long_markdown_note_survives(self, store: CredentialStore) -> None:
        # Well past the ~1280-char Credential Manager entry cap: this is the
        # case the chunking layer exists for, and notes are where it bites.
        note = "# Bank\n\n" + ("Log in, then confirm in the app. " * 200)
        store.save(_github(service_id="bank", label="Bank", notes=note))

        assert store.load("bank").notes == note  # type: ignore[union-attr]


class TestSecretsStayOut:
    def test_summary_carries_no_password(self, store: CredentialStore) -> None:
        summary = store.save(_github()).summary()

        assert not hasattr(summary, "password")
        assert "hunter2" not in repr(summary)
        assert summary.has_password is True

    def test_public_dict_carries_no_password(self, store: CredentialStore) -> None:
        payload = store.save(_github()).summary().to_public_dict()

        assert "password" not in payload
        assert "hunter2" not in str(payload)

    def test_repr_of_the_full_record_hides_the_secrets(self) -> None:
        # A dataclass repr leaks into exception messages and test output without
        # anyone deciding to print it — this is the guard for that path.
        text = repr(_github(totp_secret=_TOTP))

        assert _PASSWORD not in text
        assert _TOTP not in text


class TestListing:
    def test_lists_by_label_case_insensitively(self, store: CredentialStore) -> None:
        store.save(_github(service_id="zulip", label="zulip", domains=("zulip.com",)))
        store.save(_github(service_id="apple", label="Apple", domains=("apple.com",)))

        labels = [s.label for s in store.list_summaries()]

        assert labels == ["Apple", "zulip"]

    def test_empty_vault_lists_nothing(self, store: CredentialStore) -> None:
        assert store.list_summaries() == []

    def test_an_index_entry_without_a_record_is_healed(
        self, store: CredentialStore
    ) -> None:
        backend = InMemoryBackend()
        store = CredentialStore(backend=backend)
        store.save(_github())
        # Simulate a delete that died between removing the record and the index.
        backend.delete("credential_github")

        assert store.list_summaries() == []
        # And the stale id is gone, not re-reported on the next call.
        assert backend.get("credential_index") == "[]"


class TestUrlMatching:
    @pytest.mark.parametrize(
        "candidate",
        [
            "github.com",
            "https://github.com/login",
            "https://www.github.com/",
            "GITHUB.COM",
            "https://gist.github.com/x",
        ],
    )
    def test_matching_hosts_and_subdomains(
        self, store: CredentialStore, candidate: str
    ) -> None:
        store.save(_github())

        found = store.find_for_url(candidate)

        assert found is not None
        assert found.service_id == "github"

    @pytest.mark.parametrize("candidate", ["example.com", "notgithub.com", ""])
    def test_non_matching_hosts(self, store: CredentialStore, candidate: str) -> None:
        store.save(_github())

        assert store.find_for_url(candidate) is None

    def test_the_most_specific_entry_wins(self, store: CredentialStore) -> None:
        store.save(_github(service_id="google", label="Google", domains=("google.com",)))
        store.save(
            _github(
                service_id="gsuite",
                label="Workspace",
                domains=("accounts.google.com",),
            )
        )

        found = store.find_for_url("https://accounts.google.com/signin")

        assert found is not None
        assert found.service_id == "gsuite"


class TestStatus:
    def test_a_new_record_is_unproven(self, store: CredentialStore) -> None:
        assert store.save(_github()).status is LoginStatus.UNKNOWN

    def test_marking_used_records_outcome_and_time(
        self, store: CredentialStore
    ) -> None:
        store.save(_github())

        store.mark_used("github", LoginStatus.OK)

        loaded = store.load("github")
        assert loaded is not None
        assert loaded.status is LoginStatus.OK
        assert loaded.last_used_at is not None

    def test_marking_an_unknown_service_is_a_no_op(
        self, store: CredentialStore
    ) -> None:
        store.mark_used("nope", LoginStatus.OK)  # must not raise


class TestDelete:
    def test_delete_removes_record_and_index_entry(
        self, store: CredentialStore
    ) -> None:
        store.save(_github())

        assert store.delete("github") is True
        assert store.load("github") is None
        assert store.list_summaries() == []

    def test_deleting_an_absent_record_reports_false(
        self, store: CredentialStore
    ) -> None:
        assert store.delete("github") is False


class TestNormalisation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("GitHub", "github"),
            ("my bank", "my-bank"),
            ("  Spaced  Out  ", "spaced-out"),
            ("a//b", "a-b"),
        ],
    )
    def test_service_ids_fold_to_slugs(self, raw: str, expected: str) -> None:
        assert normalize_service_id(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", "///"])
    def test_unusable_service_ids_are_rejected(self, raw: str) -> None:
        with pytest.raises(ValueError, match="service id"):
            normalize_service_id(raw)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("https://www.Example.com/path?q=1", "example.com"),
            ("example.com", "example.com"),
            ("WWW.EXAMPLE.COM", "example.com"),
            ("", ""),
        ],
    )
    def test_domains_fold_to_bare_hosts(self, raw: str, expected: str) -> None:
        assert normalize_domain(raw) == expected

    def test_saving_normalises_the_domains_it_stores(
        self, store: CredentialStore
    ) -> None:
        stored = store.save(
            _github(domains=("https://www.GitHub.com/login", "github.com"))
        )

        # Both forms collapse to the same host, and the duplicate is dropped.
        assert stored.domains == ("github.com",)
