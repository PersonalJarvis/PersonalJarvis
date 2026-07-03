"""First-run setup wizard (CLI).

The wizard runs once on the very first `python -m jarvis`. It:
1. Shows hardware analysis + Whisper recommendation.
2. Asks for API keys and stores them in the Windows Credential Manager.
3. Checks microphone availability.
4. Confirms the hotkey choice.
5. Writes the `.setup-complete` marker.

The wizard is idempotent — a re-run only overwrites user-confirmed values.

Headless / non-interactive mode
--------------------------------
On a VPS or CI host where no TTY is attached, the interactive prompts cannot be
answered.  Jarvis detects this automatically (``sys.stdin.isatty()`` is False)
or you can force non-interactive mode explicitly:

    JARVIS_NONINTERACTIVE=1 python -m jarvis

In non-interactive mode the wizard skips ``step_api_keys()`` entirely and
writes the ``.setup-complete`` marker so that subsequent runs go straight to
the app.  API keys must be provided before boot via one of these two paths
(both are already honoured by ``jarvis.core.config.get_secret()``):

1. **Environment variables** (preferred for containers / systemd units)::

       export GEMINI_API_KEY=...
       export OPENAI_API_KEY=...   # whichever provider you use
       python -m jarvis.ui.web.launcher --headless

2. **``.env`` file** at the repo root (dev convenience)::

       cp .env.example .env
       # edit .env and fill in your keys
       python -m jarvis.ui.web.launcher --headless

The full list of recognised ENV variable names mirrors the ``env_fallback``
field in the ``SECRETS`` list defined in this module (e.g. ``GEMINI_API_KEY``,
``ANTHROPIC_API_KEY``, ``GROQ_API_KEY``, etc.).  See ``.env.example`` for the
complete reference.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from rich.console import Console
from rich.markup import escape
from rich.padding import Padding
from rich.panel import Panel
from rich.theme import Theme

from jarvis.core import config as cfg
from jarvis.hardware import detection

# Brand palette (Charcoal + Gold) — identical to install/installer.py so the
# first-run wizard reads as one continuous, on-brand experience with the
# installer that launched it. Rich auto-strips color on a non-TTY (headless
# VPS, CI, piped/captured output), so the same calls degrade to clean plain
# text there — no separate code path needed.
_THEME = Theme(
    {
        "brand": "#e7c46e",
        "brand.bold": "bold #e7c46e",
        "ok": "#7ac88c",
        "muted": "#8c8c8c",
        "bad": "#e07a6e",
    }
)
_console = Console(theme=_THEME, highlight=False)


# ---------------------------------------------------------------------------
# Non-interactive detection
# ---------------------------------------------------------------------------

def _is_noninteractive() -> bool:
    """Return True when the wizard should skip all interactive prompts.

    Triggered by either of:
    * ``JARVIS_NONINTERACTIVE=1`` env var (explicit opt-in; useful in scripts
      that need to override the TTY check).
    * ``sys.stdin.isatty()`` returning False (no TTY attached — VPS, Docker,
      CI, ``python -m jarvis < /dev/null``).
    """
    if os.environ.get("JARVIS_NONINTERACTIVE", "").strip() == "1":
        return True
    try:
        return not sys.stdin.isatty()
    except AttributeError:
        # Fallback for exotic stdin replacements that lack isatty().
        return True


@dataclass(slots=True, frozen=True)
class SecretSpec:
    key: str              # Name in the Credential Manager
    env_fallback: str     # ENV variable as an alternative
    label: str            # Display name
    help_url: str         # Where to get the key
    required_for: str     # Human-readable: "Brain (Claude)" etc.
    optional: bool = True
    # When False, the slot is whitelisted for the API (so it CAN be stored from
    # the app) but is NOT asked in the interactive first-run wizard. Used for
    # advanced, app-only secrets (e.g. per-provider BYO OAuth client ids) so they
    # don't lengthen onboarding.
    prompt: bool = True
    # Which onboarding SECTION this key belongs to (see ``_SECTIONS``). The wizard
    # groups prompted keys under their section so the user immediately sees "one
    # brain, one voice, one STT — I only need ONE per group, not all of them".
    # Non-prompted / advanced slots keep the default and are never rendered.
    section: str = "other"


SECRETS: list[SecretSpec] = [
    # Brain providers that SIMULTANEOUSLY enable the Jarvis-Agent worker harness.
    # The harness reads the standard provider ENV vars (see the AD-6 mapping in
    # docs/jarvis-agents-bridge.md §2). There is NO separate per-harness namespace
    # — the wizard maintains one key, the harness uses it on subprocess spawn.
    # Full mapping table: jarvis/missions/worker_runtime/provider_map.py.
    SecretSpec(
        key="anthropic_api_key",
        env_fallback="ANTHROPIC_API_KEY",
        label="Anthropic API Key (Claude)",
        help_url="https://console.anthropic.com/settings/keys",
        required_for="Brain (Claude via API key) + Jarvis-Agent harness (anthropic provider)",
        optional=True,
        section="brain",
    ),
    SecretSpec(
        key="openrouter_api_key",
        env_fallback="OPENROUTER_API_KEY",
        label="OpenRouter API Key (universal gateway)",
        help_url="https://openrouter.ai/keys",
        required_for="Brain (universal: access to all models via one key) + Jarvis-Agent harness (openrouter provider)",
        section="brain",
    ),
    SecretSpec(
        key="openai_api_key",
        env_fallback="OPENAI_API_KEY",
        label="OpenAI API Key",
        help_url="https://platform.openai.com/api-keys",
        required_for="Brain (GPT), Whisper API (STT), TTS + Jarvis-Agent harness (openai provider)",
        section="brain",
    ),
    SecretSpec(
        key="codex_openai_api_key",
        env_fallback="CODEX_OPENAI_API_KEY",
        label="OpenAI Codex API Key",
        help_url="https://platform.openai.com/api-keys",
        required_for="OpenAI Codex API-key mode (separate from the OpenAI Brain provider)",
        section="brain",
    ),
    SecretSpec(
        key="gemini_api_key",
        env_fallback="GEMINI_API_KEY",
        label="Google AI Studio / Gemini API Key",
        help_url="https://aistudio.google.com/app/apikey",
        required_for="Brain (Gemini) + Jarvis-Agent harness (google provider)",
        section="brain",
    ),
    SecretSpec(
        key="grok_api_key",
        env_fallback="GROK_API_KEY",
        label="xAI Grok Voice API Key (TTS)",
        help_url="https://console.x.ai/",
        required_for="TTS (Grok Voice — leo/rex/sal/ara/eve)",
        section="tts",
    ),
    SecretSpec(
        key="google_tts_credentials_path",
        env_fallback="GOOGLE_APPLICATION_CREDENTIALS",
        label="Path to the Google Cloud service-account JSON (for TTS)",
        help_url="https://console.cloud.google.com/apis/credentials",
        required_for="TTS (Google Neural2 — high-quality voice output)",
        section="tts",
    ),
    SecretSpec(
        key="deepgram_api_key",
        env_fallback="DEEPGRAM_API_KEY",
        label="Deepgram API Key (fast STT)",
        help_url="https://console.deepgram.com/",
        required_for="STT (Deepgram — cloud alternative to Whisper)",
        section="stt",
    ),
    SecretSpec(
        key="groq_api_key",
        env_fallback="GROQ_API_KEY",
        label="Groq API Key (ultra-fast Whisper)",
        help_url="https://console.groq.com/keys",
        required_for="STT (Groq Whisper — <50ms latency)",
        section="stt",
    ),
    SecretSpec(
        key="picovoice_access_key",
        env_fallback="PICOVOICE_ACCESS_KEY",
        label="Picovoice Access Key (Porcupine wake word)",
        help_url="https://console.picovoice.ai/",
        required_for="Wake-word detection (Porcupine)",
        section="wake",
    ),
    SecretSpec(
        key="tavily_api_key",
        env_fallback="TAVILY_API_KEY",
        label="Tavily API Key (web search for agents)",
        help_url="https://app.tavily.com/home",
        required_for="Tool (search_web)",
        section="tools",
    ),
    SecretSpec(
        key="elevenlabs_api_key",
        env_fallback="ELEVENLABS_API_KEY",
        label="ElevenLabs API Key (premium TTS, multi-language)",
        help_url="https://elevenlabs.io/app/settings/api-keys",
        required_for="TTS (ElevenLabs — mature British voice with DE+EN auto-detect)",
        section="tts",
    ),
    SecretSpec(
        key="cartesia_api_key",
        env_fallback="CARTESIA_API_KEY",
        label="Cartesia.ai API Key (Sonic 3.5 TTS, 42 languages)",
        help_url="https://play.cartesia.ai/keys",
        required_for="TTS (Cartesia Sonic 3.5 — multilingual incl. German, ~90ms TTFB)",
        section="tts",
    ),
    # Team / hosted-proxy mode (2026-06-20 spec). The per-user token a client
    # presents to the shared key proxy instead of holding a real vendor key.
    # The proxy URL itself lives (non-secret) in [team_proxy].url; only this
    # token is a secret. Optional — unset means the local per-machine key model.
    SecretSpec(
        key="team_proxy_token",
        env_fallback="TEAM_PROXY_TOKEN",
        label="Team Proxy Token (shared key proxy)",
        help_url="",
        required_for="Team mode — per-user token for the shared key proxy",
        optional=True,
        section="team",
    ),
    # Phase 5 — admin-helper HMAC key. NOT asked interactively:
    # on the helper's first start, `jarvis.admin.launcher` generates 32
    # random bytes and persists them base64-URL-safe-encoded in the
    # Credential Manager. The wizard only lists the entry so it is
    # visible in the secrets overview (a re-run shows "already stored").
    SecretSpec(
        key="jarvis_admin_hmac",
        env_fallback="JARVIS_ADMIN_HMAC",
        label="Admin-Helper HMAC Key (auto-generated)",
        help_url="",
        required_for="Phase 5 — admin ops (winget, services, registry, firewall)",
        optional=True,
        # Auto-generated on the admin helper's first start (32 random bytes) and
        # persisted by ``jarvis.admin.launcher``. Asking the user to *type* an
        # auto-generated key made no sense, so it is whitelisted for the API but
        # never prompted — the secrets overview still shows "already stored".
        prompt=False,
    ),
    # === F-FRIENDS [F1] · feature/friends-section · alex-2026-04-30 ===
    # Phase F1 — Telegram-channel bot token. The user creates a bot via
    # @BotFather, gets a token like ``123456:ABC-DEF...``, and enters it
    # here. ``getMe`` validation happens in TelegramChannel.start().
    SecretSpec(
        key="telegram_bot_token",
        env_fallback="TELEGRAM_BOT_TOKEN",
        label="Telegram Bot Token (@BotFather)",
        help_url="https://t.me/BotFather",
        required_for="Channel (Telegram) — two-way chat with friends",
        optional=True,
        section="channels",
    ),
    # Twilio telephony — the user calls a phone number and talks to Jarvis
    # over Twilio Media Streams (raw audio), reusing Jarvis's own STT/Brain/TTS
    # and the same Charon voice. Only the Auth Token is a secret; the Account
    # SID + phone number live in [integrations.twilio]. The token is validated
    # against the Twilio REST API by POST /api/telephony/test.
    SecretSpec(
        key="twilio_auth_token",
        env_fallback="TWILIO_AUTH_TOKEN",
        label="Twilio Auth Token",
        help_url="https://console.twilio.com",
        required_for="Telephony (call Jarvis on a Twilio phone number)",
        optional=True,
        section="telephony",
    ),
    # === Bring-your-own OAuth client (marketplace plugins) ===
    # A downloader can run their OWN production OAuth app instead of the shipped
    # catalog placeholder. This is the ONLY durable fix for provider-side
    # refresh-token expiry: a Google OAuth app left in "Testing" status drops its
    # refresh token after 7 days; publishing one's own app to production stops the
    # clock. Resolved by `marketplace.connect_helpers.resolve_pkce_client` via the
    # `<family>_oauth_client_*` keys. prompt=False: advanced + entered ONLY from
    # the Plugins UI, never asked in the first-run wizard. The Google family
    # (gmail/drive/calendar) shares ONE client pair.
    SecretSpec(
        key="google_oauth_client_id",
        env_fallback="GOOGLE_OAUTH_CLIENT_ID",
        label="Google OAuth Client ID (Gmail / Drive / Calendar)",
        help_url="https://console.cloud.google.com/auth/clients",
        required_for="Marketplace plugins (Google family) — your own OAuth client",
        optional=True,
        prompt=False,
    ),
    SecretSpec(
        key="google_oauth_client_secret",
        env_fallback="GOOGLE_OAUTH_CLIENT_SECRET",
        label="Google OAuth Client Secret (Gmail / Drive / Calendar)",
        help_url="https://console.cloud.google.com/auth/clients",
        required_for="Marketplace plugins (Google family) — your own OAuth client",
        optional=True,
        prompt=False,
    ),
    SecretSpec(
        key="slack_oauth_client_id",
        env_fallback="SLACK_OAUTH_CLIENT_ID",
        label="Slack OAuth Client ID",
        help_url="https://api.slack.com/apps",
        required_for="Marketplace plugin (Slack) — your own OAuth client",
        optional=True,
        prompt=False,
    ),
    SecretSpec(
        key="slack_oauth_client_secret",
        env_fallback="SLACK_OAUTH_CLIENT_SECRET",
        label="Slack OAuth Client Secret",
        help_url="https://api.slack.com/apps",
        required_for="Marketplace plugin (Slack) — your own OAuth client",
        optional=True,
        prompt=False,
    ),
    SecretSpec(
        key="asana_oauth_client_id",
        env_fallback="ASANA_OAUTH_CLIENT_ID",
        label="Asana OAuth Client ID",
        help_url="https://app.asana.com/0/my-apps",
        required_for="Marketplace plugin (Asana) — your own OAuth client",
        optional=True,
        prompt=False,
    ),
    SecretSpec(
        key="asana_oauth_client_secret",
        env_fallback="ASANA_OAUTH_CLIENT_SECRET",
        label="Asana OAuth Client Secret",
        help_url="https://app.asana.com/0/my-apps",
        required_for="Marketplace plugin (Asana) — your own OAuth client",
        optional=True,
        prompt=False,
    ),
]


@dataclass(slots=True, frozen=True)
class _Section:
    """A user-facing group of API keys in the first-run wizard.

    The whole point of grouping is honesty about choice: the user sees "voices",
    "brains", "speech-to-text" as *buckets* and immediately understands they pick
    at most ONE provider per bucket — not the whole list. ``pick_one`` drives the
    "you only need ONE of these" hint; ``essential`` marks the single bucket
    Jarvis truly needs to function (the brain).
    """

    id: str
    title: str
    blurb: str        # one plain-English line: what this bucket is for
    pick_one: bool = True
    essential: bool = False


# Ordered most-important-first. Only sections that actually have a prompted key
# are rendered (see ``step_api_keys``), so adding/removing a SecretSpec section
# automatically shows/hides the group.
_SECTIONS: tuple[_Section, ...] = (
    _Section(
        id="brain",
        title="Brain — the AI that thinks",
        blurb=(
            "The ONE thing Jarvis needs to work. Pick a single provider you "
            "already have. Tip: OpenRouter gives you almost every model with one key."
        ),
        essential=True,
    ),
    _Section(
        id="stt",
        title="Speech-to-text — understanding your voice",
        blurb=(
            "Optional. Jarvis can also transcribe locally & offline. Add one cloud "
            "key only if you want faster/other transcription. (OpenAI Whisper uses "
            "the OpenAI key from the Brain group.)"
        ),
    ),
    _Section(
        id="tts",
        title="Voice — how Jarvis speaks back",
        blurb=(
            "Optional. Pick one for a natural cloud voice; without any, a basic "
            "local/system voice is used."
        ),
    ),
    _Section(
        id="wake",
        title="Wake word",
        blurb="Optional. Only needed for the Porcupine wake engine.",
    ),
    _Section(
        id="tools",
        title="Web search & tools",
        blurb="Optional. Lets Jarvis and its agents search the live web.",
    ),
    _Section(
        id="channels",
        title="Messaging channels",
        blurb="Optional. Chat with Jarvis from Telegram.",
    ),
    _Section(
        id="telephony",
        title="Phone calls",
        blurb="Optional. Call Jarvis on a Twilio phone number.",
    ),
    _Section(
        id="team",
        title="Team / shared-key proxy",
        blurb="Advanced & optional. Only for a shared team key proxy.",
        pick_one=False,
    ),
)


def _println(msg: str = "") -> None:
    print(msg)


def _ask(prompt: str, default: str | None = None) -> str:
    if default:
        line = input(f"{prompt} [{default}]: ").strip()
        return line or default
    return input(f"{prompt}: ").strip()


def _ask_yesno(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    ans = input(f"{prompt} [{d}]: ").strip().lower()
    if not ans:
        return default
    return ans in ("y", "yes", "j", "ja")


# ----------------------------------------------------------------------
# Steps
# ----------------------------------------------------------------------

def step_hardware_check() -> detection.HardwareReport:
    _println()
    _println("=" * 60)
    _println(" Step 1 / 8 — Hardware analysis")
    _println("=" * 60)
    report = detection.analyze()
    rec = detection.recommend_whisper(report)
    _println(detection._format_report(report, rec))

    if report.ffmpeg_version is None:
        _println("⚠  WARNING: ffmpeg was not found. Whisper STT will not work.")
        _println("   Install: https://www.gyan.dev/ffmpeg/builds/ (then add ffmpeg to PATH)")
    if not report.torch_cuda_available and report.has_nvidia_gpu:
        _println("⚠  Note: NVIDIA GPU detected but PyTorch CUDA is not active.")
        _println("   Install PyTorch with CUDA for local Whisper acceleration.")

    return report


def _secret_store_location() -> str:
    """Plain-English name for where a saved key lands on this OS.

    The old copy always said "Windows Credential Manager", which was wrong (and
    slightly alarming) on macOS/Linux. ``get_secret`` resolves keyring → ENV →
    .env → local file, so we describe the general guarantee, not a Windows-only
    store."""
    if sys.platform == "win32":
        return "Windows Credential Manager"
    if sys.platform == "darwin":
        return "macOS Keychain"
    return "your OS keyring (or an encrypted local fallback)"


def _api_keys_intro() -> None:
    """The framing that makes onboarding calm: everything here is skippable and
    you only ever need one provider per group."""
    _console.print()
    _console.print(" [brand.bold]Step 2 / 8 — API keys[/]  [muted](all optional)[/]")
    body = (
        "[ok]Nothing here is required right now.[/] Press [brand]Enter[/] to skip any "
        "field — or every field — and add keys later in the app under "
        "[brand]Settings → API Keys[/].\n\n"
        "You only need [brand.bold]ONE provider per group[/] (one brain, one voice, "
        "one speech-to-text …) — never the whole list. The only group Jarvis truly "
        "needs to think is the [brand.bold]Brain[/].\n\n"
        f"[muted]Whatever you enter is stored securely in {_secret_store_location()} — "
        "never in the code or a plain config file.[/]"
    )
    _console.print(Panel(body, border_style="brand", padding=(1, 2)))


def step_api_keys() -> dict[str, str]:
    _api_keys_intro()

    stored: dict[str, str] = {}
    for section in _SECTIONS:
        specs = [s for s in SECRETS if s.prompt and s.section == section.id]
        if not specs:
            continue

        # Section header: title + one plain line + the "pick one" nudge. Wrapped
        # sub-lines hang-indent (Padding left=2) so a long blurb stays aligned
        # under the title instead of falling back to the left margin.
        _console.print()
        tag = "[bad]needed[/]" if section.essential else "[muted]optional[/]"
        _console.print(f"[brand.bold]▸ {escape(section.title)}[/]  ({tag})")
        _console.print(Padding(f"[muted]{escape(section.blurb)}[/]", (0, 0, 0, 2)))
        if section.pick_one and len(specs) > 1:
            _console.print(
                Padding("[muted]You only need ONE of the following.[/]", (0, 0, 0, 2))
            )
        _console.print()

        for spec in specs:
            existing = cfg.get_secret(spec.key)
            marker = "[ok]✓ already set[/]" if existing else "[muted]— not set[/]"
            _console.print(f"  [brand]•[/] {escape(spec.label)}   {marker}")
            if spec.help_url:
                _console.print(f"    [muted]Get a key:[/] {escape(spec.help_url)}")
            val = _ask("    Key/path (Enter = skip)", default="")
            if val:
                if cfg.set_secret(spec.key, val):
                    stored[spec.key] = val
                    _console.print("    [ok]→ saved.[/]")
                else:
                    _console.print(
                        "    [bad]⚠ keyring unavailable — saved to the .env fallback.[/]"
                    )
            _console.print()

    # Closing reassurance — the single most important line for a nervous first-timer.
    if stored:
        _console.print(f"  [ok]Saved {len(stored)} key(s).[/] "
                       "[muted]Change or add more any time in Settings → API Keys.[/]")
    else:
        _console.print("  [muted]No keys entered — that's fine. You can add them "
                       "any time in the app under Settings → API Keys.[/]")
    _console.print()
    return stored


def step_mic_check() -> None:
    _println()
    _println("=" * 60)
    _println(" Step 3 / 8 — Microphone check")
    _println("=" * 60)
    try:
        import sounddevice as sd  # type: ignore[import-untyped]

        devices = sd.query_devices()
    except ImportError:
        _println("⚠  sounddevice not installed. Run `pip install -r requirements.txt`.")
        return
    except Exception as exc:  # noqa: BLE001 — no audio backend must not kill setup
        # sd.query_devices() raises (e.g. PortAudioError "library not found") on a
        # host with no audio backend: a headless server, or a Linux desktop without
        # libportaudio2. Degrade gracefully — a mic-check failure must NEVER abort
        # the wizard, or step_finalize never writes the .setup-complete marker and
        # the app re-runs its whole onboarding (same failure mode as the step-7 bug).
        _println(f"⚠  Audio system unavailable ({exc}).")
        _println("   Skipping the microphone check — setup continues.")
        _println("   Headless server: expected. Linux desktop: install libportaudio2.")
        return

    inputs = [d for d in devices if d["max_input_channels"] > 0]
    if not inputs:
        _println("⚠  No microphone detected. Plug in a headset and restart.")
        return

    _println("Available input devices:")
    for idx, dev in enumerate(inputs):
        _println(f"  [{idx}] {dev['name']}  (Channels: {dev['max_input_channels']})")
    _println()
    _println("Default is 'auto-headset' — Jarvis detects headsets automatically.")
    _println("Manual selection is always possible via jarvis.toml → [audio] input_device.")


def step_hotkey_check(default_hotkey: str) -> str:
    _println()
    _println("=" * 60)
    _println(" Step 4 / 8 — Hotkey configuration")
    _println("=" * 60)
    _println(f"Current default: {default_hotkey}")
    _println("Safe combinations: ctrl+right_alt+<letter>, ctrl+shift+<letter>")
    _println("Avoid: alt+f4 (closes apps), ctrl+c (copy), win+* (Windows shortcuts)")
    choice = _ask("Customize the hotkey? (empty = keep the default)", default=default_hotkey)
    return choice


def step_wake_word_setup() -> str:
    """Step 5 / 8 — let the user pick their spoken wake word.

    English copy by mandate (Output Language Policy): every artifact this repo
    produces is English; only the user-facing chat/voice reply stays bilingual.

    The choice is persisted best-effort via
    ``config_writer.set_wake_word(phrase, engine="auto")`` so the voice pipeline
    resolves the right wake engine on the next start. ``engine="auto"`` lets
    ``resolve_wake_plan()`` decide between the instant pretrained models and the
    local-Whisper text-match path (see docs/local-wakeword/). A failed write is
    a printed warning, never a crash — the wizard always reaches the finish.
    """
    from jarvis.speech.wake_constants import (
        DEFAULT_WAKE_PHRASE,
        INSTANT_WAKE_PHRASES,
    )

    _println()
    _println("=" * 60)
    _println(" Step 5 / 8 — Wake word")
    _println("=" * 60)
    _println("Choose the spoken phrase that wakes your assistant. There is no")
    _println("preset — you type your own (e.g. \"Jonas\").")
    _println()
    if INSTANT_WAKE_PHRASES:
        _println("These phrases work instantly and fully offline — no GPU, no")
        _println("download, lowest latency (pretrained on-device models):")
        for phrase in INSTANT_WAKE_PHRASES:
            _println(f"  • {phrase}")
        _println()
    _println("A phrase the offline models don't cover (e.g. \"Computer\", \"Athena\")")
    _println("needs the optional local-Whisper extra (install via")
    _println("`pip install -e \".[desktop]\"`). Without it, your assistant falls back")
    _println("to the bundled offline wake word and tells you why — it never pretends")
    _println("a phrase works when it cannot detect it.")
    _println()
    _println("Engine is set to \"auto\": it picks an instant pretrained model when")
    _println("your phrase matches one, otherwise the Whisper path.")
    _println()

    phrase = _ask("Your wake phrase", default=DEFAULT_WAKE_PHRASE)

    try:
        from jarvis.core import config_writer

        config_writer.set_wake_word(phrase, engine="auto")
        _println(f"→ Wake word saved: \"{phrase}\" (engine: auto).")
        if phrase not in INSTANT_WAKE_PHRASES:
            _println("   Note: this is a custom phrase — it needs the local-Whisper")
            _println("   extra at runtime, otherwise it degrades to the bundled")
            _println("   offline wake word.")
        _println("   Takes effect after the next Jarvis restart.")
    except Exception as exc:  # noqa: BLE001
        _println(f"⚠  Could not persist the wake word: {exc}")
        _println("   You can set it later in the desktop Settings UI or in")
        _println("   jarvis.toml under [trigger.wake_word].")

    return phrase


def step_dependency_check() -> None:
    """External CLI dependencies — node / npm / claude / openclaw.

    Welle 3 (2026-05-17): before this step the wizard only *announced*
    that the user should run ``npm i -g openclaw``. Today the worker
    and critic both go through ``claude --print`` via the user's Claude
    Max OAuth (BUG-023 + CRIT-1 fixes); ``claude`` is the new
    mandatory dependency and the wizard now auto-installs it when
    safe. ``node`` stays manual (UAC required); ``openclaw`` is
    optional since the default path bypasses it.
    """
    from jarvis.setup import dependencies as deps

    _println()
    _println("=" * 60)
    _println(" Step 6 / 8 — External CLI dependencies")
    _println("=" * 60)
    _println()

    # 1. Node + npm — prerequisite for every npm-packaged tool.
    node = deps.check_node()
    npm = deps.check_npm()
    if node.present:
        _println(f"✓ node {node.version}")
    else:
        _println(f"–  node missing. {node.install_hint}")
    if npm.present:
        _println(f"✓ npm {npm.version}")
    else:
        _println(f"–  npm missing. {npm.install_hint}")

    # 2. claude CLI — the canonical worker/critic backend since
    #    BUG-023 + CRIT-1. Auto-install if missing AND npm is usable.
    claude = deps.check_claude_cli()
    if claude.present:
        _println(f"✓ claude {claude.version} ({claude.path})")
    elif not npm.present:
        _println("–  claude missing — npm must be available first. Install manually:")
        _println("   npm i -g @anthropic-ai/claude-code")
    else:
        _println("–  claude CLI missing — installing via npm (non-destructive)...")
        ok, claude_after = deps.install_claude_cli()
        if ok:
            _println(f"✓ claude {claude_after.version} installed ({claude_after.path})")
        else:
            _println(f"✗ Auto-install failed: {claude_after.install_hint}")
            _println("   Please install manually: npm i -g @anthropic-ai/claude-code")

    # 3. openclaw — explicitly optional now.
    openclaw = deps.check_openclaw()
    if openclaw.present:
        _println(f"✓ openclaw {openclaw.version} ({openclaw.path})")
    else:
        _println("–  openclaw missing (optional).")
        _println(f"   {openclaw.install_hint}")

    _println()
    # End-of-step summary so a human eyeballing the wizard knows what
    # state the worker path is in.
    if claude.present or (not claude.present and npm.present):
        _println(
            "Worker/critic path: claude CLI (OAuth via Claude Max) — "
            "preferred since Welle-4 + CRIT-1."
        )
    else:
        _println(
            "⚠  Worker path NOT ready. Voice missions will fail with "
            "'claude binary not found' until claude is installed."
        )


def step_jarvis_agent_harness_check() -> None:
    """External Jarvis-Agent worker-harness status — informational, not a key-entry step.

    Contract (docs/jarvis-agents-bridge.md §4.3, Amendment 2026-05-09): NO new
    per-harness secrets are created in the Credential Manager. The worker
    harness uses the standard provider ENV vars (``GEMINI_API_KEY``,
    ``ANTHROPIC_API_KEY``, ...). This step only shows the user:

    1. Whether the optional external ``openclaw`` binary is on PATH
       (``npm i -g openclaw`` — the npm package keeps that name).
    2. Which Personal-Jarvis brain providers are thereby Jarvis-Agent-capable
       (= which of the API keys entered above also enable the harness).
    3. A pointer to the ``Personal-Jarvis slug → worker-harness slug`` mapping.
    """
    import shutil

    _println()
    _println("=" * 60)
    _println(" Step 7 / 8 — Jarvis-Agent worker harness (optional, heavy tasks)")
    _println("=" * 60)
    _println()
    _println("Jarvis-Agents handle complex multi-step tasks for you")
    _println("('read this repo + build X', 'reproduce the bug + propose a fix').")
    _println("Personal Jarvis dispatches via the 'spawn_worker' tool to a short-lived")
    _println("worker subprocess; the LLM output only lands in the voice path after")
    _println("the Controller signature (see docs/jarvis-agents-bridge.md §3 architecture diagram).")
    _println()
    _println("One optional external harness is the 'openclaw' npm binary (that is the")
    _println("package's own name); the default worker path uses the claude CLI instead.")
    _println()

    # 1. Binary check (B-7 finding: .cmd/.ps1 wrappers on Windows count too).
    binary = shutil.which("openclaw")
    if not binary:
        for ext in (".cmd", ".ps1", ".exe"):
            binary = shutil.which("openclaw" + ext)
            if binary:
                break

    if binary:
        _println(f"✓ Worker-harness binary found: {binary}")
    else:
        _println("–  'openclaw' harness binary not on PATH (optional).")
        _println("   Install: npm i -g openclaw   (pin: 2026.5.7, see AD-21)")
        _println("   The harness stays inactive until the binary is available — no crash.")

    # 2. Show the provider mapping (lazy import — the wizard should also run
    #    without the fully installed modules).
    _println()
    _println("Provider mapping (Personal-Jarvis → worker-harness CLI):")
    try:
        from jarvis.missions.worker_runtime.provider_map import MAPPINGS
    except Exception:  # noqa: BLE001
        _println("   (Provider map not loaded — skipping the mapping display.)")
        return

    secret_key_overrides = {
        "claude-api": "anthropic_api_key",
        "openrouter": "openrouter_api_key",
        "openai": "openai_api_key",
        "gemini": "gemini_api_key",
    }
    for mapping in MAPPINGS:
        secret_key = secret_key_overrides.get(
            mapping.jarvis, f"{mapping.jarvis}_api_key"
        )
        has_key = bool(cfg.get_secret(secret_key))
        marker = "✓" if has_key else "–"
        envs = " / ".join(
            v for v in (mapping.env_var, mapping.env_fallback) if v
        )
        _println(
            f"   {marker} {mapping.jarvis:<11} → {mapping.worker_slug:<10} "
            f"(ENV: {envs})"
        )

    _println()
    _println("To activate: set 'enabled = true' in jarvis.toml [harness.jarvis_agent]")
    _println("AND check 'binary_path'. The harness automatically follows the")
    _println("provider choice under [brain].primary — no Anthropic lock.")


class _TermsDeclined(Exception):
    """Raised when the user declines the Terms of Use — setup cannot complete."""


def step_finalize() -> None:
    _println()
    _println("=" * 60)
    _println(" Step 8 / 8 — Terms of use & finish")
    _println("=" * 60)
    _println()

    # Terms of Use must be accepted before first use. Deliberately short here —
    # the authoritative full text is docs/legal/TERMS.md (and the desktop app's
    # Terms view). Declining stops setup: Jarvis is not usable without it.
    from jarvis.setup.onboarding_meta import CURRENT_TERMS_VERSION
    from jarvis.setup.state import accept_terms, mark_onboarding_complete

    _println(f"Terms of Use & Disclaimer (v{CURRENT_TERMS_VERSION}) — short version:")
    _println('  • Free & open-source, provided "as is": no warranty, no liability.')
    _println("  • You are responsible for your usage, your activation word (trademarks),")
    _println("    your API keys, and lawful microphone use.")
    _println("  • Runs locally — the authors run no server and receive none of your data.")
    _println("  Full text: docs/legal/TERMS.md")
    _println()
    if not _ask_yesno("Do you accept these terms?", default=False):
        raise _TermsDeclined()
    accept_terms(CURRENT_TERMS_VERSION)
    _println()

    # Default Yes per the maintainer mandate ("start at boot unless explicitly
    # disabled"). Cross-platform via the autostart port (Windows .lnk / macOS
    # LaunchAgent / Linux XDG .desktop); a headless host degrades to a logged
    # no-op. English copy per the Output Language Policy.
    autostart = _ask_yesno("Start Jarvis automatically at login?", default=True)
    _apply_autostart_choice(autostart)

    # Record terms acceptance + onboarding completion so the desktop app's
    # onboarding gate treats setup as done and does NOT re-run its own
    # onboarding (the CLI wizard and the app share one setup_state.json).
    mark_onboarding_complete()
    cfg.mark_setup_complete()
    _println()
    _println("✓ Setup complete. Enjoy Jarvis!")
    _println()
    _println("Next step:")
    _println("  Run `python -m jarvis` (or run.bat on Windows) → the tray icon appears.")


def _apply_autostart_choice(enabled: bool) -> None:
    """Persist the autostart toggle and apply the OS entry cross-platform.

    Delegates to the ``jarvis.autostart`` port (Windows .lnk / macOS LaunchAgent
    / Linux XDG .desktop). On a headless host the manager reports
    ``supported=False`` and we say so honestly. Never raises — a setup wizard
    must always reach the finish line.
    """
    try:
        from jarvis.core import config_writer

        config_writer.set_autostart(enabled)
    except Exception as exc:  # noqa: BLE001 — persistence is best-effort
        _println(f"  Could not persist the autostart setting: {exc}")

    try:
        from jarvis.autostart import make_autostart_manager, resolve_launch_spec
        from jarvis.platform.capabilities import detect_capabilities

        manager = make_autostart_manager(detect_capabilities())
        if not enabled:
            manager.uninstall(interactive=True)
            _println(
                "→ Autostart disabled. Start Jarvis any time via run.bat or "
                "`python -m jarvis.ui.web.launcher`."
            )
            return

        # interactive=True: on Windows this registers the instant-start logon task
        # via a one-time permission prompt (declined → startup-shortcut fallback).
        status = manager.install(resolve_launch_spec(None), interactive=True)
        if status.supported and status.installed:
            _println(f"→ Autostart enabled: {status.entry_path}")
        elif not status.supported:
            _println(f"→ Autostart not available on this host: {status.detail}")
        else:
            _println(f"→ Autostart could not be installed: {status.detail}")
    except Exception as exc:  # noqa: BLE001 — never crash the wizard
        _println(f"⚠  Autostart setup failed (you can toggle it later in Settings): {exc}")


# ----------------------------------------------------------------------
# Orchestrator
# ----------------------------------------------------------------------

def run() -> int:
    _println()
    _println("╔══════════════════════════════════════════════════════════╗")
    _println("║  Personal Jarvis — First-Run Setup                       ║")
    _println("║  Duration: ~5 minutes, one-time                          ║")
    _println("╚══════════════════════════════════════════════════════════╝")

    # Non-interactive path: headless VPS, CI, or JARVIS_NONINTERACTIVE=1.
    # Skip all prompts; keys are read from ENV / .env by get_secret() at
    # runtime.  Write the .setup-complete marker so subsequent boots go
    # straight to the app without hitting this code path again.
    if _is_noninteractive():
        _println()
        _println("Non-interactive mode detected (no TTY or JARVIS_NONINTERACTIVE=1).")
        _println("Skipping interactive prompts.  API keys will be read from")
        _println("environment variables or the .env file at runtime.")
        _println("See .env.example for the full list of recognised variable names.")
        _println()
        from jarvis.setup.onboarding_meta import CURRENT_TERMS_VERSION

        _println(
            f"By running the non-interactive installer you accept the Terms of Use "
            f"(v{CURRENT_TERMS_VERSION}, docs/legal/TERMS.md)."
        )
        cfg.mark_setup_complete()
        _println("✓ Setup marker written.  Starting Jarvis...")
        return 0

    try:
        step_hardware_check()
        step_api_keys()
        step_mic_check()
        new_hotkey = step_hotkey_check(default_hotkey="ctrl+right_alt+j")
        if new_hotkey != "ctrl+right_alt+j":
            _println(f"→ Hotkey '{new_hotkey}' noted — please enter it in jarvis.toml.")
            # Persistence note: the wizard only writes the hotkey into the config actively in Phase 1
        step_wake_word_setup()
        step_dependency_check()
        step_jarvis_agent_harness_check()
        step_finalize()
        return 0
    except _TermsDeclined:
        _println("\nTerms not accepted — setup stopped. You must accept the terms")
        _println("to use Jarvis. Re-run `jarvis --wizard` when you're ready.")
        return 3
    except KeyboardInterrupt:
        _println("\n\n⚠  Setup aborted. Re-run: `python -m jarvis`")
        return 130
    except Exception as exc:  # noqa: BLE001
        _println(f"\n✗ Setup error: {exc}")
        return 1


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
