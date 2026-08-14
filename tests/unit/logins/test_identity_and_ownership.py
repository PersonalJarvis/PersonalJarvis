"""Two identities in one vault, and the card behind the assistant's own.

The behaviour under test is a separation, so most of these are negative: what
must NOT happen is that a lookup for one identity quietly answers with the
other's account. That mistake has no visible symptom at the moment it happens —
the login simply succeeds, under the wrong name — which is exactly why it is
pinned here rather than left to review.

Every test drives an in-memory backend; the real keychain is never touched.
"""

from __future__ import annotations

import pytest

from jarvis.logins.identity import AgentIdentity, IdentityStore
from jarvis.logins.injection import SecretResolutionError, resolve_secrets, scrub_secrets
from jarvis.logins.store import (
    Credential,
    CredentialOwner,
    CredentialStore,
)
from jarvis.marketplace.token_store import InMemoryBackend

# Fixture values, not real secrets.
_USER_PASSWORD = "user-side-hunter2"  # noqa: S105
_AGENT_PASSWORD = "agent-side-hunter2"  # noqa: S105
_API_TOKEN = "tok-live-abcdef123456"  # noqa: S105


@pytest.fixture
def store() -> CredentialStore:
    return CredentialStore(backend=InMemoryBackend())


@pytest.fixture
def both(store: CredentialStore) -> CredentialStore:
    """One vault holding the user's forge account and the assistant's own."""
    store.save(
        Credential(
            service_id="github-user",
            label="GitHub (mine)",
            domains=("github.com",),
            username="ruben@example.com",
            password=_USER_PASSWORD,
        )
    )
    store.save(
        Credential(
            service_id="github-agent",
            label="GitHub (assistant)",
            domains=("github.com",),
            username="assistant@example.com",
            password=_AGENT_PASSWORD,
            owner=CredentialOwner.AGENT,
        )
    )
    return store


class TestOwnership:
    def test_a_record_belongs_to_the_user_unless_told_otherwise(
        self, store: CredentialStore
    ) -> None:
        """The pre-existing meaning of every record, preserved."""
        stored = store.save(
            Credential(
                service_id="bank",
                label="Bank",
                domains=("bank.example",),
                username="me",
                password=_USER_PASSWORD,
            )
        )

        assert stored.owner is CredentialOwner.USER

    def test_a_record_written_before_the_field_existed_reads_as_the_users(self) -> None:
        """Back-compat, asserted on the wire format rather than through a save.

        A record already in someone's keychain has no ``owner`` key at all, and
        it must not become the assistant's by upgrading.
        """
        legacy = (
            '{"service_id":"old","label":"Old","domains":["old.example"],'
            '"username":"u","password":"p","notes":"","totp_secret":null,'
            '"status":"ok","created_at":null,"updated_at":null,"last_used_at":null}'
        )

        restored = Credential.from_json(legacy)

        assert restored.owner is CredentialOwner.USER
        assert restored.kind == "website"

    def test_an_unknown_owner_from_a_newer_build_reads_as_the_users(self) -> None:
        """Fail towards the identity that asks for confirmation, not away."""
        forward = (
            '{"service_id":"x","label":"X","domains":[],"username":"u",'
            '"password":"p","owner":"some-future-owner"}'
        )

        assert Credential.from_json(forward).owner is CredentialOwner.USER

    def test_listing_can_show_one_side_of_the_ledger(
        self, both: CredentialStore
    ) -> None:
        mine = both.list_summaries(CredentialOwner.USER)
        theirs = both.list_summaries(CredentialOwner.AGENT)

        assert [s.service_id for s in mine] == ["github-user"]
        assert [s.service_id for s in theirs] == ["github-agent"]
        assert len(both.list_summaries()) == 2

    def test_a_domain_lookup_for_the_assistant_never_returns_the_users(
        self, both: CredentialStore
    ) -> None:
        """The core defect this feature exists to prevent.

        Both records match ``github.com``. Asking as the assistant and being
        handed the user's account would sign the user in — silently, and with
        their session left behind in the browser profile.
        """
        found = both.find_for_url("https://github.com/login", CredentialOwner.AGENT)

        assert found is not None
        assert found.service_id == "github-agent"
        assert found.username == "assistant@example.com"

    def test_a_domain_lookup_without_an_owner_still_answers(
        self, both: CredentialStore
    ) -> None:
        """"Is anything stored for this site" stays a legitimate question."""
        assert both.find_for_url("github.com") is not None

    def test_an_owner_filter_that_matches_nothing_returns_nothing(
        self, store: CredentialStore
    ) -> None:
        """Not "fall back to the other one" — that is the whole bug."""
        store.save(
            Credential(
                service_id="onlymine",
                label="Only mine",
                domains=("example.com",),
                username="u",
                password=_USER_PASSWORD,
            )
        )

        assert store.find_for_url("example.com", CredentialOwner.AGENT) is None


class TestAnyKindOfConnection:
    def test_a_connection_can_carry_its_own_fields_and_secrets(
        self, store: CredentialStore
    ) -> None:
        """An API token and a website login are the same shape of record."""
        stored = store.save(
            Credential(
                service_id="mailbox",
                label="Assistant mailbox",
                domains=("mail.example",),
                username="assistant@example.com",
                password=_AGENT_PASSWORD,
                owner=CredentialOwner.AGENT,
                kind="email",
                fields={"imap_host": "imap.example"},  # type: ignore[arg-type]
                secrets={"app_password": _API_TOKEN},  # type: ignore[arg-type]
            )
        )

        assert stored.kind == "email"
        assert stored.field_map() == {"imap_host": "imap.example"}
        assert stored.secret_map() == {"app_password": _API_TOKEN}

    def test_a_dict_passed_by_a_caller_survives_the_round_trip(
        self, store: CredentialStore
    ) -> None:
        """Saving normalises to pairs, so the frozen record stays hashable."""
        store.save(
            Credential(
                service_id="hub",
                label="Hub",
                domains=("hub.example",),
                username="u",
                password=_USER_PASSWORD,
                fields={"base_url": "https://hub.example/api"},  # type: ignore[arg-type]
            )
        )

        reloaded = store.load("hub")

        assert reloaded is not None
        assert reloaded.field_map() == {"base_url": "https://hub.example/api"}
        assert {reloaded}  # hashable: a dict in the record would raise here

    def test_a_summary_names_extra_secrets_but_never_carries_them(
        self, store: CredentialStore
    ) -> None:
        stored = store.save(
            Credential(
                service_id="api",
                label="Some API",
                domains=("api.example",),
                username="u",
                password="",
                secrets={"api_key": _API_TOKEN},  # type: ignore[arg-type]
            )
        )

        summary = stored.summary()

        assert summary.secret_names == ("api_key",)
        assert _API_TOKEN not in repr(summary)
        assert _API_TOKEN not in str(summary.to_public_dict())


class TestInjectionOfExtraSecrets:
    @pytest.fixture
    def api_store(self, store: CredentialStore) -> CredentialStore:
        store.save(
            Credential(
                service_id="api",
                label="Some API",
                domains=("api.example",),
                username="robot",
                password="",
                owner=CredentialOwner.AGENT,
                kind="api",
                fields={"base_url": "https://api.example"},  # type: ignore[arg-type]
                secrets={"api_key": _API_TOKEN},  # type: ignore[arg-type]
            )
        )
        return store

    def test_an_extra_secret_can_be_referenced_by_name(
        self, api_store: CredentialStore
    ) -> None:
        resolution = resolve_secrets('h = SECRET("api", "api_key")', api_store)

        assert _API_TOKEN in resolution.script
        assert resolution.secrets == (_API_TOKEN,)

    def test_the_reference_is_matched_case_insensitively(
        self, api_store: CredentialStore
    ) -> None:
        """The model writes this from a name a human typed into a form."""
        resolution = resolve_secrets('h = SECRET("api", "API_KEY")', api_store)

        assert _API_TOKEN in resolution.script

    def test_a_non_secret_field_resolves_without_being_redacted(
        self, api_store: CredentialStore
    ) -> None:
        """Redacting a base URL would shred the page text the user wants read."""
        resolution = resolve_secrets('u = SECRET("api", "base_url")', api_store)

        assert "https://api.example" in resolution.script
        assert resolution.secrets == ()

    def test_an_extra_secret_is_redacted_out_of_the_output(
        self, api_store: CredentialStore
    ) -> None:
        resolution = resolve_secrets('h = SECRET("api", "api_key")', api_store)

        cleaned = scrub_secrets(f"the header was {_API_TOKEN}", resolution.secrets)

        assert _API_TOKEN not in cleaned

    def test_an_unknown_field_names_what_the_record_holds(
        self, api_store: CredentialStore
    ) -> None:
        with pytest.raises(SecretResolutionError, match="api_key"):
            resolve_secrets('SECRET("api", "nope")', api_store)


class TestAgentIdentity:
    @pytest.fixture
    def identity(self) -> IdentityStore:
        return IdentityStore(backend=InMemoryBackend())

    def test_an_unset_identity_is_empty_rather_than_an_error(
        self, identity: IdentityStore
    ) -> None:
        profile = identity.load()

        assert profile.is_configured is False
        assert profile.email == ""

    def test_a_damaged_entry_reads_as_unset(self) -> None:
        """A caller asking "who am I" must not be taken out by a bad write."""
        backend = InMemoryBackend()
        backend.set("agent_identity", "{not json at all")

        assert IdentityStore(backend=backend).load().is_configured is False

    def test_a_saved_identity_round_trips(self, identity: IdentityStore) -> None:
        stored = identity.save(
            AgentIdentity(
                email="assistant@example.com",
                phone="+49 000 0000000",
                handles={"github": "assistant-machine-user"},  # type: ignore[arg-type]
            )
        )

        assert stored.updated_at is not None
        reloaded = identity.load()
        assert reloaded.email == "assistant@example.com"
        assert reloaded.handle_map() == {"github": "assistant-machine-user"}
        assert reloaded.is_configured is True

    def test_a_timezone_alone_is_not_an_identity(
        self, identity: IdentityStore
    ) -> None:
        """Nothing can be registered under a timezone.

        Reporting it as configured would send the assistant into a signup it
        has no way of completing.
        """
        identity.save(AgentIdentity(timezone="Europe/Berlin"))

        assert identity.load().is_configured is False

    def test_the_display_name_falls_back_to_the_wake_word_name(self) -> None:
        """Contract §4: the visible name is never hardcoded here."""
        assert AgentIdentity().display_name_or("Athena") == "Athena"
        assert AgentIdentity(display_name="Jarvis").display_name_or("Athena") == "Jarvis"

    def test_the_model_card_states_the_gaps_it_would_get_stuck_on(self) -> None:
        card = AgentIdentity(email="assistant@example.com").describe_for_model("Athena")

        assert "assistant@example.com" in card
        assert "phone number" in card

    def test_an_empty_card_forbids_borrowing_the_users_details(self) -> None:
        card = AgentIdentity().describe_for_model("Athena")

        assert "no identity of your own yet" in card
        assert "never use the user's address" in card

    def test_clearing_is_idempotent(self, identity: IdentityStore) -> None:
        identity.save(AgentIdentity(email="assistant@example.com"))

        assert identity.clear() is True
        assert identity.clear() is False
        assert identity.load().is_configured is False
