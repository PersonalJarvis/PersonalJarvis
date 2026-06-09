# How to Change Your Wake Word

This guide explains how to choose the spoken phrase that wakes your assistant
(there is no preset — you choose your own; the bundled offline fallback is
**"Hey Rhasspy"**). For the engineering rationale behind the three
detection paths, see [`CUSTOM-WAKE-WORD-DESIGN.md`](CUSTOM-WAKE-WORD-DESIGN.md).

---

## Two ways to set it

### 1. Desktop Settings UI

Open the desktop app and go to **Settings → Wake word**. Pick one of the
instant phrases from the dropdown, or type your own phrase, then save. This is
the recommended path for everyday use.

### 2. First-run setup wizard

The wizard (`python -m jarvis` on first launch, or `python -m jarvis --wizard`
to re-run) includes a **"Wake word"** step. It asks for your phrase (default
"Hey Jarvis") and saves it for you. If you skip it, the default is kept.

Both paths write to the same place — `[trigger.wake_word]` in `jarvis.toml` —
so they are interchangeable.

---

## Instant phrases vs. custom phrases

There are two classes of wake word, and the difference matters:

### Instant phrases (recommended)

Four phrases work **instantly, fully offline, on CPU only, with no download**.
They use small pretrained on-device models and have the lowest latency:

- **Hey Jarvis** (default)
- **Alexa**
- **Hey Mycroft**
- **Hey Rhasspy**

If you pick one of these, nothing else is required — it just works.

### Custom phrases (any other word)

You can also choose **any** phrase you like — for example "Computer" or
"Athena". A custom phrase is matched against a live local-Whisper transcript,
so it requires the optional **local-Whisper extra**:

```bash
pip install -e ".[desktop]"
```

This installs the on-device speech-to-text engine used to detect arbitrary
phrases. It is heavier than the instant models (it benefits from a GPU but runs
on CPU too) and is intentionally **not** part of the slim/VPS base install.

**No silent failures.** If you set a custom phrase on a machine that does *not*
have the local-Whisper extra installed, Jarvis does **not** pretend it works.
It falls back to **"Hey Jarvis"** and shows a clear message telling you the
chosen phrase needs the local-Whisper extra (or a custom on-device model). You
will never be left with a wake word that quietly never fires.

---

## The "auto" engine

Both the Settings UI and the wizard save your phrase with the engine set to
**`auto`**. That means Jarvis picks the right detection path for you:

- Your phrase matches one of the four instant phrases → the fast pretrained
  on-device model (offline, CPU, instant).
- Your phrase is anything else and local Whisper is available → the
  local-Whisper text-match path.
- Your phrase is anything else and local Whisper is *not* available → graceful
  fallback to "Hey Jarvis" with a clear message.

You normally never need to choose an engine by hand.

---

## A restart is required

A wake word change takes effect on the **next Jarvis restart**. The wake engine
and phrase matcher are resolved once when the voice pipeline starts, so saving
the setting alone does not re-arm the listener mid-session. Restart Jarvis (for
example via `run.bat`) and your new wake word is live.

---

## Quick reference

| You want… | Do this | Restart needed? |
|---|---|---|
| One of the four instant phrases | Settings UI or wizard, pick from the list | Yes |
| Any custom phrase | Install `[desktop]` extra, then set it in Settings UI or wizard | Yes |
| Back to the default | Set the phrase to "Hey Jarvis" | Yes |
