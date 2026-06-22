"""Deklarative Beschreibung aller Brain/TTS/STT-Provider für die Desktop-App.

Single-Source-of-Truth für die UI: welche Provider gibt es, welches Auth-Verfahren
brauchen sie, welcher Credential-Manager-Slot speichert ihren Key, welche Login-CLI
muss gespawnt werden? Ganz bewusst KEINE Modellnamen — Modelle wechseln zu oft,
und der Default kommt aus jarvis.toml. Die UI rendert pro Provider ein generisches
Widget anhand des auth_mode.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AuthMode = Literal["api_key", "codex", "antigravity", "none"]
Tier = Literal["brain", "tts", "stt"]


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


PROVIDERS: tuple[ProviderSpec, ...] = (
    # ── Brain: Subscription via externes CLI ──────────────────────────────
    # Direkt gegen Anthropic-API — OAuth-Token aus ~/.claude/.credentials.json
    # ODER manuell gepasteter anthropic_api_key. KEIN CLI-Spawn, kein Harness-
    # Roundtrip: reine POST-Requests wie bei jedem anderen LLM-Provider. Deshalb
    # `oauth_or_key` statt `api_key` — das Frontend rendert dafür zwei
    # getrennte Auth-Pfade (OAuth-Import-Button + API-Key-Formular).
    # ── Brain: Klassischer API-Key ────────────────────────────────────────
    ProviderSpec(
        id="claude-api",
        label="Claude (API-Key)",
        tier="brain",
        auth_mode="api_key",
        secret_keys=("anthropic_api_key",),
        dashboard_url="https://console.anthropic.com/settings/keys",
    ),
    ProviderSpec(
        id="openai",
        label="OpenAI",
        tier="brain",
        auth_mode="api_key",
        secret_keys=("openai_api_key",),
        dashboard_url="https://platform.openai.com/api-keys",
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
    ),
    ProviderSpec(
        id="openrouter",
        label="OpenRouter",
        tier="brain",
        auth_mode="api_key",
        secret_keys=("openrouter_api_key",),
        dashboard_url="https://openrouter.ai/keys",
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
        secret_keys=(),
        dashboard_url="https://antigravity.google",
        # agy has NO `login` subcommand (verified 2026-06-21) — the bare binary
        # drops into the interactive "Sign in with Google" flow. The Connect
        # button drives POST /api/antigravity/login (start_login → bare run).
        login_cli=("agy",),
        install_hint="Install Antigravity (agy) or sign in with the Gemini CLI",
        credential_path_hint="~/.gemini/oauth_creds.json",
        brain_switchable=False,
    ),
    ProviderSpec(
        id="gemini",
        label="Google Gemini",
        tier="brain",
        auth_mode="api_key",
        secret_keys=("gemini_api_key",),
        dashboard_url="https://aistudio.google.com/app/apikey",
    ),
    ProviderSpec(
        id="grok",
        label="xAI Grok",
        tier="brain",
        auth_mode="api_key",
        secret_keys=("grok_api_key",),
        dashboard_url="https://console.x.ai/",
    ),
    # Ollama-Provider 2026-04-21 entfernt — reine API-Provider-Chain.
    # ── TTS ───────────────────────────────────────────────────────────────
    ProviderSpec(
        id="gemini-flash-tts",
        label="Gemini Flash TTS",
        tier="tts",
        auth_mode="api_key",
        secret_keys=("gemini_api_key",),
        dashboard_url="https://aistudio.google.com/app/apikey",
    ),
    ProviderSpec(
        id="google-neural2",
        label="Google Cloud TTS (Neural2)",
        tier="tts",
        auth_mode="api_key",
        secret_keys=("google_tts_credentials_path",),
        dashboard_url="https://console.cloud.google.com/apis/credentials",
    ),
    ProviderSpec(
        id="openai-tts",
        label="OpenAI TTS",
        tier="tts",
        auth_mode="api_key",
        secret_keys=("openai_api_key",),
        dashboard_url="https://platform.openai.com/api-keys",
    ),
    ProviderSpec(
        id="grok-voice",
        label="xAI Grok Voice (leo/rex/sal/ara/eve)",
        tier="tts",
        auth_mode="api_key",
        secret_keys=("grok_api_key",),
        dashboard_url="https://console.x.ai/",
    ),
    ProviderSpec(
        id="cartesia",
        label="Cartesia Sonic 3.5",
        tier="tts",
        auth_mode="api_key",
        secret_keys=("cartesia_api_key",),
        dashboard_url="https://play.cartesia.ai/keys",
    ),
    # ── STT ───────────────────────────────────────────────────────────────
    ProviderSpec(
        id="deepgram",
        label="Deepgram STT",
        tier="stt",
        auth_mode="api_key",
        secret_keys=("deepgram_api_key",),
        dashboard_url="https://console.deepgram.com/",
    ),
    ProviderSpec(
        id="groq-api",
        label="Groq STT (Whisper)",
        tier="stt",
        auth_mode="api_key",
        secret_keys=("groq_api_key",),
        dashboard_url="https://console.groq.com/keys",
    ),
    ProviderSpec(
        id="openai-api",
        label="OpenAI Whisper STT",
        tier="stt",
        auth_mode="api_key",
        secret_keys=("openai_api_key",),
        dashboard_url="https://platform.openai.com/api-keys",
    ),
    ProviderSpec(
        id="faster-whisper",
        label="Faster-Whisper (lokal)",
        tier="stt",
        auth_mode="none",
        secret_keys=(),
        dashboard_url=None,
    ),
)


def get_spec(provider_id: str) -> ProviderSpec | None:
    """Lookup eines Specs anhand der ID. None wenn unbekannt."""
    for spec in PROVIDERS:
        if spec.id == provider_id:
            return spec
    return None


def all_secret_keys() -> set[str]:
    """Set aller Secret-Keys, die von den deklarierten Provider-Specs referenziert werden."""
    return {key for spec in PROVIDERS for key in spec.secret_keys}
