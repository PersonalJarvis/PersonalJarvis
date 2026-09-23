# Native audio worker (development, not a qualified Jarvis voice default)

This executable keeps one LFM2.5 audio model resident and exchanges JSON lines
over stdin/stdout. It opens no network listener and executes no tools. The
parent application owns audio devices, wake detection, model selection and
permissions. Human-readable upstream logs are disabled because they include
transcripts and would corrupt the control stream.

## Build inputs

- Runner source: `tdakhran/llama.cpp` commit
  `ec9d1fdd9cc18643c5e161b65b8053f6f6f34a4b`.
- Source archive:
  `https://codeload.github.com/tdakhran/llama.cpp/zip/ec9d1fdd9cc18643c5e161b65b8053f6f6f34a4b`.
- Source archive SHA-256:
  `87709e27a0d24b8b67c674de2e0b61c6d5f2f1b9b3548044d5f1067a0bd4efb4`.
- Windows cross-build tested with LLVM-MinGW `20260922` UCRT, Linux x64 host
  archive SHA-256:
  `bb7bb7654b33d5aa8712acb837c963b2e0c56352560c76105270a3268c665c21`.

Verify the archive before extracting it. `JARVIS_LFM_SOURCE` is the extracted
source root. CMake supplies two standard-library headers missing from the pinned
engine's transitive includes on libc++/MinGW; no inference logic is patched.

```text
cmake -S jarvis/realtime/local_runtime/native -B native-build -DJARVIS_LFM_SOURCE=SOURCE_ROOT -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=OFF
cmake --build native-build --config Release --target jarvis-native-audio
```

Platform/toolchain options select CUDA, Metal or CPU in the upstream GGML build.
The Windows reference build used a static x64 CPU binary, Windows 10 target,
Clang's `x86_64-w64-mingw32` cross compiler, `GGML_CUDA=OFF`,
`GGML_OPENMP=OFF`, `BUILD_SHARED_LIBS=OFF` and `-static` linker flags. Native
MSVC, CUDA and Metal builds still need qualification. Ship the engine and
third-party license files with any distributed binary; the repository contains
source only at this stage. Normal users must eventually receive a verified
runtime bundle through the app, not these build instructions.

## Control protocol v1

After model initialization the worker emits `kind=loaded`, its engine revision,
sample rate and explicit `tools=false` / `full_duplex=false`. This proves model
loading only. A real inference probe is still required before voice readiness.

Each request has a unique `id`:

- `command=generate`: `text` and/or base64 `audio_wav`, `reset_context`,
  `instructions`, `output_mode` (`text`, `audio`, `text_audio`) and `max_tokens`.
  The engine accepts at most one turn at a time; competitors receive `busy`.
- Output events carry the same `id`: `text`, `audio` (base64 mono PCM16),
  then exactly one terminal `done`, `cancelled` or `error`.
- `command=cancel`: only the currently active `id` can interrupt inference.
  Cancellation invalidates conversation context; the next turn must reset.
- `command=shutdown`, or closing stdin, stops inference and joins the worker.
  The parent must enforce a kill deadline if a native operation stops responding.

`done` means generation returned normally; it is not proof that a tool ran or
that the answer is factually correct. A text-only request never emits acoustic
JSON/tool proposals even if the underlying model generates audio internally.

## Actual-engine verification

From the repository root, with the Q4 catalog files already acquired:

```text
python scripts/verify_local_native_audio.py --runner WORKER_BINARY --weights WEIGHTS_DIRECTORY --report REPORT.json
```

The command must exit zero and print `LOCAL_NATIVE_AUDIO_OK`. It verifies
synthetic English speech generation, audio-input arithmetic, conversation
context, cancellation, rejection of stale context, recovery and process exit.
It does not test a microphone, wake phrase, German, full duplex, real tool
execution or physical Apple Silicon. The report records this scope explicitly.
