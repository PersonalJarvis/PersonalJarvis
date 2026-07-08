**\# Bug Report — Custom wake word never fires after fresh install (\`auto\` silently degrades to \`stt\_match\`)**

\> Handoff for a coding agent working on the public repo. Everything below is  
\> reconstructed from a live install on the maintainer's machine — no code was  
\> changed, no push was made.

**\#\# TL;DR**

A user configures a **\*\*custom proper-noun wake phrase\*\*** (\`"Hey Ruben"\`) with  
\`engine \= "auto"\` and completes onboarding successfully. The wake word \*\*never  
fires\*\*. Root cause is a provisioning \+ fallback gap, **\*\*not\*\*** user  
misconfiguration:

1\. The fresh **\*\*v1.0.3\*\*** checkout ships **\*\*no wake-model assets\*\*** for either  
   reliable engine — \`jarvis/assets/wakeword/\` is **\*\*absent\*\*** (openWakeWord  
   backbones) and \`data/wake\_models/vosk/\` **\*\*does not exist\*\*** (Vosk KWS).  
2\. So \`resolve\_wake\_plan(engine="auto")\` falls all the way through to the  
   **\*\*weakest\*\*** engine, \`stt\_match\` (local \`base\`/CPU Whisper transcribe-and-match).  
3\. \`stt\_match\` **\*\*structurally cannot recognize a hard custom name\*\***: the base  
   model garbles \`"Ruben"\` (transcribes spoken "Hey Ruben" as \`'Hey, um, zu  
   sein.'\`), so the phrase matcher gets \`matched=0\` forever.  
4\. Onboarding marked the setup **\*\*complete\*\*** and never warned that the chosen  
   word requires a model that isn't installed.

\*\*The user built the config exactly right. The install never provisioned an  
engine that can serve a custom word, and \`auto\` degraded silently instead of  
warning.\*\*

**\#\# Environment**

| | |  
|---|---|  
| Repo | \`github.com/PersonalJarvis/PersonalJarvis\` |  
| Branch | \`main\` |  
| **\*\*Commit\*\*** | **\*\*\`ae1bcdf64d8c67921490b7fb479b784b367ab88d\`\*\*** (\`v1.0.3\` — "release: v1.0.3 — Inworld premium voice, cross-provider voice picker, wiki/wake/fallback robustness") |  
| Install type | managed install (\`.jarvis-managed-install\` present), \`install/installer.py\` |  
| OS | Windows 11 (26200) |  
| GPU/CUDA | \`wake\_cuda\_probe.json → {"cuda": false}\` → wake runs on **\*\*CPU\*\*** |  
| STT (utterance) | OpenRouter cloud (\`JARVIS\_\_STT\_\_PROVIDER=openrouter-stt\`); **\*\*wake stays local\*\*** |  
| \`faster\_whisper\` | 1.2.1 installed → \`stt\_match\` available |  
| \`vosk\` (pip) | installed, **\*\*but no model dir\*\*** → \`vosk\_kws\` unavailable |

**\#\#\# User config (\`jarvis.toml\`) — correct, as intended**  
\`\`\`toml  
\[trigger\]  
wake\_word\_enabled \= true

\[trigger.wake\_word\]  
phrase \= "Hey Ruben"  
engine \= "auto"  
\`\`\`

**\#\#\# Onboarding state (\`data/setup\_state.json\`) — reported success**  
\`\`\`json  
{ "onboarding\_step": "finish",  
  "wake\_word\_acknowledged\_at": "2026-07-08T08:05:16Z",  
  "onboarding\_completed\_at": "2026-07-08T08:05:32Z" }  
\`\`\`

**\#\# Root-cause chain (with code anchors)**

\`resolve\_wake\_plan\` — \`jarvis/speech/wake\_phrase.py:320\`. \`engine="auto"\` tries,  
in order:

| Order | Engine | Requirement | State on this install |  
|---|---|---|---|  
| 1 | \`custom\_onnx\` | trained \`.onnx\` at \`custom\_model\_path\` | none configured → skip (\`wake\_phrase.py:367\`) |  
| 2 | \`vosk\_kws\` | model dir \`data/wake\_models/vosk/\<lang\>/\` | **\*\*dir absent\*\*** → skip (\`wake\_phrase.py:416\`, \`resolve\_vosk\_model\_path\` in \`wake\_constants.py:175\`) |  
| 3 | **\*\*\`stt\_match\`\*\*** | \`faster\_whisper\` importable | present → **\*\*selected\*\*** (\`wake\_phrase.py:451\`) |  
| 4 | degrade \`wake\_available=False\` | — | not reached |

Additionally, \`bundled\_wakeword\_models()\` (\`jarvis/assets/\_\_init\_\_.py:40\`)  
returns \`None\` because \*\*\`jarvis/assets/wakeword/\` does not exist in the release  
tree\*\* — so even a user who supplied a custom \`.onnx\` would be blocked (the  
backbones \`melspectrogram.onnx\` / \`embedding\_model.onnx\` are missing).

Net: on a stock v1.0.3 install, \*\*the only reachable wake engine is the one  
least able to recognize a custom word.\*\*

**\#\# Evidence (live log — \`data/jarvis\_desktop.log\`, 2026-07-08)**

Wake armed correctly:  
\`\`\`  
10:07:39  Wake-word plan: engine=stt\_match keyword=ruben phrase='Hey Ruben' — via local-Whisper transcript match.  
10:07:39  Pipeline bereit. … OWW=off WAKE=\['ruben'\] (threshold=0.15) WHISPER-WAKE=on  
10:07:39  Wake-Listener aktiv — sag 'Hey Ruben' …  
\`\`\`  
Recognition fails on three fronts:  
\`\`\`  
\# (a) proper noun garbled — never spells "Ruben"  
10:10:21  rolling-whisper: text='Hey, um, zu sein.'      ← spoken "Hey Ruben"  
          rolling-whisper: text='und'   (rejected, confidence 0.153)  
          💓 wake-heartbeat: … transcribed=12 … matched=0     ← matched stays 0

\# (b) quiet mic — most windows gated before Whisper runs  
          💓 wake-heartbeat: … max-rms=0.0125 (-38 dBFS) … gated\[rms=964 …\]  (964/1370 windows dropped)

\# (c) faster-whisper wedges on CPU (AP-24 / BUG-036)  
10:07:58  WARNING Rolling-Whisper transcription aborted after 8.0s (hung STT) — re-polling  
10:08:38  ERROR   in-flight transcription stuck for \>20 s (true hang) — rebuilding the wedged wake model  
10:08:38  WARNING FasterWhisperProvider.recover(): dropping a wedged model \+ lock …  
\`\`\`

**\#\# Contributing factors (ranked)**

1\. **\*\*\[decisive\] Custom proper noun \+ transcription engine.\*\*** \`stt\_match\` cannot  
   spell \`"Ruben"\`. This is exactly the failure documented in \*\*AP-25 / AP-27 /  
   BUG-037\*\* ("truly-instant \+ zero-ghost custom wake needs a neural KWS model,  
   NOT transcription").  
2\. **\*\*\[provisioning\] No model for the reliable engines shipped.\*\*** \`vosk\_kws\` and  
   \`custom\_onnx\` are both dead-on-arrival because their assets aren't present in  
   the v1.0.3 install.  
3\. **\*\*\[silent degrade\] \`auto\` picks the weakest engine without warning\*\*** when a  
   custom phrase is set — no log/UI signal that recognition will be unreliable.  
4\. **\*\*\[secondary\] Quiet mic\*\*** — 964/1370 windows below the RMS/peak gate  
   (\`min\_rms=0.003\`, \`min\_peak=0.008\`, \`rolling\_whisper\_wake.py:201,212\`).  
5\. **\*\*\[secondary\] CPU Whisper wedge\*\*** — 8s/20s hangs \+ self-heal rebuild (AP-24).

**\#\# Recommended fixes (for the repo)**

1\. **\*\*Provision a reliable wake engine for custom words at install/onboarding.\*\***  
   When the user sets a custom phrase, auto-download a Vosk model for  
   \`\[stt\].language\` into \`data/wake\_models/vosk/\<lang\>/\` so \`auto\` resolves to  
   \`vosk\_kws\` (package is already a dependency). This is the smallest change  
   that makes a custom word work out of the box.  
2\. **\*\*Ship / auto-fetch the openWakeWord backbones.\*\*** \`jarvis/assets/wakeword/\`  
   (\`melspectrogram.onnx\`, \`embedding\_model.onnx\`) is missing from the release  
   tree, so \`bundled\_wakeword\_models()\` returns \`None\` and \`custom\_onnx\` can  
   never load. Either package these assets or fetch them on first run.  
3\. **\*\*Stop \`auto\` from silently landing on \`stt\_match\` for a custom word.\*\*** In  
   \`resolve\_wake\_plan\`, when the phrase is a custom/proper noun and only  
   \`stt\_match\` is reachable, emit a **\*\*loud\*\*** warning (log \+ onboarding/UI) that  
   recognition will be unreliable and point to the Vosk/\`custom\_onnx\` remedy —  
   or refuse to mark onboarding "complete" until a working engine is present.  
4\. **\*\*Make onboarding's wake step verify, not just acknowledge.\*\*** Add a live  
   mic-dBFS check (reuse \`python \-m jarvis.speech.diagnose\`, which already warns  
   \`\< \-40 dBFS\`) plus a "say your wake word once" confirmation before writing  
   \`wake\_word\_acknowledged\_at\`. Both the quiet mic and the unrecognizable word  
   would have been caught during setup.  
5\. **\*\*Investigate the CPU \`stt\_match\` wedge (AP-24 / BUG-036).\*\*** The \`base\`/\`cpu\`  
   wake Whisper still hangs (8s → 20s → rebuild) on this machine even with  
   \`wake\_cuda\_probe \= {cuda:false}\`. Likely a ctranslate2 CPU thread-pool  
   deadlock; revisit fixed \`cpu\_threads\` on the CPU floor.

**\#\# Reproduce**

1\. Fresh v1.0.3 managed install on a CUDA-less Windows box.  
2\. \`jarvis.toml\`: \`wake\_word\_enabled=true\`, \`\[trigger.wake\_word\] phrase="Hey Ruben"\`, \`engine="auto"\`.  
3\. Complete onboarding, speak "Hey Ruben".  
4\. Observe \`data/jarvis\_desktop.log\`: \`engine=stt\_match\`, \`matched=0\`, garbled  
   transcripts, periodic wedge/rebuild. Wake never triggers.

**\#\# Diagnostic tool**

\`python \-m jarvis.speech.diagnose\` — prints input devices, live mic dBFS  
(warns \`\< \-40 dBFS\`), and a Whisper transcription test. Confirms both the  
quiet-mic and the garbled-transcription symptoms directly.

