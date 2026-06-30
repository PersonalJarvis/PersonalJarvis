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

## 5. The mutable set — schema-derived (Voice-First Config Control, Wave 1.1)

The mutable set is **no longer a hand-maintained list**. It is computed once
(lazily, then cached) by walking the `JarvisConfig` schema: every leaf primitive
field (str/int/float/bool, incl. `Optional`/`Literal`) becomes voice-mutable,
minus `FORBIDDEN_PATTERNS`. See `jarvis/core/self_mod/schema_introspect.py`
(`introspect_mutable_specs`) and the curated `overrides.OVERRIDES` table.

**AD-1 / AP-11 are preserved (reinterpreted):** the set is still fixed by *code*
(the schema + overrides) and is computed at first use, NOT a runtime
`register()` the model could call. A new mutable field appears only by editing
`JarvisConfig` (which is code-reviewed). We only moved from "explicit list" to
"whole schema minus the deny layer". The model still cannot widen the set.

Adding/refining a setting:

1. A new leaf field on a `JarvisConfig` section is mutable **automatically** —
   no registry edit needed.
2. To refine it (a SAFE risk-tier so REST/CLI auto-applies it, a hot-reload
   `needs_restart=False`, a nicer spoken `description`), add a `SpecOverride`
   for its path in `jarvis/core/self_mod/overrides.py`.
3. For an **undeclared** `extra="allow"` key the schema walk can't see (e.g.
   `ui.theme`), the override must carry `pydantic_model_name` + `field_name` to
   force-include it.
4. The parity guard (`tests/unit/self_mod/test_registry.py::TestAllowlistField
   Parity`) checks every emitted spec against the real schema automatically.

**Forbidden paths** (`jarvis/core/self_mod/forbidden.py`): secrets / API keys /
privileged sections (`security.*`, `safety.*`, `harness.*`, `mcp_server.*`,
`*_api_key`, …) are NEVER in the set — read or write. They surface as an honest
voice *refusal*, not a confirmation. The "self-lockout" class is mostly closed
by architecture (no STT/TTS enable flags exist; the provider `enabled` list is a
`list`, which the walk skips; a broken config is rolled back) — see the module
docstring before adding any path there.

**Voice vs. REST/CLI confirmation:** the brain (voice/chat) wiring uses
`auto_apply="all"` (apply immediately, then an honest deterministic readback —
`jarvis/voice/config_readback.py`); REST/CLI keep the SAFE-auto / ASK-confirm
split (`auto_apply="safe_only"`).

## 6. Skill authoring (Plan §7.5)

A voice command like "Erstelle einen Skill, der Spotify pausiert wenn ich
rede" (Create a skill that pauses Spotify when I speak) → main Jarvis
calls the `spawn_skill_author` tool → a Jarvis-Agent (Opus 4.7) generates
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
