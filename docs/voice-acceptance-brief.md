# Voice Acceptance Brief — Persona+Delegation Refactor

**Mandate:** `Jarvis-Behavior/persona-delegation-mandate.md` §"Phase 6 — Voice-Acceptance"
**Tester:** Alex (manual)
**Setup:** `run.bat --debug` (console + verbose logging) in the repo root
**Status:** 2026-04-29 — all 6 mandate phases ☑ verified; F-9 fixed (`persona_loader` reactivated, JARVIS_PERSONA takes effect in the prompt); F-11 fixed (Anthropic `<function_calls>` markup in the filter).
**Prerequisite:** Branch bug **F-10** (`jarvis.clis.risk_integration` missing) must be resolved first — either via `git stash pop` of the prior work or a targeted module restore. Otherwise the voice pipeline does not start.

---

## Preparation

```bash
# 1. (If not done yet) Restore prior work from the stash
git stash list                     # shows stash@{0}: WIP vor Phase 3
git stash pop                       # merges the prior work back
                                    # resolve conflicts with Phase 1-5 files manually

# 2. Editable install after code changes
pip install -e . --no-deps          # a few seconds

# 3. Voice app with console logs
run.bat --debug
```

**During the test**, log subprocess spawns in a second terminal:

```powershell
# counts running openclaw/codex/cli-tool subprocesses
while($true) {
  Get-Process | Where-Object {
    $_.ProcessName -match 'claude|codex|node'
  } | Format-Table Id, ProcessName, StartTime
  Start-Sleep 2
}
```

---

## Test protocol — 12 voice turns from mandate phase 6

Say **"Hey Jarvis"** as the wake word **before every turn**. Note briefly after each turn:
- Response time (perceived: <1 s = "fast", 1–3 s = "ok", >3 s = "spawn reflex?")
- Whether you see a subprocess spawn in the 2nd terminal (Y/N)
- "butler-like" / "still loose-guy-like" / "neutral"

### Block A — 5 smalltalk turns (expectation: 0 subprocesses, <1 s per turn)

| # | You say | Expectation | Alex's note: perceived | Subprocess? | Tone |
|---|---|---|---|---|---|
| 1 | "Hallo." ("Hello.") | Short greeting with "Alex". | _____ | _____ | _____ | <!-- i18n-allow: voice examples -->
| 2 | "Wie geht's dir?" ("How are you?") | Smalltalk answer, no "Ich bin einsatzbereit" ("I am ready for deployment"). | _____ | _____ | _____ | <!-- i18n-allow: voice examples -->
| 3 | "Was ist die Hauptstadt von Frankreich?" ("What is the capital of France?") | "Paris." directly. | _____ | _____ | _____ | <!-- i18n-allow: voice examples -->
| 4 | "Danke." ("Thank you.") | Dry acknowledgement. | _____ | _____ | _____ | <!-- i18n-allow: voice examples -->
| 5 | "Auf Wiedersehen." ("Goodbye.") | "Auf Wiedersehen, Alex." (hangup contract). | _____ | _____ | _____ | <!-- i18n-allow: voice examples -->

### Block B — 3 spawn turns (expectation: exactly 1 spawn per turn)

| # | You say | Expectation | Alex's note: perceived | Subprocess? | Tone |
|---|---|---|---|---|---|
| 6 | "Lies die Datei jarvis.toml und sag mir was drin steht." ("Read the file jarvis.toml and tell me what's in it.") | Spawn → Jarvis-Agent reads → short summary, **no tool JSON in the voice output**. | _____ | _____ | _____ | <!-- i18n-allow: voice examples -->
| 7 | "Such im Web nach dem aktuellen Wetter in Berlin." ("Search the web for the current weather in Berlin.") | Spawn → Jarvis-Agent → weather info. | _____ | _____ | _____ | <!-- i18n-allow: voice examples -->
| 8 | "Mach einen Screenshot und sag mir was du siehst." ("Take a screenshot and tell me what you see.") | Spawn → Computer-Use → description. | _____ | _____ | _____ | <!-- i18n-allow: voice examples -->

### Block C — 2 bad-news turns (provoke errors — expectation: no cushioning, no "Es tut mir leid, aber" ("I'm sorry, but"))

| # | You say | Expectation | Alex's note: perceived | Tone |
|---|---|---|---|---|
| 9 | "Lies die Datei /nicht/existent.txt." ("Read the file /not/existent.txt.") | Dry error message, no "Es tut mir leid, aber leider" ("I'm sorry, but unfortunately"). | _____ | _____ | <!-- i18n-allow: voice examples -->
| 10 | "Verbinde dich mit dem Server xyz123.invalid." ("Connect to the server xyz123.invalid.") | Direct error notice. | _____ | _____ | <!-- i18n-allow: voice examples -->

### Block D — 2 echo-trap turns (expectation: NO "Du möchtest also …" ("So you want to …"))

| # | You say | Expectation | Alex's note: perceived | Tone |
|---|---|---|---|---|
| 11 | "Ich möchte wissen, wie spät es ist." ("I'd like to know what time it is.") | Direct time answer, NO "Du möchtest also wissen, wie spät es ist" ("So you want to know what time it is"). | _____ | _____ | <!-- i18n-allow: voice examples -->
| 12 | "Ich brauche ein neues Notepad-Fenster." ("I need a new Notepad window.") | "Öffne ich, Alex." ("Opening it, Alex.") or direct spawn — NO "Du möchtest also ein neues Notepad-Fenster" ("So you want a new Notepad window"). | _____ | _____ | <!-- i18n-allow: voice examples -->

---

## Mandate success criteria (to check off after the 10-minute test)

- [ ] **5 smalltalk turns spawn 0 `openclaw` subprocesses** (Block A, subprocess column all "N").
- [ ] **3 spawn turns spawn exactly 1 subprocess each** (Block B, 1× "Y" each during the turn).
- [ ] **Voice output contains no tool JSON/XML leak** (Block B, no "spawn_sub_jarvis(…)" / "<spawn_sub_jarvis>" audible).
- [ ] **Echo trap (11+12) delivers a direct answer** without a "Du möchtest also …" ("So you want to …") opener.
- [ ] **Bad news (9+10) without cushioning** ("Es tut mir leid, aber" ("I'm sorry, but") audible = ❌).
- [ ] **The form of address is "Alex"** (NEVER "Sir"/"Mr. Stark"/"Tony"/"boss" audible).
- [ ] **Hangup contract** (turn 5): exactly "Auf Wiedersehen, Alex." ("Goodbye, Alex.") — not "Gern. Bis dann." ("Sure. See you then.").

## Alex's overall assessment

**Tone verdict (check one of these):**

- [ ] ☑ **"felt butler-like"** → refactor successful, Phase 6 ☑.
- [ ] ◐ **"mostly butler, individual drift spots"** → Which turns? (Note them.)
- [ ] ☐ **"still felt like a loose guy"** → What stood out? (Note it.)

**Optional audio note:** a short voice recording (e.g. via the Windows Sound Recorder) of the three most striking turns, attach it to the next sync with me.

---

## After the test — diagnostic help

If one of the success points is ❌, look into the console logs from the `run.bat --debug` window:

| Symptom | What to search for in the log | Hint |
|---|---|---|
| Smalltalk spawned | `Force-Spawn Sub-Jarvis: 'Hallo'` | Smalltalk allowlist does not catch — `spawn_verbs` too broad or allowlist pattern missing. |
| Spawn turn delivered tool JSON | `🧹 Output-Filter [...]: ['removed_tool_json']` missing | Filter was bypassed — new tool-use markup not matched by `TOOL_NAMES` (see ADR-0010, F-11). |
| Echo opener still audible | `🧹 Output-Filter [...]: ['rephrased_echo']` missing | Extend the echo pattern in the first 60 characters. |
| Hangup contract missed | `JARVIS_PERSONA.md` not in the prompt | ~~F-9~~ **Fixed 2026-04-29** (`46a84c0d feat(brain): persona_loader reactivated`). If the bug still occurs, check `from jarvis.brain.persona_loader import load_persona_prompt` in `manager.py`. |
| Voice pipeline crashes on startup | `ModuleNotFoundError: jarvis.clis.risk_integration` | F-10: stash pop or targeted module restore. **Still open** — Phase-8 hook owner. |
| Tool-use markup audible (`<function_calls>`/`<invoke>` or similar) | `🧹 Output-Filter [...]: ['removed_tool_json']` missing | ~~F-11~~ **Fixed 2026-04-29** (`c8729c07 feat(filter): anthropic-internal-tags + base64-leaks`). If a new markup format appears, add it to `output_filter.py:TOOL_NAMES` / `GENERIC_TOOL_WRAPPER_RE`. |

Send the filled-out test sheet back to me (even if only partial), then we will iterate specifically on the drift spots.
