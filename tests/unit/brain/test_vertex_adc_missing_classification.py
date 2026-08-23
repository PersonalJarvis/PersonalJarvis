"""A Vertex project path without Application Default Credentials is a DEAD
provider for the session (missing_key), not a transient call failure.

Live 2026-08-22 18:40:06 → 18:40:15 and 18:42:14 → 18:42:21 (gemini-live,
delegated voice turns): ``[brain.tool_model] provider = "vertex"`` on a host
with no gcloud login. Every delegated turn led the chain with Vertex, paid
google-auth's 7-10 s probe for the identical ``DefaultCredentialsError: Your
default credentials were not found``, and only then crossed to a provider that
could answer — the single biggest slice of a 34 s "tool model" turn. The
classifier knew keys, not ADC, so the miss was ``call_fail`` and never
dead-listed. Pinned here: both the raw google-auth wording and the fast-fail
message :func:`jarvis.core.google_genai.build_vertex_client` raises on a
remembered miss classify as ``missing_key``.
"""

from __future__ import annotations

from jarvis.brain.manager import _DEAD_LIST_KINDS, _classify_provider_error

GOOGLE_AUTH_WORDING = (
    "Your default credentials were not found. To set up Application Default "
    "Credentials, see https://cloud.google.com/docs/authentication/external/set-up-adc "
    "for more information."
)

FAST_FAIL_WORDING = (
    "Vertex AI is not configured on this host: Application Default Credentials "
    "were not found (DefaultCredentialsError: Your default credentials were not "
    "found). Run `gcloud auth application-default login` or point "
    "GOOGLE_APPLICATION_CREDENTIALS at a service-account file; the project path "
    "is retried after 600 s."
)


def test_google_auth_default_credentials_not_found_dead_lists_vertex() -> None:
    kind = _classify_provider_error(GOOGLE_AUTH_WORDING, default="call_fail")
    assert kind == "missing_key"
    assert kind in _DEAD_LIST_KINDS


def test_the_exception_class_name_alone_is_enough() -> None:
    kind = _classify_provider_error(
        "DefaultCredentialsError: could not automatically determine credentials",
        default="call_fail",
    )
    assert kind == "missing_key"


def test_the_fast_fail_message_dead_lists_too() -> None:
    kind = _classify_provider_error(FAST_FAIL_WORDING, default="call_fail")
    assert kind in _DEAD_LIST_KINDS


def test_an_ordinary_call_failure_is_untouched() -> None:
    assert (
        _classify_provider_error("Error code: 500 - internal error", default="call_fail")
        == "call_fail"
    )
