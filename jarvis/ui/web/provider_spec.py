"""Declarative description of all Brain/TTS/STT providers for the desktop app.

Single source of truth for the UI: which providers exist, what auth method
do they need, which credential-manager slot stores their key, which login
CLI needs to be spawned? Deliberately NO model names — models change too
often, and the default comes from jarvis.toml. The UI renders a generic
widget per provider based on auth_mode.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AuthMode = Literal["api_key", "codex", "antigravity", "none"]
Tier = Literal["brain", "tts", "stt"]
# How using a provider is billed. Derived from auth_mode (never branched on a
# provider name — see provider_billing): an API key bills per token on an API
# account; a subscription provider runs over an existing plan login; codex can
# do either; a local provider needs no credential at all.
Billing = Literal["api", "subscription", "subscription_or_api", "local"]


@dataclass(frozen=True, slots=True)
class AltCredential:
    """An alternative credential path for the SAME provider.

    Gemini is the motivating case (2026-06-22 forensic): the identical model is
    reachable either via a Google AI Studio API key (pay-as-you-go) OR a Google
    Cloud Vertex AI service account (a *separate* billing project). A user who
    tops up one account while Jarvis is wired to the other gets a silent
    "credits depleted" failure. The UI surfaces BOTH paths so the choice — and
    its billing — is explicit. ``None`` (the default on ProviderSpec) means the
    provider has a single credential path.
    """

    label: str
    billing: Billing
    credential_help: str
    dashboard_url: str | None = None
    credential_path_hint: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    id: str
    label: str
    tier: Tier
    auth_mode: AuthMode
    secret_keys: tuple[str, ...]
    dashboard_url: str | None
    login_cli: tuple[str, ...] | None = None
    install_hint: str | None = None
    credential_path_hint: str | None = None
    brain_switchable: bool = True
    # Plain-English "which key / subscription, and what it is for", shown under
    # the provider's credential widget so the user does not have to guess.
    credential_help: str | None = None
    # Where to sign up for the account/subscription this provider bills against
    # (distinct from dashboard_url, which is where you GENERATE the key). Mainly
    # useful for the subscription providers; ``None`` for most API providers.
    signup_url: str | None = None
    # Alternative credential path (Gemini AI Studio vs Vertex). ``None`` =
    # single path.
    alt_credential: AltCredential | None = None
    # Maintainer-recommended pick for this tier (UI badge). Set on exactly the
    # provider the maintainer wants users to default to — currently the Gemini
    # brain (best real-world experience, 2026-06-22). This is a presentation
    # hint only: it never gates behavior, never branches a code path on a
    # provider name (AP-21), and only the *brain* tier carries it today.
    recommended: bool = False
    # The specific model the recommendation points at (e.g. ``gemini-3.5-flash``),
    # surfaced as an "empfohlen" marker in the model picker. ``None`` = the badge
    # stands for the provider as a whole with no model preference.
    recommended_model: str | None = None


def provider_billing(spec: ProviderSpec) -> Billing:
    """How using *spec* is billed — capability-driven, never name-branched
    (multi-provider mandate, AP-21).

    * a subscription-login provider (``codex``/``antigravity``) that ALSO carries
      an API-key slot → ``"subscription_or_api"`` (a plan login OR per-token key);
      one with no key slot → ``"subscription"``. The distinction is the presence
      of ``secret_keys``, not the provider name — so adding a key slot to either
      flips it without touching this function.
    * ``none`` → ``"local"`` — no credential, runs on-device.
    * everything else (``api_key``) → ``"api"`` — pay per token on an API account.
    """
    if spec.auth_mode in ("antigravity", "codex"):
        return "subscription_or_api" if spec.secret_keys else "subscription"
    if spec.auth_mode == "none":
        return "local"
    return "api"


# Gemini's alternative credential path: Google Cloud Vertex AI via a
# service-account JSON. A SEPARATE billing project from an AI Studio key — the
# 2026-06-22 forensic was a user topping up AI Studio while Jarvis was wired to
# Vertex. Shared by the Gemini brain + Gemini TTS specs so both surface the
# choice. (Vertex is wired for the TTS path today via [tts].use_vertex.)
_GEMINI_VERTEX = AltCredential(
    label="Vertex AI (service account)",
    billing="api",
    credential_help=(
        "Bill Gemini through a Google Cloud Vertex AI project instead of an AI "
        "Studio key — this is a SEPARATE billing account. Enable the Vertex AI "
        "API, create a service account, download its JSON key, and point Jarvis "
        "at the file. Use this for higher quota than the AI Studio preview cap. "
        "Don't mix them up: topping up AI Studio does nothing if Jarvis is on "
        "Vertex, and vice versa."
    ),
    dashboard_url="https://console.cloud.google.com/iam-admin/serviceaccounts",
    credential_path_hint="~/.config/jarvis/vertex-sa.json",
)


PROVIDERS: tuple[ProviderSpec, ...] = (
    # ── Brain: Klassischer API-Key ────────────────────────────────────────
    ProviderSpec(
        id="claude-api",
        label="Claude (API-Key)",
        tier="brain",
        auth_mode="api_key",
        secret_keys=("anthropic_api_key",),
        dashboard_url="https://console.anthropic.com/settings/keys",
        credential_help=(
            "Anthropic API key (starts with sk-ant-). Billed per token on your "
            "Anthropic account. Powers the Claude brain."
        ),
    ),
    ProviderSpec(
        id="openai",
        label="OpenAI",
        tier="brain",
        auth_mode="api_key",
        secret_keys=("openai_api_key",),
        dashboard_url="https://platform.openai.com/api-keys",
        credential_help=(
            "OpenAI API key (starts with sk-). Billed per token. Shared by the "
            "GPT brain, Whisper STT and OpenAI TTS."
        ),
    ),
    ProviderSpec(
        id="codex",
        label="OpenAI Codex",
        tier="brain",
        auth_mode="codex",
        secret_keys=("codex_openai_api_key",),
        dashboard_url="https://platform.openai.com/api-keys",
        login_cli=("codex", "login"),
        install_hint="npm i -g @openai/codex",
        brain_switchable=False,
        signup_url="https://chatgpt.com",
        credential_help=(
            "Run heavy subagent tasks over your ChatGPT subscription via the "
            "Codex CLI — sign in below, no API key needed. Or paste an OpenAI "
            "API key to bill per token instead."
        ),
    ),
    ProviderSpec(
        id="openrouter",
        label="OpenRouter",
        tier="brain",
        auth_mode="api_key",
        secret_keys=("openrouter_api_key",),
        dashboard_url="https://openrouter.ai/keys",
        credential_help=(
            "OpenRouter API key (starts with sk-or-). One key reaches many "
            "models; billed per token on your OpenRouter account."
        ),
    ),
    # ── Brain: Google subscription via the official Antigravity/Gemini CLI ──
    # OAuth-only (no API-key slot): we drive the official ``agy``/``gemini`` CLI
    # as a subprocess over the user's "Sign in with Google" login — billed
    # against the Google subscription, no Gemini API key. Mirror of the Codex
    # ChatGPT-login path. The UI renders an OAuth connect widget (auth_mode).
    ProviderSpec(
        id="antigravity",
        label="Antigravity (Google subscription)",
        tier="brain",
        auth_mode="antigravity",
        # Dual billing, mirror of Codex: the Google subscription login OR a
        # Gemini API key (the Google Cloud credential). The key slot reuses the
        # shared ``gemini_api_key`` — the same key the Gemini provider uses — so
        # a user who already set it gets per-token Antigravity billing for free.
        # Carrying a secret_key flips provider_billing → subscription_or_api.
        secret_keys=("gemini_api_key",),
        dashboard_url="https://antigravity.google",
        # agy has NO `login` subcommand (verified 2026-06-21) — the bare binary
        # drops into the interactive "Sign in with Google" flow. The Connect
        # button drives POST /api/antigravity/login (start_login → bare run).
        login_cli=("agy",),
        install_hint="Install Antigravity (agy) or sign in with the Gemini CLI",
        credential_path_hint="~/.gemini/oauth_creds.json",
        brain_switchable=False,
        signup_url="https://antigravity.google",
        credential_help=(
            "Sign in with your Google account to run heavy subagent tasks over "
            "your Google subscription via the Antigravity/Gemini CLI — no API "
            "key, billed to your subscription. Or set a Gemini API key to bill "
            "per token instead."
        ),
    ),
    ProviderSpec(
        id="gemini",
        label="Google Gemini",
        tier="brain",
        auth_mode="api_key",
        secret_keys=("gemini_api_key",),
        dashboard_url="https://aistudio.google.com/app/apikey",
        credential_help=(
            "Google AI Studio API key (starts with AIza or AQ.). Pay-as-you-go "
            "on your AI Studio project. For higher quota, use the Vertex AI "
            "service-account path instead — it bills a different account."
        ),
        alt_credential=_GEMINI_VERTEX,
        # Maintainer-recommended brain (2026-06-22): best real-world experience.
        # Badge on the brain card; the model picker highlights gemini-3.5-flash.
        recommended=True,
        recommended_model="gemini-3.5-flash",
    ),
    # xAI Grok was removed as a BRAIN and SUB-AGENT provider on 2026-06-22
    # (maintainer decision: Grok stays only as a TTS voice). The `grok-voice`
    # TTS spec below is intentionally kept, and the `grok_api_key` credential it
    # shares with the (now TTS-only) xAI key remains.
    # Ollama-Provider 2026-04-21 entfernt — reine API-Provider-Chain.
    # ── TTS ───────────────────────────────────────────────────────────────
    ProviderSpec(
        id="gemini-flash-tts",
        label="Gemini Flash TTS",
        tier="tts",
        auth_mode="api_key",
        secret_keys=("gemini_api_key",),
        dashboard_url="https://aistudio.google.com/app/apikey",
        credential_help=(
            "Same Google AI Studio key as the Gemini brain (AIza/AQ.). Note: the "
            "TTS preview model is hard-capped on AI Studio — switch to the Vertex "
            "AI path for production quota."
        ),
        alt_credential=_GEMINI_VERTEX,
    ),
    ProviderSpec(
        id="grok-voice",
        label="xAI Grok Voice (leo/rex/sal/ara/eve)",
        tier="tts",
        auth_mode="api_key",
        secret_keys=("grok_api_key",),
        dashboard_url="https://console.x.ai/",
        credential_help=(
            "xAI API key (starts with xai-) for Grok Voice. Voices: leo, rex, "
            "sal, ara, eve."
        ),
    ),
    ProviderSpec(
        id="cartesia",
        label="Cartesia Sonic 3.5",
        tier="tts",
        auth_mode="api_key",
        secret_keys=("cartesia_api_key",),
        dashboard_url="https://play.cartesia.ai/keys",
        credential_help=(
            "Cartesia API key (starts with sk_car_). Billed per token; "
            "multilingual Sonic voices incl. German."
        ),
    ),
    # ── STT ───────────────────────────────────────────────────────────────
    ProviderSpec(
        id="groq-api",
        label="Groq STT (Whisper)",
        tier="stt",
        auth_mode="api_key",
        secret_keys=("groq_api_key",),
        dashboard_url="https://console.groq.com/keys",
        credential_help=(
            "Groq API key (starts with gsk_). Fast hosted Whisper "
            "speech-to-text, billed per token."
        ),
    ),
    ProviderSpec(
        id="openai-api",
        label="OpenAI Whisper STT",
        tier="stt",
        auth_mode="api_key",
        secret_keys=("openai_api_key",),
        dashboard_url="https://platform.openai.com/api-keys",
        credential_help=(
            "Uses your OpenAI API key (shared with the GPT brain) for Whisper "
            "speech-to-text."
        ),
    ),
    ProviderSpec(
        id="faster-whisper",
        label="Faster-Whisper (lokal)",
        tier="stt",
        auth_mode="none",
        secret_keys=(),
        dashboard_url=None,
        credential_help=(
            "Runs locally on your machine — no API key, no account. Needs the "
            "[desktop] extra and a one-time model download."
        ),
    ),
)


def get_spec(provider_id: str) -> ProviderSpec | None:
    """Look up a spec by ID. None if unknown."""
    for spec in PROVIDERS:
        if spec.id == provider_id:
            return spec
    return None


def all_secret_keys() -> set[str]:
    """Set of all secret keys referenced by the declared provider specs."""
    return {key for spec in PROVIDERS for key in spec.secret_keys}
