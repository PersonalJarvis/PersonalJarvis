# Voice QA Checklist — Personal Jarvis

A reusable manual test script for validating the full feature surface by *talking*
to Jarvis the way a real user does: general conversation plus multi-step flows,
not just one-shot prompts.

Test utterances are given in **German** and **English** because bilingual
auto-detection is the single most bug-prone area of this system. Everything else
(headers, pass criteria, what-it-probes) is English per the repo language policy.

Mark each row: **✓ pass** · **✗ fail** (write what came back) · **⚠ wrong language**.
The wrong-language column is the one to watch hardest — it is the failure mode the
bug history says is most likely to quietly come back.

---

## Pre-flight (do this once per run)

> A test only counts as a real signal if the **running** code is the code on disk.
> Most recent fixes are uncommitted and marked "needs restart" — testing a stale
> process will surface bugs that are already fixed.

- [ ] Restart the app cleanly via `POST /api/settings/restart-app`
      (`Stop-Process` returns *Access Denied* — the in-app endpoint is the reliable path).
- [ ] Wake word triggers; the orb / whisper-bar reacts.
- [ ] App log or chat transcript is visible — language, beheaded-turn and
      leaked-tool-output bugs are only obvious when you compare *what you said*
      against *what came back*.

---

## A — Basic conversation & bilingual core (highest value)

| # | Say this | Pass criteria | Probes / red flag | Result |
|---|----------|---------------|-------------------|--------|
| A1 | "Hallo Jarvis, wie geht's dir?" | German reply, butler tone, no tool leak | Baseline | |
| A2 | "Hey, how are you doing today?" | English reply | English→German leak (#1 recurring) | |
| A3 | "What's the weather like?" | Stays English, does not refuse | STT force-pin mangling EN→DE | |
| A4 | DE turn, then EN turn, same session | Each reply matches **that** turn's language | Per-turn detection, not session-sticky | |
| A5 | "Erzähl mir einen kurzen Witz." then "And now one in English." | Switches cleanly mid-conversation | Language switch on follow-up | |

## B — Ack-brain & latency

| # | Say this | Pass criteria | Probes | Result |
|---|----------|---------------|--------|--------|
| B1 | "Erklär mir kurz, wie ein Verbrennungsmotor funktioniert." | Sub-second preamble, *then* the real answer | Ack-brain fires before deep brain | |
| B2 | "Wie spät ist es?" | Fast answer, **no** preamble | Suppress-if-fast gate (2000 ms) | |
| B3 | Any normal question | First spoken word within ~1–2 s | Streaming TTS first-frame | |

## C — Web search & weather

| # | Say this | Pass criteria | Probes / red flag | Result |
|---|----------|---------------|-------------------|--------|
| C1 | "Wie ist das Wetter in Berlin?" | Real spoken weather | DDG-can't-do-weather refusal | |
| C2 | "What's the weather in London?" | Spoken, in English | Weather + English combined | |
| C3 | "Was hältst du von exp.com?" | Real spoken opinion, **not** "Aktion erkannt, konnte sie nicht ausführen" | Leaked `search_web` → dropped result | |
| C4 | "Suche mal, wer aktuell Bundeskanzler ist." | Spoken answer from search | General web-search path | |

## D — Computer Use

| # | Say this | Pass criteria | Probes / red flag | Result |
|---|----------|---------------|-------------------|--------|
| D1 | "Öffne Chrome." | Opens; says "Erledigt." not "Wie meinst du das?" | CU success → spurious clarify | |
| D2 | "Öffne Chrome und geh auf youtube.com." | Opens *and* navigates | Browser+URL fast-path | |
| D3 | "Open Discord." | Opens, English confirmation | Conjugation/alias + EN path | |
| D4 | "Öffne den Rechner und tippe 5 mal 5." | Multi-step goal completes | CU planner for compound goals | |

## E — Sub-agent missions (heavy work)

| # | Say this | Pass criteria | Probes / red flag | Result |
|---|----------|---------------|-------------------|--------|
| E1 | "Schreib mir ein kleines Python-Skript, das die ersten 100 Primzahlen ausgibt, und speicher es." | Optimistic ACK, then completion announcement; mission APPROVED | Full Worker→Critic→Kontrollierer loop | |
| E2 | "Spawne einen Subagenten, der die README zusammenfasst." | Explicit spawn actually spawns (not skill-hijacked) | AD-S9 skill-hijack-mutes-turn | |
| E3 | Start E1, then hold the abort ring (~1.2 s) on the running card | Worker is *actually* killed | Hold-to-abort + real cancel | |
| E4 | A long task, then wait | Silent retry OR spoken correction OR audited apology — **never silent drop** | AD-OE6 zero-silent-drops | |

## F — Provider switching

| # | Say this | Pass criteria | Result |
|---|----------|---------------|--------|
| F1 | "Jarvis, wechsel zu Gemini." then ask anything | Confirms; next answer from Gemini | |
| F2 | "Switch to Grok." | Confirms | |
| F3 | "Wechsel zurück zu Claude." | Confirms; routing actually changes (check log) | |

## G — Memory & Wiki

| # | Say this | Pass criteria | Probes | Result |
|---|----------|---------------|--------|--------|
| G1 | "Merk dir, dass mein Lieblingseditor Neovim ist." | Confirms it saved | `wiki-ingest` | |
| G2 | *(later)* "Was ist mein Lieblingseditor?" | "Neovim" | `wiki-recall` round-trip | |
| G3 | "Was weißt du über mich?" | Coherent profile, no raw dump | Profile / Knows-you | |
| G4 | "Füge Max Mustermann als Kontakt hinzu." | Creates contact; a person page mirrors it | Contact→Wiki mirror | |

## H — Skills & integrations

| # | Say this | Pass criteria | Note | Result |
|---|----------|---------------|------|--------|
| H1 | "Check mal meine E-Mails." | Reads inbox OR honest "Verbindung abgelaufen, bitte neu verbinden" | Gmail OAuth is a known open issue — expect the honest reconnect message, not silence | |
| H2 | "Was steht heute in meinem Kalender?" | Real agenda or honest no-access | Calendar skill | |
| H3 | "Welche CLI-Tools hast du?" | Lists connected CLIs with evidence | CLI evidence-gate | |

## I — Self-modification / config by voice

| # | Say this | Pass criteria | Result |
|---|----------|---------------|--------|
| I1 | "Stell die Antwortsprache auf Englisch." | Confirms; next reply English (hot-reload, no restart) | |
| I2 | "Welche Einstellungen kannst du ändern?" | Lists mutable settings | |

## J — Robustness & the "listens forever / never speaks" class

| # | Do this | Pass criteria | Probes | Result |
|---|---------|---------------|--------|--------|
| J1 | Ask a long brain+tool question (e.g. C1) and time it | Still speaks even if the brain thinks >20 s | No-first-frame ceiling beheading tool loops | |
| J2 | Start a sentence, pause mid-thought, go silent | Short clarify question, not infinite listening | Continuation-buffer clarify timer | |
| J3 | "Hallo, öffne mal Chrome." | Treated as a command, not smalltalk | Greeting-prefix swallows command | |
| J4 | Talk over Jarvis while it is speaking | Handles barge-in gracefully | Interrupt handling | |

## K — Flagship end-to-end conversation (run last)

Chain the subsystems the way real use does — the single best "is the whole thing alive" probe.

- [ ] 1. "Hallo Jarvis." → greeting
- [ ] 2. "Wie ist das Wetter heute?" → web search, spoken
- [ ] 3. "Okay, und merk dir bitte, dass ich morgen früh joggen gehen will." → memory write
- [ ] 4. "Öffne Chrome und such nach Laufschuhen." → computer-use + navigate
- [ ] 5. "Switch to English. Summarize what we just did." → language switch + context-aware recall
- [ ] 6. "Schreib mir dazu ein kurzes Notiz-Skript." → heavy sub-agent mission
- [ ] 7. *(while it runs)* hold-to-abort it → cancel

A clean pass means greeting, search, memory, computer-use, mid-conversation language
switch, context retention, mission spawn, and cancel are all healthy *together*.

---

# Deep dive — bilingual / language (scripted, turn by turn)

This is the most bug-prone area, with at least four distinct historical root causes:
the STT force-pin that mangled English audio into German words, the `_looks_german`
detector that tied zero-signal text to German, the force-spawn ACK that spoke German
for an English request, and two hardcoded German fallback phrases. Each dialogue
below targets one of those, with the **exact language each reply must come back in**.

> Read the **transcript**, not just the reply. Some of these bugs corrupt the words
> *before* the brain ever sees them — the reply can look reasonable while the
> transcript proves the input was already wrong.

### L1 — Clean single-language baselines

| Turn | You say | Required reply language | Fail looks like |
|------|---------|-------------------------|-----------------|
| 1 | "Hallo Jarvis, wie geht es dir heute?" | German | English reply |
| 2 | *(new session or after L1)* "Hey Jarvis, how's it going today?" | English | German reply |

### L2 — Mid-session switching (per-turn, not sticky)

| Turn | You say | Required reply language | Fail looks like |
|------|---------|-------------------------|-----------------|
| 1 | "Erzähl mir einen kurzen Witz." | German | — |
| 2 | "Nice. Now tell me one in English." | English | Reply stays German because turn 1 was German |
| 3 | "Und jetzt wieder auf Deutsch, bitte." | German | Reply stays English |

Pass = each reply matches the language of *its own* incoming turn. The classic
failure is the reply sticking to the first turn's language for the whole session.

### L3 — The tie-to-German trap (zero-signal / proper-noun English)

The `_looks_german` heuristic counted stopwords and, on a tie, chose German — so a
perfectly English sentence with few stopwords (lots of proper nouns) got a German
reply. Speak these clearly; they are deliberately low on German-looking cues:

| Turn | You say | Required reply language | Fail looks like |
|------|---------|-------------------------|-----------------|
| 1 | "What do you think about Tesla and SpaceX?" | English | German reply (0-0 tie → German) |
| 2 | "Plan a trip to Tokyo next March." | English | German reply (this is the real reported case) |

### L4 — STT mangling probe (English audio force-pinned to German)

The STT language pin transcribed English audio as German words ("weather like" →
"West-Like", confidence ~0.65). This one is only visible in the **transcript**.

| Turn | You say (speak slowly, clearly) | Check the transcript | Required reply |
|------|---------------------------------|----------------------|----------------|
| 1 | "What's the weather like in Munich?" | Transcript reads English words, not German nonsense | English reply with real weather |

Pass = transcript shows the English words you actually said. If the transcript shows
German-ish garbage, the STT language is still pinned (should be `[stt].language = "auto"`).

### L5 — Spawn-ACK language (force-spawn path)

The optimistic ACK on a heavy spawn spoke German even for an English request. Trigger
a heavy spawn in each language and listen to the **ACK and the completion announcement**:

| Turn | You say | Required ACK + completion language | Fail looks like |
|------|---------|------------------------------------|-----------------|
| 1 | "Build me a small script that prints the first 20 Fibonacci numbers." | English | "Geht klar" / German ACK on an English request |
| 2 | "Bau mir ein kleines Skript, das die ersten 20 Fibonacci-Zahlen ausgibt." | German | English ACK on a German request |

### L6 — Hardcoded German fallback probe

Two fallback phrases were hardcoded German. Trigger an edge/refusal path in English
and confirm the fallback is English:

| Turn | You say | Required reply language | Fail looks like |
|------|---------|-------------------------|-----------------|
| 1 | "Was hältst du von exp.com?" (German) | German real opinion | "Aktion erkannt, konnte sie nicht ausführen" (dropped result) |
| 2 | "What do you think of exp.com?" (English) | English real opinion | German fallback phrase on an English turn |

### L7 — Weather refusal, both languages

| Turn | You say | Required reply | Fail looks like |
|------|---------|----------------|-----------------|
| 1 | "Wie ist das Wetter in Hamburg?" | Real spoken weather, German | Refusal ("kann ich nicht") |
| 2 | "What's the weather in Hamburg?" | Real spoken weather, English | Refusal or German reply |

---

## How to read failures

- **⚠ wrong language** with a correct answer → a language-detection bug (L2/L3/L5/L6),
  not a capability bug. Note which turn and which direction (DE↔EN).
- **Transcript already wrong** → STT-layer bug (L4), upstream of the brain.
- **"Action recognized, couldn't execute it"** while a tool clearly ran → leaked /
  dropped tool output (C3/L6), not a real capability failure.
- **Silence / never speaks** on a slow turn → watchdog / no-first-frame class (J1).
- If a whole section fails the same way, re-check the **pre-flight restart** before
  filing anything — stale process is the most common false positive.
