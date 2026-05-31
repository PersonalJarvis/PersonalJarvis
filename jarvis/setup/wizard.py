"""First-Run Setup-Wizard (CLI).

Der Wizard läuft einmal beim allerersten `python -m jarvis`. Er:
1. Zeigt Hardware-Analyse + Whisper-Empfehlung.
2. Fragt API-Keys ab und speichert im Windows Credential Manager.
3. Prüft Mikrofon-Verfügbarkeit.
4. Bestätigt Hotkey-Wahl.
5. Schreibt `.setup-complete`-Marker.

Der Wizard ist idempotent — Re-Run überschreibt nur User-bestätigte Werte.

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

from jarvis.core import config as cfg
from jarvis.hardware import detection


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
    key: str              # Name im Credential Manager
    env_fallback: str     # ENV-Variable als Alternative
    label: str            # Anzeigename
    help_url: str         # Wo den Key holen
    required_for: str     # Menschen-lesbar: "Brain (Claude)" etc.
    optional: bool = True


SECRETS: list[SecretSpec] = [
    # Brain-Provider, die GLEICHZEITIG OpenClaw-Bridge aktivieren.
    # OpenClaw liest die Standard-Provider-ENV-Vars (siehe AD-6 Mapping in
    # docs/openclaw-bridge.md §2). Es gibt KEIN separates OPENCLAW_*-Namespace
    # — der Wizard pflegt einen Key, OpenClaw nutzt ihn beim Subprocess-Spawn.
    # Vollständige Mapping-Tabelle: jarvis/missions/worker_runtime/provider_map.py.
    SecretSpec(
        key="anthropic_api_key",
        env_fallback="ANTHROPIC_API_KEY",
        label="Anthropic API Key (Claude)",
        help_url="https://console.anthropic.com/settings/keys",
        required_for="Brain (Claude via API-Key) + OpenClaw-Bridge (anthropic-Provider)",
        optional=True,
    ),
    SecretSpec(
        key="openrouter_api_key",
        env_fallback="OPENROUTER_API_KEY",
        label="OpenRouter API Key (Universal-Gateway)",
        help_url="https://openrouter.ai/keys",
        required_for="Brain (Universal: Zugriff auf alle Modelle über einen Key) + OpenClaw-Bridge (openrouter-Provider)",
    ),
    SecretSpec(
        key="openai_api_key",
        env_fallback="OPENAI_API_KEY",
        label="OpenAI API Key",
        help_url="https://platform.openai.com/api-keys",
        required_for="Brain (GPT), Whisper API (STT), TTS + OpenClaw-Bridge (openai-Provider)",
    ),
    SecretSpec(
        key="codex_openai_api_key",
        env_fallback="CODEX_OPENAI_API_KEY",
        label="OpenAI Codex API Key",
        help_url="https://platform.openai.com/api-keys",
        required_for="OpenAI Codex API-Key-Modus (getrennt vom OpenAI Brain-Provider)",
    ),
    SecretSpec(
        key="gemini_api_key",
        env_fallback="GEMINI_API_KEY",
        label="Google AI Studio / Gemini API Key",
        help_url="https://aistudio.google.com/app/apikey",
        required_for="Brain (Gemini) + OpenClaw-Bridge (google-Provider)",
    ),
    SecretSpec(
        key="grok_api_key",
        env_fallback="GROK_API_KEY",
        label="xAI Grok API Key (Brain + Voice/TTS)",
        help_url="https://console.x.ai/",
        required_for="Brain (Grok) + TTS (Grok Voice — leo/rex/sal/ara/eve) + OpenClaw-Bridge (xai-Provider, ENV XAI_API_KEY)",
    ),
    SecretSpec(
        key="google_tts_credentials_path",
        env_fallback="GOOGLE_APPLICATION_CREDENTIALS",
        label="Pfad zur Google Cloud Service-Account JSON (für TTS)",
        help_url="https://console.cloud.google.com/apis/credentials",
        required_for="TTS (Google Neural2 — hochqualitative Sprachausgabe)",
    ),
    SecretSpec(
        key="deepgram_api_key",
        env_fallback="DEEPGRAM_API_KEY",
        label="Deepgram API Key (schnelles STT)",
        help_url="https://console.deepgram.com/",
        required_for="STT (Deepgram — Cloud-Alternative zu Whisper)",
    ),
    SecretSpec(
        key="groq_api_key",
        env_fallback="GROQ_API_KEY",
        label="Groq API Key (ultra-schnelles Whisper)",
        help_url="https://console.groq.com/keys",
        required_for="STT (Groq Whisper — <50ms Latenz)",
    ),
    SecretSpec(
        key="picovoice_access_key",
        env_fallback="PICOVOICE_ACCESS_KEY",
        label="Picovoice Access Key (Porcupine Wake-Word)",
        help_url="https://console.picovoice.ai/",
        required_for="Wake-Word-Detection (Porcupine)",
    ),
    SecretSpec(
        key="tavily_api_key",
        env_fallback="TAVILY_API_KEY",
        label="Tavily API Key (Web-Search für Agents)",
        help_url="https://app.tavily.com/home",
        required_for="Tool (search_web)",
    ),
    SecretSpec(
        key="elevenlabs_api_key",
        env_fallback="ELEVENLABS_API_KEY",
        label="ElevenLabs API Key (Premium TTS, Multi-Language)",
        help_url="https://elevenlabs.io/app/settings/api-keys",
        required_for="TTS (ElevenLabs — Jarvis-Butler-Stimme mit DE+EN Auto-Detect)",
    ),
    SecretSpec(
        key="cartesia_api_key",
        env_fallback="CARTESIA_API_KEY",
        label="Cartesia.ai API Key (Sonic 3.5 TTS, 42 Sprachen)",
        help_url="https://play.cartesia.ai/keys",
        required_for="TTS (Cartesia Sonic 3.5 — multilingual incl. Deutsch, ~90ms TTFB)",
    ),
    # Phase 5 — Admin-Helper HMAC-Key. Wird NICHT interaktiv abgefragt:
    # beim ersten Helper-Start generiert der `jarvis.admin.launcher` 32
    # zufaellige Bytes und persistiert sie base64-URL-safe-encoded im
    # Credential Manager. Der Wizard listet den Eintrag nur auf, damit er
    # in der Secrets-Uebersicht sichtbar ist (Re-Run zeigt "bereits hinterlegt").
    SecretSpec(
        key="jarvis_admin_hmac",
        env_fallback="JARVIS_ADMIN_HMAC",
        label="Admin-Helper HMAC-Key (auto-generated)",
        help_url="",
        required_for="Phase 5 — Admin-Ops (winget, services, registry, firewall)",
        optional=True,
    ),
    # === F-FRIENDS [F1] · feature/friends-section · the maintainer-2026-04-30 ===
    # Phase F1 — Telegram-Channel-Bot-Token. User legt einen Bot via
    # @BotFather an, bekommt ein Token wie ``123456:ABC-DEF...``, traegt es
    # hier ein. ``getMe``-Validation passiert beim TelegramChannel.start().
    SecretSpec(
        key="telegram_bot_token",
        env_fallback="TELEGRAM_BOT_TOKEN",
        label="Telegram Bot Token (@BotFather)",
        help_url="https://t.me/BotFather",
        required_for="Channel (Telegram) — bidirektionaler Chat mit Friends",
        optional=True,
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
    ),
]


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
    _println(" Schritt 1 / 8 — Hardware-Analyse")
    _println("=" * 60)
    report = detection.analyze()
    rec = detection.recommend_whisper(report)
    _println(detection._format_report(report, rec))

    if report.ffmpeg_version is None:
        _println("⚠  WARNUNG: ffmpeg wurde nicht gefunden. Whisper-STT wird nicht funktionieren.")
        _println("   Installation: https://www.gyan.dev/ffmpeg/builds/ (dann ffmpeg-PATH setzen)")
    if not report.torch_cuda_available and report.has_nvidia_gpu:
        _println("⚠  Hinweis: NVIDIA-GPU erkannt aber PyTorch-CUDA nicht aktiv.")
        _println("   PyTorch mit CUDA installieren für lokale Whisper-Beschleunigung.")

    return report


def step_api_keys() -> dict[str, str]:
    _println()
    _println("=" * 60)
    _println(" Schritt 2 / 8 — API-Keys einrichten")
    _println("=" * 60)
    _println("Keys werden im Windows Credential Manager gespeichert (verschlüsselt).")
    _println("Leer lassen = überspringen. Mindestens ein Brain-Provider nötig.")
    _println()

    stored: dict[str, str] = {}
    for spec in SECRETS:
        existing = cfg.get_secret(spec.key)
        marker = "✓ bereits hinterlegt" if existing else "–"
        _println(f"• {spec.label}  [{marker}]")
        _println(f"  Für: {spec.required_for}")
        _println(f"  Keys holen: {spec.help_url}")
        val = _ask("  Key/Pfad eingeben (Enter = überspringen)", default="")
        if val:
            if cfg.set_secret(spec.key, val):
                stored[spec.key] = val
                _println("  → gespeichert im Credential Manager.")
            else:
                _println("  ⚠  Credential Manager nicht verfügbar, verwende .env-Fallback.")
        _println()
    return stored


def step_mic_check() -> None:
    _println()
    _println("=" * 60)
    _println(" Schritt 3 / 8 — Mikrofon-Check")
    _println("=" * 60)
    try:
        import sounddevice as sd  # type: ignore[import-untyped]
    except ImportError:
        _println("⚠  sounddevice nicht installiert. `pip install -r requirements.txt` ausführen.")
        return

    devices = sd.query_devices()
    inputs = [d for d in devices if d["max_input_channels"] > 0]
    if not inputs:
        _println("⚠  Kein Mikrofon erkannt. Headset einstecken und erneut starten.")
        return

    _println("Verfügbare Eingabegeräte:")
    for idx, dev in enumerate(inputs):
        _println(f"  [{idx}] {dev['name']}  (Channels: {dev['max_input_channels']})")
    _println()
    _println("Default wird 'auto-headset' — Jarvis erkennt Headsets automatisch.")
    _println("Manuelle Wahl jederzeit möglich über jarvis.toml → [audio] input_device.")


def step_hotkey_check(default_hotkey: str) -> str:
    _println()
    _println("=" * 60)
    _println(" Schritt 4 / 8 — Hotkey-Konfiguration")
    _println("=" * 60)
    _println(f"Aktueller Default: {default_hotkey}")
    _println("Sichere Kombinationen: ctrl+right_alt+<buchstabe>, ctrl+shift+<buchstabe>")
    _println("Meiden: alt+f4 (schließt Apps), ctrl+c (copy), win+* (Windows-Shortcuts)")
    choice = _ask("Hotkey anpassen? (leer = Default übernehmen)", default=default_hotkey)
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
    _println("Choose the spoken phrase that wakes Jarvis (default: \"Hey Jarvis\").")
    _println()
    _println("These four phrases work instantly and fully offline — no GPU, no")
    _println("download, lowest latency (pretrained on-device models):")
    for phrase in INSTANT_WAKE_PHRASES:
        _println(f"  • {phrase}")
    _println()
    _println("Any other phrase (e.g. \"Computer\", \"Athena\") needs the optional")
    _println("local-Whisper extra (install via `pip install -e \".[desktop]\"`).")
    _println("Without it, Jarvis falls back to \"Hey Jarvis\" and tells you why —")
    _println("it never pretends a phrase works when it cannot detect it.")
    _println()
    _println("Engine is set to \"auto\": Jarvis picks the instant model when your")
    _println("phrase matches one of the four above, otherwise the Whisper path.")
    _println()

    phrase = _ask("Your wake phrase", default=DEFAULT_WAKE_PHRASE)

    try:
        from jarvis.core import config_writer

        config_writer.set_wake_word(phrase, engine="auto")
        _println(f"→ Wake word saved: \"{phrase}\" (engine: auto).")
        if phrase not in INSTANT_WAKE_PHRASES:
            _println("   Note: this is a custom phrase — it needs the local-Whisper")
            _println("   extra at runtime, otherwise it degrades to \"Hey Jarvis\".")
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
    _println(" Schritt 6 / 8 — Externe CLI-Dependencies")
    _println("=" * 60)
    _println()

    # 1. Node + npm — prerequisite for every npm-packaged tool.
    node = deps.check_node()
    npm = deps.check_npm()
    if node.present:
        _println(f"✓ node {node.version}")
    else:
        _println(f"–  node fehlt. {node.install_hint}")
    if npm.present:
        _println(f"✓ npm {npm.version}")
    else:
        _println(f"–  npm fehlt. {npm.install_hint}")

    # 2. claude CLI — the canonical worker/critic backend since
    #    BUG-023 + CRIT-1. Auto-install if missing AND npm is usable.
    claude = deps.check_claude_cli()
    if claude.present:
        _println(f"✓ claude {claude.version} ({claude.path})")
    elif not npm.present:
        _println("–  claude fehlt — npm muss erst da sein. Manuell installieren:")
        _println("   npm i -g @anthropic-ai/claude-code")
    else:
        _println("–  claude CLI fehlt — installiere via npm (non-destructive)...")
        ok, claude_after = deps.install_claude_cli()
        if ok:
            _println(f"✓ claude {claude_after.version} installiert ({claude_after.path})")
        else:
            _println(f"✗ Auto-Install fehlgeschlagen: {claude_after.install_hint}")
            _println("   Bitte manuell: npm i -g @anthropic-ai/claude-code")

    # 3. openclaw — explicitly optional now.
    openclaw = deps.check_openclaw()
    if openclaw.present:
        _println(f"✓ openclaw {openclaw.version} ({openclaw.path})")
    else:
        _println("–  openclaw fehlt (optional).")
        _println(f"   {openclaw.install_hint}")

    _println()
    # End-of-step summary so a human eyeballing the wizard knows what
    # state the worker path is in.
    if claude.present or (not claude.present and npm.present):
        _println(
            "Worker/Critic-Pfad: claude CLI (OAuth via Claude Max) — "
            "bevorzugt seit Welle-4 + CRIT-1."
        )
    else:
        _println(
            "⚠  Worker-Pfad NICHT bereit. Voice-Missions werden mit "
            "'claude binary not found' fehlschlagen bis claude installiert ist."
        )


def step_openclaw_check() -> None:
    """OpenClaw-Bridge-Status — informativ, kein Key-Eingabe-Schritt.

    Vertrag (docs/openclaw-bridge.md §4.3, Amendment 2026-05-09): Es werden
    KEINE neuen ``OPENCLAW_*``-Secrets im Credential Manager angelegt.
    OpenClaw nutzt die Standard-Provider-ENV-Vars (``GEMINI_API_KEY``,
    ``ANTHROPIC_API_KEY``, ...). Dieser Schritt zeigt dem User nur:

    1. Ob die ``openclaw``-Binary auf PATH liegt (``npm i -g openclaw``).
    2. Welche Personal-Jarvis-Brain-Provider damit Subagent-fähig sind
       (= welche der oben eingegebenen API-Keys auch OpenClaw aktivieren).
    3. Hinweis auf das Mapping ``Personal-Jarvis-Slug → OpenClaw-Slug``.
    """
    import shutil

    _println()
    _println("=" * 60)
    _println(" Schritt 7 / 8 — OpenClaw-Bridge (optional Heavy-Tasks Subagent)")
    _println("=" * 60)
    _println()
    _println("OpenClaw ist der externe Subagent für komplexe Multi-Step-Aufgaben")
    _println("('lies diesen Repo + baue X', 'reproduziere den Bug + schlage Fix vor').")
    _println("Personal Jarvis dispatcht via 'spawn_worker'-Tool an einen kurzlebigen")
    _println("OpenClaw-Subprocess; LLM-Output landet erst nach Kontrollierer-Signatur")
    _println("im Voice-Pfad (siehe docs/openclaw-bridge.md §3 Architektur-Bild).")
    _println()

    # 1. Binary-Check (B-7 Befund: .cmd/.ps1-Wrapper auf Windows zaehlen mit).
    binary = shutil.which("openclaw")
    if not binary:
        for ext in (".cmd", ".ps1", ".exe"):
            binary = shutil.which("openclaw" + ext)
            if binary:
                break

    if binary:
        _println(f"✓ OpenClaw-Binary gefunden: {binary}")
    else:
        _println("–  OpenClaw-Binary nicht auf PATH.")
        _println("   Installation: npm i -g openclaw   (Pin: 2026.5.7, siehe AD-21)")
        _println("   Bridge bleibt inaktiv bis Binary verfügbar ist — kein Crash.")

    # 2. Provider-Mapping anzeigen (lazy import — Wizard soll auch ohne
    #    voll-installierte Module laufen).
    _println()
    _println("Provider-Mapping (Personal-Jarvis → OpenClaw-CLI):")
    try:
        from jarvis.missions.worker_runtime.provider_map import MAPPINGS
    except Exception:  # noqa: BLE001
        _println("   (Provider-Map nicht geladen — überspringe Mapping-Anzeige.)")
        return

    secret_key_overrides = {
        "claude-api": "anthropic_api_key",
        "openrouter": "openrouter_api_key",
        "openai": "openai_api_key",
        "gemini": "gemini_api_key",
        "grok": "grok_api_key",
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
            f"   {marker} {mapping.jarvis:<11} → {mapping.openclaw:<10} "
            f"(ENV: {envs})"
        )

    _println()
    _println("Zum Aktivieren: 'enabled = true' in jarvis.toml [harness.openclaw]")
    _println("setzen UND 'binary_path' prüfen. Bridge folgt automatisch der")
    _println("Provider-Wahl unter [brain].primary — kein Anthropic-Lock.")


def step_finalize() -> None:
    _println()
    _println("=" * 60)
    _println(" Schritt 8 / 8 — Setup abschließen")
    _println("=" * 60)

    # Default Yes per the maintainer mandate ("start at boot unless explicitly
    # disabled"). Cross-platform via the autostart port (Windows .lnk / macOS
    # LaunchAgent / Linux XDG .desktop); a headless host degrades to a logged
    # no-op. English copy per the Output Language Policy.
    autostart = _ask_yesno("Start Jarvis automatically at login?", default=True)
    _apply_autostart_choice(autostart)

    cfg.mark_setup_complete()
    _println()
    _println("✓ Setup abgeschlossen. Viel Spaß mit Jarvis!")
    _println()
    _println("Nächste Schritte:")
    _println("  1. Phase 1 des Plans implementieren (Voice I/O mit Hotkey).")
    _println("  2. `python -m jarvis` erneut aufrufen → Tray-Icon erscheint.")


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
            manager.uninstall()
            _println(
                "→ Autostart disabled. Start Jarvis any time via run.bat or "
                "`python -m jarvis.ui.web.launcher`."
            )
            return

        status = manager.install(resolve_launch_spec(None))
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
    _println("║  Dauer: ~5 Minuten, einmalig                             ║")
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
        cfg.mark_setup_complete()
        _println("✓ Setup marker written.  Starting Jarvis...")
        return 0

    try:
        step_hardware_check()
        step_api_keys()
        step_mic_check()
        new_hotkey = step_hotkey_check(default_hotkey="ctrl+right_alt+j")
        if new_hotkey != "ctrl+right_alt+j":
            _println(f"→ Hotkey '{new_hotkey}' notiert — bitte in jarvis.toml eintragen.")
            # Persistence-Note: wizard schreibt Hotkey erst in Phase 1 aktiv in die Config
        step_wake_word_setup()
        step_dependency_check()
        step_openclaw_check()
        step_finalize()
        return 0
    except KeyboardInterrupt:
        _println("\n\n⚠  Setup abgebrochen. Re-run: `python -m jarvis`")
        return 130
    except Exception as exc:  # noqa: BLE001
        _println(f"\n✗ Setup-Fehler: {exc}")
        return 1


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
