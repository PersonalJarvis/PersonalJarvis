"""``jarvis.core.redact`` — the redact-and-cap gate for decision-log previews.

Every persisted telemetry preview (a tool's ``output_preview``, the brain's
``rationale``) passes through ``safe_preview`` before it reaches the bus / the
session DB / the local Markdown diary. These tests pin the two guarantees that
make persisting such previews safe: credential shapes are masked, and the length
is capped so a tool cannot smuggle a large blob into the log.
"""
from __future__ import annotations

from jarvis.core.redact import DEFAULT_PREVIEW_CHARS, redact_secrets, safe_preview


def test_plain_text_passes_through_untouched() -> None:
    text = "Listed 3 GCP projects: alpha, beta, gamma. Total spend last month: 12.40 EUR."
    assert safe_preview(text) == text


def test_openai_style_key_is_masked() -> None:
    leaked = "your key is sk-proj-AbCdEf0123456789ghijKLmnopQRstuv and it works"
    out = safe_preview(leaked)
    assert "sk-proj-AbCdEf0123456789ghijKLmnopQRstuv" not in out
    assert "<redacted:openai_key>" in out
    # The surrounding prose survives so the log stays readable.
    assert out.startswith("your key is ")


def test_provider_and_bearer_tokens_are_masked() -> None:
    google = "AIzaSyA1234567890abcdefghijklmnopqrstuvwx"
    bearer = "Bearer abcDEF1234567890ghijKL"
    out = safe_preview(f"google={google}\nauth={bearer}")
    assert google not in out
    assert "abcDEF1234567890ghijKL" not in out
    assert "<redacted:provider_key>" in out
    assert "<redacted:bearer_token>" in out


def test_labelled_secret_keeps_label_drops_value() -> None:
    out = safe_preview("api_key=supersecretvalue123")
    assert "supersecretvalue123" not in out
    # Label survives for readability; value is gone.
    assert "api_key=" in out
    assert "<redacted:labelled_secret>" in out


def test_git_sha1_is_not_treated_as_a_secret() -> None:
    # A 40-char hex is a git commit SHA — legitimate, must NOT be masked.
    sha = "a" * 40
    assert sha in safe_preview(f"committed at {sha}")


def test_length_is_capped_with_honest_marker() -> None:
    # Spaced prose so it is long without being one 64+ char credential-shaped run.
    big = "word " * (DEFAULT_PREVIEW_CHARS)  # ~5x over the cap
    out = safe_preview(big)
    assert len(out) <= DEFAULT_PREVIEW_CHARS + 40  # cap + short marker
    assert "more chars)" in out


def test_custom_cap_is_respected() -> None:
    out = safe_preview("abcdefghij", max_chars=4)
    assert out.startswith("abcd")
    assert "more chars)" in out


def test_none_and_non_string_values_are_stringified() -> None:
    assert safe_preview(None) == ""
    assert safe_preview({"projects": 3}) == "{'projects': 3}"
    assert safe_preview(42) == "42"


def test_redact_secrets_is_pure_and_idempotent() -> None:
    once = redact_secrets("token: eyJabcdef.eyJ123456.sigABC123")
    twice = redact_secrets(once)
    assert once == twice
    assert "<redacted:jwt>" in once
