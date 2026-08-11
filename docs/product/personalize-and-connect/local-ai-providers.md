---
title: "Use Local AI Providers"
slug: local-ai-providers
summary: Run chat and speech on your own machine, or connect a trusted self-hosted server, without making cloud access mandatory.
section: "Personalize and connect"
section_order: 3
order: 7
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-08-09
phase: "-"
audience: end-user
tags: [providers, local-ai, ollama, speech-recognition, text-to-speech, privacy, self-hosting]
related: [providers-and-api-keys, dictation, voice-conversations, ultrawiki]
---

Local AI can keep the Brain, speech recognition, or spoken voice on hardware
you control. Make one part local first, then add the others when ready.

## Before You Start

- Open **API Keys & Providers**; no settings-file editing is needed. Select
  **Pipeline** for local speech because Realtime bypasses its three providers.
- Keep the app open during installation and leave extra disk space.
- Treat a model on another computer as self-hosted, not offline. Your text or
  audio still crosses the network to that computer.

## Choose the Right Local Option

| Choice | Purpose | Practical tradeoff | API key |
|---|---|---|---|
| **Ollama (local)** | Brain on this computer or another Ollama host | Size and speed depend on the downloaded model; larger models need more memory | None |
| **Local server (OpenAI-compatible)** | Brain through llama.cpp, vLLM, LM Studio, Hugging Face Transformers Serve, TGI, or a compatible server | Hardware use belongs to the computer running that server | Only if its administrator requires one |
| **Whisper (on this machine)** | Multilingual speech recognition with large-v3 | About 3 GB; best local accuracy here, but slower without a suitable GPU | None |
| **Nemotron (on this machine)** | Streaming recognition in 40 languages | About 690 MB; designed to run several times faster than real time on a CPU | None |
| **Piper (on this machine)** | Spoken Pipeline replies in English, German, or Spanish | About 200 MB; faster than real time on CPU, but fewer and less natural voices than hosted services | None |

Sizes are estimates. Choose Nemotron for lower delay and Whisper for accuracy.
Windows, macOS, and Linux can use compatible local packages. A GPU can
speed up Whisper or a Brain model, but is not required for Nemotron or Piper.

## Connect a Local Brain

### Ollama

1. Install and start Ollama.
2. Open **API Keys & Providers > Brain > Ollama (local)**.
3. Keep the default server address for this computer, or enter a trusted
   Ollama host and select **Save**.
4. Under **Models on this machine**, download a model without leaving the app.
   Each entry shows its approximate size, what it is for, and whether it fits
   this computer's memory. Any other model name from Ollama's library can be
   entered directly. A download runs in the background and reports progress.
5. Select a downloaded model, choose **Test**, then **Set active**.

If no model is selected, the app can choose the smallest suitable download. A
text-only model cannot gain unsupported tools or vision: for screen questions
and images, download a model marked as seeing images. The memory note is
advice, not a limit — a graphics card can run a model the note calls tight.

### Another OpenAI-compatible server

1. Start llama.cpp, vLLM, LM Studio, Hugging Face Transformers Serve, TGI, or
   another compatible server and load one model there.
2. Open **Brain > Local server (OpenAI-compatible)**, enter the server address,
   and select **Save**. The app normalizes a copied `/v1` ending.
3. Leave the optional key empty unless that server requires one. If required,
   enter it only in this provider card.
4. Choose **Test**, then **Set active**.

Generic image support is treated as unknown. Use capable Ollama or a hosted
provider for images; tools still depend on the loaded model.

### Recommended llama.cpp preset

For a machine with about 12 GB or more of usable GPU memory, the current
balanced preset is Google's instruction-tuned Gemma 4 12B QAT Q4_0. It is
about 7.2 GB, so it leaves room for context instead of spilling model weights
to system memory. Install the current llama.cpp release:

```powershell
winget install --id ggml.llamacpp --exact
```

On macOS or Linux, use the current package from your package manager or the
official llama.cpp installer. Then start the same OpenAI-compatible endpoint
on every platform:

```text
llama-server -hf google/gemma-4-12B-it-qat-q4_0-gguf:Q4_0 --host 127.0.0.1 --port 8080 --ctx-size 32768 --parallel 1 -ngl 99 --flash-attn on --jinja --alias gemma-4-12b-it-qat-q4_0
```

In **Local server (OpenAI-compatible)**, use
`http://127.0.0.1:8080` and model `gemma-4-12b-it-qat-q4_0`. The matching
optional block ships in `jarvis.toml.example`; the cloud provider remains the
fresh-install default. Users with enough memory can instead serve
`ggml-org/Qwen3.6-35B-A3B-GGUF:Q4_K_M`, but its model file alone is about
20.4 GB, so it is not the right preset for a 16 GB GPU. On a 16 GB card, keep
the single llama.cpp slot above and budget any co-resident Ollama profile
explicitly. If inference becomes unstable, inspect `ollama ps` and stop
unrelated runners with `ollama stop <model>`; unbounded contexts can exhaust
VRAM even when the model weights appear to fit.

## Install Local Speech

1. Select **Pipeline**, open **Voice Input**, and choose Whisper or Nemotron.
2. Select **Install on this machine**. The app remains usable while it works.
3. Wait until both the engine and model show ready, then select **Test** and
   **Set active**.
4. Open **Voice Output** and repeat those steps for Piper.

The card probes the engine and model separately, so incomplete files are not
called ready. A second click joins the same installation. After an error,
check its message and disk space, then select **Try again**.

## Privacy, Fallbacks, and Removal

“Local” describes one model call; it is not an app-wide offline switch.

- Local Voice Input keeps transcription on the device. A failed local
  recognizer does not silently upload the recording merely to recover.
- Ollama prompts stay on the selected computer or self-hosted host. Web, MCP,
  plugin, and Computer Use tools can still contact outside services.
- Piper creates audio locally, but its reply may come from a hosted Brain.
- Dictation's automatic wording choice stays local with local recognition. An
  explicitly selected cloud polish or translation provider can receive text.
- A failed local Brain moves quickly to another ready provider family. If that
  fallback is hosted, the request may leave the device. Ollama is therefore
  not an airplane-mode setting.

Review every **Active** badge, including tools, Realtime, agents, wording, and
UltraWiki, before calling a workflow fully local.

Weak laptops can use a trusted server. Headless hosts can serve text,
but cannot offer normal voice without desktop audio devices.

To stop using a model, first activate and test another provider. Remove
server-managed models through that server's manager, never during a request.
The app currently has no one-model removal action for Whisper, Nemotron, or
Piper. Leave them after switching and use **Help** for guided cleanup instead
of deleting runtime folders behind the running app.

## How It Fits Together

| Feature | Relationship | Boundary to remember |
|---|---|---|
| **Dictation** | Whisper or Nemotron turns speech into text locally. | Polish and translation have a separate local or cloud choice. |
| **Voice conversations** | Pipeline combines Voice Input, the Brain, and Voice Output. | Realtime bypasses all three selections. |
| **API Keys & Providers** | Shows installation, readiness, server address, test, and active state. | Making one category local does not change the others. |
| **UltraWiki** | Ollama can create embeddings and distill new items locally. | Its embedding model is separate from the Brain and never changes by fallback. Changing it rebuilds the index. |
| **Privacy and local data** | Local inference reduces data sent to model vendors. | History, connected sources, tools, and LAN servers keep their own storage and network rules. |

UltraWiki locks its embedding provider because different models produce
incompatible search values. If local Ollama embeddings stop, semantic indexing
pauses while keyword and time search remain available; the app does not
silently mix in cloud embeddings.

## Check That It Works

1. Test and activate the local Brain, then send a short message in **Chats**.
2. In Pipeline, dictate one sentence and check its raw Dictation history.
3. Start a new Pipeline voice conversation and request a short Piper reply.
4. If UltraWiki uses Ollama, test its embedding slot under **UltraWiki >
   Settings**.

Test each path separately. A working Brain does not prove that speech, tools,
or UltraWiki is local.

## Troubleshooting

| What you see | Likely cause | What to do |
|---|---|---|
| Engine installed, model not ready | Missing or incomplete model files | Keep the app open, check space and the error, then select **Try again**. |
| Ollama test reports no model | Ollama is running with no downloaded model | Download one under **Models on this machine** on the Ollama card, then retest. |
| Self-hosted server is unreachable | Stopped server, wrong address, or blocked network | Restart it, copy its address, select **Save**, and retest. |
| Chat works, but an action or image fails | The model lacks tools or vision | Choose a capable Ollama model or another suitable provider. |
| Replies are very slow | The model is too large for available memory or compute | Choose a smaller Brain, use Nemotron for faster speech, or move the server. |
| Local speech is active but unused | Realtime or an old Pipeline session is still running | Select Pipeline, end the voice session, and start a new one. |
| You still see network use | Another slot, fallback, tool, wording choice, or UltraWiki is hosted | Review all active cards and test the exact feature again. |

## Next Steps

- Read [Providers and API Keys](providers-and-api-keys) for capabilities and
  cross-family fallback.
- Set up [Dictation](dictation) or [Voice Conversations](voice-conversations).
- Configure local embeddings in [UltraWiki](ultrawiki).
- Review [Privacy and Local Data](privacy-and-local-data) before treating a
  workflow as offline.
