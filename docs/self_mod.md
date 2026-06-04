# Self-Mod Pipeline — User Documentation (Phase 7)

Plan reference: [docsplansphase-7-self-mod/JARVIS_SELFMOD_PLAN.md](../docsplansphase-7-self-mod/JARVIS_SELFMOD_PLAN.md)
Bootstrap context: [docsplansphase-7-self-mod/PROJEKT_KONTEXT.md](../docsplansphase-7-self-mod/PROJEKT_KONTEXT.md)

## 1. What can I (the user) do, and what not?

Personal Jarvis can, at runtime and on a voice or chat command, change a
**hardcoded** list of its own settings and create new skills. Both are
possible ONLY via speech/chat — the web UI is read-only.

**Mutable settings (Plan §7.1 allowlist):**

| Path | Risk tier | Restart? | What it does |
|---|---|---|---|
| `tts.provider` | ASK | no | TTS provider (e.g. from ElevenLabs to Gemini Flash) |
| `tts.voice_de` | ASK | no | German TTS voice |
| `tts.voice_en` | ASK | no | English TTS voice |
| `tts.speed` | SAFE | no | Speaking speed (0.5..2.0) |
| `stt.provider` | ASK | yes | STT provider (speech-to-text) |
| `brain.primary` | ASK | yes | Primary brain provider |
| `ui.theme` | SAFE | no | UI theme (light/dark/auto) |
| `profile.language` | ASK | no | Profile language (DE/EN/auto) |

**What Jarvis does NOT mutate:**
- API keys (only via the UI in Phase 7.7+)
- `security.*`, `mcp_server.*`, `harness.*` paths (privilege protection)
- Generic tool configuration

## 2. What it sounds like (example conversation)

```
User:    "Wechsle TTS auf Gemini Flash."
Jarvis:  "Verstanden — TTS-Provider wechselt von elevenlabs zu
          gemini-flash-tts. Bestätigen?"
User:    "Ja."
Jarvis:  "Erledigt — TTS-Provider ist jetzt gemini-flash-tts."
```

The **end-focus pattern** (old value first, new value last) is
intentional: STT misshears stand out at the end of the sentence, because
the brain focuses on the last tokens. A faked STT output of "Karen"
instead of "Charon" would therefore be noticed immediately in the echo —
the user answers "nein" (no) and no mutation happens.

**SAFE-tier paths** (`tts.speed`, `ui.theme`) skip the echo confirmation
to protect against confirmation fatigue: "Setze Speed auf 1.3" →
immediate persistence, a terse confirmation "Geht klar —
Sprechgeschwindigkeit jetzt 1.3."

## 3. Audit trail

Every mutation — success, reject, veto, timeout, pre-validate fail,
rollback — is stored in `data/self_mod.log` as JSON Lines. Plan §AD-6:
no rotate, no truncate, append-only. Format:

```json
{
  "ts": "2026-04-26T11:42:08.123Z",
  "audit_id": "uuid4",
  "source": "voice|chat|ui",
  "requested_by": "hauptjarvis|sub-jarvis|user",
  "path": "tts.provider",
  "old_value": "elevenlabs",
  "new_value": "gemini-flash-tts",
  "ok": true,
  "rolled_back": false,
  "error": null
}
```

Values for sensitive paths (e.g. `*_api_key`, `*_token`) are masked with
`*` of the same length before being written (Plan §AP-2 defense-in-depth).

UI view: SelfModView in the sidebar → History tab. Clicking an audit row
opens a detail drawer with the full JSON. Sensitive paths explicitly show
a "***" badge instead of cleartext.

## 4. Rollback behavior

Before every mutation the `AtomicConfigWriter` creates a backup in
`<jarvis.toml.parent>/.backups/jarvis.toml.<iso>.bak`. If, after the
write, the reload test (`ConfigLoader.load(jarvis.toml)`) crashes — e.g.
because a subtle schema error slipped through pre-validate — the backup is
restored **automatically** and a `ReloadError` is raised. The user hears
a TTS message "Konnte nicht gespeichert werden, hab den vorherigen
Zustand wiederhergestellt." (Could not be saved, I restored the previous
state.) and the audit has `rolled_back=true`.

**Manual restore via the UI:** Backups tab → "Wiederherstellen" (Restore)
button next to the desired backup file. Requires `admin_password`. The
restore itself is also audited.

**Backup GC:** automatic after every mutation. Keeps at least 10 (floor),
at most 50 (FIFO cap). Backups older than 30 days are checked, but not
removed below the floor.

## 5. Extending the allowlist (for developers)

The allowlist is hardcoded in
`jarvis/core/self_mod/registry.py:SelfModRegistry.ALLOWED`. Plan §AD-1
+ Plan §AP-11: no configuration file, because otherwise the model could
edit the allowlist itself (constraint self-bypass).

Steps:

1. Add a new `MutableSpec(...)` to `SelfModRegistry.ALLOWED`.
   Fields: `path`, `pydantic_model_name`, `field_name`, `risk_tier`,
   `needs_restart`, `description`, `sensitive` (default `False`).
2. Ensure the path has a Pydantic field in `JarvisConfig`
   (`jarvis/core/config.py`) — otherwise pre-validate would silently
   ignore it.
3. Add a test in `tests/unit/self_mod/test_registry.py`.
4. PR with a plan update in `docsplansphase-7-self-mod/JARVIS_SELFMOD_PLAN.md`
   §7.1 allowlist table.

**Sensitive paths** (API keys, tokens): NEVER in the allowlist. Phase 7.7
(UI keyring editing) is the intended path for API-key mutations.

## 6. Skill authoring (Plan §7.5)

A voice command like "Erstelle einen Skill, der Spotify pausiert wenn ich
rede" (Create a skill that pauses Spotify when I speak) → main Jarvis
calls the `spawn_skill_author` tool → sub-Jarvis (Opus 4.7) generates
SKILL.md → forces `state=draft`. The draft lands in
`~/.jarvis/skills/<slug>/` and is visible in the hot-reload pool, but is
**not active**.

Manual activation:
- Web UI: `SkillsView` → draft badge "ENTWURF — vor Aktivierung review"
  (DRAFT — review before activation) → click "Aktivieren" (Activate)
  (Phase 7.6+, UI under construction).
- CLI: `python -m jarvis.skills.cli --promote <slug>`.

The pre-promote lint blocks `eval/exec/os.system/subprocess(shell=True)`
as well as reflective bypasses (`getattr(__builtins__, "eval")`). Skills
that violate the allowlist fail at promote time (Plan §AP-11).
