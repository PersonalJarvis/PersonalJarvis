"""Tests for `jarvis.voice.echo_confirmation` (Phase 7.4).

Plan acceptance criteria §7.4:
- Echo question follows the end-focus pattern (old first, new last)
- Misshear test: faked STT output „Karen" instead of „Charon" → user reject
- Language detection: templates adapt to `profile.language`

Plus prompt AC:
- Templater happy paths for numeric/string/bool/enum
- Sensitive: value does NOT appear in the sentence
- Pattern match is deterministic (Veto > Confirm > Ambiguous > Unknown)
"""
from __future__ import annotations

import pytest

from jarvis.core.self_mod import PendingMutation
from jarvis.voice.echo_confirmation import (
    classify_response,
    format_confirmation,
    format_outcome,
    is_sensitive_path,
)

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _pending(
    *,
    path: str,
    old_value,
    new_value,
    description: str = "Test-Pfad",
    risk_tier: str = "ask",
    requires_restart: bool = False,
    applied: bool = False,
) -> PendingMutation:
    from uuid import uuid4

    return PendingMutation(
        id=uuid4(),
        path=path,
        old_value=old_value,
        new_value=new_value,
        needs_confirmation=not applied,
        risk_tier=risk_tier,
        requires_restart=requires_restart,
        applied=applied,
        backup_path=None,
        description=description,
    )


# ----------------------------------------------------------------------
# Pattern-Match — Confirm/Veto/Ambiguous (Plan-§AP-12)
# ----------------------------------------------------------------------


class TestConfirmPatterns:
    @pytest.mark.parametrize(
        "transcript",
        [
            "ja",
            "Ja",
            "ja, mach das",
            "bestätige",  # i18n-allow
            "bestätigt",  # i18n-allow
            "mach",
            "los",
            "okay",
            "OK",
            "passt",
            "stimmt",
            "korrekt",
            "genau",
            "richtig",
        ],
    )
    def test_confirm_de(self, transcript: str) -> None:
        assert classify_response(transcript, language="de") == "confirm"

    @pytest.mark.parametrize(
        "transcript",
        [
            "yes",
            "confirm",
            "do it",
            "correct",
            "sure",
            "go ahead",
            "go for it",
        ],
    )
    def test_confirm_en(self, transcript: str) -> None:
        assert classify_response(transcript, language="en") == "confirm"


class TestVetoPatterns:
    @pytest.mark.parametrize(
        "transcript",
        [
            "nein",
            "Nein",
            "abbrechen",  # i18n-allow
            "abbruch",
            "stop",
            "stopp",
            "nicht",  # i18n-allow
            "doch nicht",  # i18n-allow
            "lass",
            "falsch",
        ],
    )
    def test_veto_de(self, transcript: str) -> None:
        assert classify_response(transcript, language="de") == "veto"

    def test_veto_priority_over_confirm(self) -> None:
        """Plan-§AP-12 Sicherheits-Bias: bei „nein, doch ja" gewinnt Veto."""
        assert classify_response("nein, doch ja", language="de") == "veto"


class TestAmbiguousPatterns:
    @pytest.mark.parametrize(
        "transcript",
        ["vielleicht", "warte", "moment", "weiß nicht", "ähm"],  # i18n-allow
    )
    def test_ambiguous_de(self, transcript: str) -> None:
        # WICHTIG: Plan-Sicherheits-Eigenschaft — ambiguous ist NIEMALS Confirm.
        result = classify_response(transcript, language="de")
        assert result in ("ambiguous", "veto"), (
            f"Ambiguous response '{transcript}' was interpreted as Confirm!"
        )
        assert result != "confirm"

    def test_unknown_returns_unknown(self) -> None:
        assert classify_response("xyz blub random", language="de") == "unknown"

    def test_empty_returns_unknown(self) -> None:
        assert classify_response("", language="de") == "unknown"
        assert classify_response("   ", language="de") == "unknown"


# ----------------------------------------------------------------------
# is_sensitive_path
# ----------------------------------------------------------------------


class TestSensitivePathDetection:
    @pytest.mark.parametrize(
        "path",
        [
            "security.admin_password_hash",
            "anthropic_api_key",
            "openai.api_key",
            "spotify_token",
            "deepgram_secret",
            "user_password",
            "auth_credential",
            # Sub-Agent-Review-MINOR-Erweiterung
            "auth.bearer",
            "github.pat",
            "google.oauth_token",
            "session_id",
            "session.cookie",
        ],
    )
    def test_known_sensitive(self, path: str) -> None:
        assert is_sensitive_path(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "tts.provider",
            "tts.speed",
            "ui.theme",
            "profile.language",
            "stt.provider",
            "brain.primary",
        ],
    )
    def test_allowlist_paths_not_sensitive(self, path: str) -> None:
        assert is_sensitive_path(path) is False


# ----------------------------------------------------------------------
# format_confirmation — End-Focus + Sensitive (Plan-§7.4)
# ----------------------------------------------------------------------


class TestFormatConfirmation:
    def test_string_value_de(self) -> None:
        pending = _pending(
            path="tts.provider",
            old_value="elevenlabs",
            new_value="gemini-flash-tts",
            description="TTS-Provider (Hot-Reload abgedeckt)",
        )
        sentence = format_confirmation(pending, language="de")
        # End-Focus-Plan-Template
        assert sentence.startswith("Verstanden — TTS-Provider")
        assert "von elevenlabs zu gemini-flash-tts" in sentence
        assert sentence.rstrip(".?!").endswith("Bestätigen")  # i18n-allow

    def test_numeric_value_de(self) -> None:
        pending = _pending(
            path="tts.speed",
            old_value=1.0,
            new_value=1.25,
            description="TTS-Sprechgeschwindigkeit",
        )
        sentence = format_confirmation(pending, language="de")
        assert "1.0" in sentence
        assert "1.25" in sentence

    def test_bool_value_de(self) -> None:
        pending = _pending(
            path="ui.theme",  # synthetisch — als Tier="safe" gemockt
            old_value=False,
            new_value=True,
            description="Dark-Mode",
        )
        sentence = format_confirmation(pending, language="de")
        # bool is rendered as "an"/"aus" (not as True/False)
        assert "von aus zu an" in sentence  # i18n-allow

    def test_label_strips_parenthesis(self) -> None:
        pending = _pending(
            path="profile.language",
            old_value="de",
            new_value="en",
            description="Profil-Sprache (wirkt sofort in nächster Antwort)",  # i18n-allow
        )
        sentence = format_confirmation(pending, language="de")
        assert "Profil-Sprache" in sentence  # i18n-allow
        # Parenthesis content must NOT land in the TTS output
        assert "wirkt sofort" not in sentence

    def test_english_template(self) -> None:
        pending = _pending(
            path="tts.provider",
            old_value="elevenlabs",
            new_value="gemini-flash-tts",
            description="TTS provider",
        )
        sentence = format_confirmation(pending, language="en")
        assert sentence.startswith("Got it — TTS provider")
        assert "from elevenlabs to gemini-flash-tts" in sentence
        assert sentence.rstrip(".?!").endswith("Confirm")


class TestEndFocusTokenPosition:
    """Plan-AC §7.4: End-Focus — `new_value` in den letzten Tokens."""

    @pytest.mark.parametrize(
        ("path", "old", "new"),
        [
            ("tts.provider", "elevenlabs", "gemini-flash-tts"),
            ("tts.speed", 1.0, 1.25),
            ("profile.language", "de", "en"),
        ],
    )
    def test_new_value_within_last_3_tokens(self, path, old, new) -> None:
        pending = _pending(path=path, old_value=old, new_value=new)
        sentence = format_confirmation(pending, language="de")
        tokens = sentence.split()
        last_three = " ".join(tokens[-3:])
        assert str(new) in last_three, (
            f"new_value '{new}' not in the last 3 tokens: {last_three!r}"
        )


# ----------------------------------------------------------------------
# Sensitive-Path-Leak-Test (Plan-§AP-2 Defense-in-Depth)
# ----------------------------------------------------------------------


class TestSensitiveLeak:
    SENTINEL = "ABC123XYZ_NEVER_LEAK_ME"

    @pytest.mark.parametrize(
        "path",
        [
            "security.admin_password_hash",
            "anthropic_api_key",
            "openai_token",
            "user_password",
            "deepgram_secret",
        ],
    )
    def test_sensitive_value_not_in_sentence(self, path: str) -> None:
        pending = _pending(
            path=path,
            old_value="prev",
            new_value=self.SENTINEL,
            description="Sensitive-Test-Pfad",
        )
        sentence_de = format_confirmation(pending, language="de")
        sentence_en = format_confirmation(pending, language="en")
        assert self.SENTINEL not in sentence_de, (
            f"Klartext-Secret im DE-TTS-Output (path={path}): {sentence_de}"
        )
        assert self.SENTINEL not in sentence_en, (
            f"Klartext-Secret im EN-TTS-Output (path={path}): {sentence_en}"
        )
        # But the phrase must still be semantically meaningful
        assert "neuen Wert" in sentence_de or "new value" in sentence_en

    def test_outcome_does_not_leak_either(self) -> None:
        pending = _pending(
            path="anthropic_api_key",
            old_value="prev",
            new_value=self.SENTINEL,
            description="API-Key",
        )
        for kind in ("safe_applied", "applied", "validate_failed", "vetoed", "timeout"):
            sentence_de = format_outcome(kind, pending, language="de")
            sentence_en = format_outcome(kind, pending, language="en")
            assert self.SENTINEL not in sentence_de, (
                f"Secret leakt in DE-Outcome '{kind}': {sentence_de}"
            )
            assert self.SENTINEL not in sentence_en, (
                f"Secret leakt in EN-Outcome '{kind}': {sentence_en}"
            )

    def test_outcome_validate_failed_with_short_error_blocks_leak(
        self,
    ) -> None:
        """Sub-Agent-review blocker: short_error can carry the new_value via
        the Pydantic message. For a sensitive path it must be discarded.
        """
        pending = _pending(
            path="anthropic_api_key",
            old_value="prev",
            new_value=self.SENTINEL,
            description="API-Key",
        )
        # Wir simulieren einen short_error wie writer.py ihn liefert
        fake_msg = (
            f"Pre-validate for 'anthropic_api_key' = '{self.SENTINEL}' "
            "failed: 1 validation error"
        )
        sentence_de = format_outcome(
            "validate_failed",
            pending,
            language="de",
            short_error=fake_msg[:80],
        )
        sentence_en = format_outcome(
            "validate_failed",
            pending,
            language="en",
            short_error=fake_msg[:80],
        )
        assert self.SENTINEL not in sentence_de
        assert self.SENTINEL not in sentence_en

    def test_short_error_from_pre_validate_exception_does_not_contain_repr(
        self,
    ) -> None:
        """short_error_from_exception(PreValidateError) must NOT pass through
        the repr value from the original message.
        """
        from jarvis.core.self_mod import PreValidateError
        from jarvis.voice.echo_confirmation import short_error_from_exception

        secret = "sk-leakable-XYZ-789"  # noqa: S105 — Test-Fixture
        exc = PreValidateError(
            f"Pre-validate for 'x' = {secret!r} failed: bla"
        )
        result = short_error_from_exception(exc)
        assert secret not in result


# ----------------------------------------------------------------------
# Outcome-Templates (Plan-§7.4-Tabelle)
# ----------------------------------------------------------------------


class TestOutcomeTemplates:
    def test_safe_applied(self) -> None:
        pending = _pending(
            path="tts.speed", old_value=1.0, new_value=1.25,
            description="TTS-Sprechgeschwindigkeit", applied=True,
        )
        sentence = format_outcome("safe_applied", pending, language="de")
        assert "Geht klar" in sentence
        assert "1.25" in sentence

    def test_applied_with_restart(self) -> None:
        pending = _pending(
            path="brain.primary", old_value="claude", new_value="gemini",
            description="Primärer Brain-Provider", requires_restart=True,  # i18n-allow
        )
        sentence = format_outcome("applied_restart", pending, language="de")
        assert "neustarten" in sentence

    def test_vetoed(self) -> None:
        pending = _pending(path="tts.speed", old_value=1.0, new_value=1.5)
        sentence = format_outcome("vetoed", pending, language="de")
        assert "Okay" in sentence

    def test_timeout(self) -> None:
        pending = _pending(path="tts.speed", old_value=1.0, new_value=1.5)
        sentence = format_outcome("timeout", pending, language="de")
        assert "1.0" in sentence  # Setting bleibt {old}
        assert "brech ich ab" in sentence
