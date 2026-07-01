# Telephony — call Jarvis on a phone number

Jarvis can answer a real phone call. A caller dials a Twilio number and talks to
Jarvis as a real-time voice agent, using **Jarvis's own** speech stack — the
same STT, the same Brain, and the same **Charon** voice as the "Hey Jarvis"
microphone path. Nothing about the voice changes between the desk and the phone.

Under the hood the call audio is bridged with **Twilio Media Streams** (raw
audio over a WebSocket), not Twilio's ConversationRelay. Media Streams is the
only mode that lets Jarvis run its own STT and speak in its own consistent
voice — ConversationRelay would force Twilio's TTS voices, so it is disqualified
for the default path (design decision AD-T1).

> **Cloud-first.** The telephony feature is an **optional extra**
> (`pip install -e .[telephony]`). The base install boots without it, and the
> Telephony section in the app degrades gracefully (a clear "feature disabled"
> message, never a crash) when the `twilio` package is absent.

---

## What you need

1. A **Twilio account** with a **voice-capable phone number**.
2. A **public HTTPS URL** that Twilio can reach. Two paths, in order of
   preference:
   - **VPS (recommended, cloud-first):** a domain pointing at your VPS, with
     Caddy or another reverse proxy terminating TLS via Let's Encrypt.
   - **Home PC (development):** a tunnel (cloudflared / ngrok) that exposes the
     local FastAPI port.
3. Your Twilio **Auth Token** stored in the credential manager (the setup
   wizard does this).

---

## 1. Install the extra

```bash
pip install -e .[telephony]
pip install -e . --no-deps   # refresh entry points
```

On Python 3.13+ this also pulls in `audioop-lts` (the mu-law transcode backport,
since `audioop` left the stdlib). On Python 3.11/3.12 `audioop` is built in.

## 2. Store the Auth Token

```bash
python -m jarvis --wizard
```

The wizard lists **Twilio Auth Token** among the secrets. It is stored in the
Windows Credential Manager (service `personal-jarvis`, key `twilio_auth_token`),
never in `jarvis.toml`. You can also set the `TWILIO_AUTH_TOKEN` environment
variable.

## 3. Configure the number (VPS path — recommended)

Deploy Jarvis on a VPS and put a reverse proxy in front of the FastAPI port
(default `8765`). A minimal **Caddyfile** that terminates TLS and proxies both
the HTTP webhook and the WebSocket:

```
jarvis.example.com {
    reverse_proxy localhost:8765
}
```

Caddy handles the WebSocket upgrade for `/api/telephony/media` automatically and
gets a Let's Encrypt certificate for `jarvis.example.com` on first start.

Then in the app's **Telephony** section (or in `jarvis.toml`) set:

```toml
[integrations.twilio]
enabled = true
account_sid = "AC..."                # your Account SID (not a secret)
phone_number = "+49301234567"        # your Twilio number, E.164
public_base_url = "https://jarvis.example.com"   # no trailing slash
language_code = "de-DE"
greeting = ""                        # empty = Jarvis butler default
max_call_seconds = 600
```

Finally point the number's **Voice webhook** at Jarvis:

```bash
python scripts/telephony_provision.py set-webhook \
    --number +49301234567 \
    --url https://jarvis.example.com/api/telephony/voice
```

(or set the webhook to `POST {public_base_url}/api/telephony/voice` in the
Twilio console.)

## 3-alt. Configure the number (home-PC path — development)

Start a tunnel to the local port and copy the public URL it prints:

```powershell
pwsh scripts/telephony-tunnel.ps1 -Port 8765
# or:  pwsh scripts/telephony-tunnel.ps1 -Port 8765 -Provider ngrok
```

Paste the `https://…` URL into **Public base URL**, then run the
`set-webhook` command above with that URL. Note: a free-tunnel URL changes every
restart, so re-run `set-webhook` each time. The VPS path avoids this.

## 4. Verify and call

- In the Telephony section, click **Test connection** (calls the Twilio REST API
  to confirm the Auth Token), then **Self-test voice** (runs a fixed utterance
  through STT → Brain → TTS with no real call, and shows the transcript +
  response so you can confirm there is no text truncation).
- Call your Twilio number. Jarvis greets you, you talk, it answers in the Charon
  voice. Say **"auflegen"** (or "tschüss", "goodbye", "hang up") to end the <!-- i18n-allow: voice hangup trigger words -->
  call; barge-in is supported (start talking while Jarvis is speaking and it
  stops to listen). <!-- i18n-allow -->

Recent calls appear in the **Recent calls** table.

---

## German +49 numbers — regulatory note

Twilio requires a **regulatory bundle** (an address + identity document, and for
some number types a local-presence proof) before it will provision a German
`+49` number. Allow a day or two for Twilio to approve the bundle. A US/UK
number works immediately for testing if you just want to prove the chain.

## Trial-account caveats

On a Twilio **trial** account:
- You can only call **verified** caller IDs.
- Twilio prepends a trial notice to the call.
- Balance is limited.

Upgrade to a paid account for unrestricted inbound calls.

---

## How it works (for maintainers)

```
Caller -> Twilio number
       -> POST /api/telephony/voice         (signed webhook; we validate
                                              X-Twilio-Signature against the
                                              PUBLIC url, then mint a per-call
                                              secret and return TwiML)
       -> <Connect><Stream wss://…/api/telephony/media>  (bidirectional)
       -> WS /api/telephony/media           (validates the per-call secret,
                                              then runs the turn loop)

per turn:  inbound mu-law 8 kHz
           -> linear PCM -> resample 16 kHz -> VAD endpoint
           -> STT.transcribe_pcm -> hangup-regex guard
           -> per-call BrainManager.generate_stream
           -> scrub_for_voice (regex only)
           -> TTS.synthesize (24 kHz Charon)
           -> resample 8 kHz -> mu-law -> 20 ms paced frames
           -> Twilio plays them back to the caller
```

- The Brain is built **per call** so phone and desktop conversations never share
  memory.
- The path never imports `sounddevice` or `SpeechPipeline` — it runs headless on
  a VPS with no audio hardware.
- Modules: `jarvis/telephony/` (audio, session, twiml, security, provisioning,
  status, events, constants) + `jarvis/ui/web/telephony_routes.py`.

### Probe without a phone

```bash
python scripts/probe_telephony_e2e.py
```

drives the media-stream session with synthetic frames and prints a transcript,
a Jarvis response, and the outbound mu-law frame count — the in-repo proof of
correctness. Add `--real --wav utterance.wav` to run the configured real stack
against a 16 kHz mono WAV.

### REST API

Base path `/api/telephony`. UI-facing: `GET /status`, `GET/POST /config`,
`POST /credentials`, `POST /test`, `POST /selftest`, `GET /calls`,
`GET /scripts`. Twilio-facing: `POST /voice`, `WS /media`.
