# How to Set Your Wake Word

Personal Jarvis ships **no built-in wake word**. You pick any phrase you like
during onboarding (or later in Settings); the recognizer is fully generic and
runs entirely on your machine — wake-word listening never sends audio to the
cloud. For the engineering rationale behind the detection paths, see
[`CUSTOM-WAKE-WORD-DESIGN.md`](CUSTOM-WAKE-WORD-DESIGN.md).

---

## Where to set it

1. **Desktop app** — the first-run onboarding has a wake-word step, and
   **Settings → Wake word** changes it any time. The desktop flow fixes the
   "Hey" prefix and you type the rest ("Hey" + your word); a prefixed phrase
   is what keeps the bare word in normal conversation from waking Jarvis.
   Prefer no wake word at all? The onboarding also offers the
   push-to-talk / hotkey path instead.
2. **Terminal wizard** — `python -m jarvis --wizard` for SSH / headless
   setups. Headless installs can also complete the same onboarding in the
   browser UI.

Both paths write to the same place — `[trigger.wake_word]` in `jarvis.toml` —
so they are interchangeable.

---

## How recognition works (engine "auto")

Every phrase goes through the same generic chain — no word is special:

1. **Your own trained model** (`custom_onnx`) — if you supplied a custom
   `.onnx` model for exactly this phrase, it wins (fastest, most accurate).
2. **Any-word keyword spotting (Vosk)** — works offline on every machine,
   CPU-only, no training. A small per-language model (~45 MB) is downloaded
   once at setup.
3. **Local-Whisper transcript match** (`stt_match`) — a higher-accuracy path
   that transcribes short audio windows and fuzzy-matches your phrase
   (tolerates small mishearings). Part of the full install.
4. **Honest degrade** — if none of these can serve your phrase on this
   machine, the wake word stays **off** and Jarvis says so clearly. There is
   no hidden fallback word; use the hotkey / push-to-talk until a local
   engine is available.

You normally never need to choose an engine by hand — leave it on `auto`.

---

## No trademark words

Pick any word — just make sure it isn't someone else's trademark. The product
ships no pre-trained brand models and never downloads one.

---

## Applying a change

Saving a wake-word change from Settings applies it to a running voice
pipeline immediately when possible; otherwise the app tells you a restart is
needed and offers it.

---

## Quick reference

| You want | Do this |
|---|---|
| Change the wake word | Settings → Wake word (or first-run onboarding) |
| Best accuracy for your word | Train/supply a custom `.onnx` (`custom_onnx`) |
| No wake word, manual activation | Choose the push-to-talk / hotkey path |
| Wake word "stopped working" | Run `python -m jarvis.speech.diagnose` |
