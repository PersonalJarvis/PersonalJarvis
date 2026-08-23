# Bundled provider marks

The **original** mark of each AI provider family, bundled so the API-Keys &
Providers screen renders offline and on a headless host without calling any
third party at render time. Same rules and same gate as the plugin marks in
`../brands/LOGOS.md` (`scripts/ci/check_brand_logos.py` covers both folders).

Every mark belongs to its owner. They are used **solely to identify the service
a provider card connects to** — nominative use — and never to imply that the
owner endorses, sponsors or is affiliated with this project. If you own a mark
listed here and want it removed, open an issue and it will be taken out.

> A CC0 or MIT licence on an SVG settles the **copyright in the drawing**. It
> does not grant **trademark** rights, and no entry below claims otherwise.

## How a card picks a mark

`ProviderLogo.tsx` maps a provider id to a **family** (every Gemini card —
brain, STT, TTS, Live — draws the one Gemini mark) and the family to a file
here. A family with no file, and the on-device engines (Whisper, Piper,
Nemotron, a self-hosted server) that are capabilities rather than brands, draw
a neutral glyph or monogram on the same tile, so adding a provider never
produces a broken image.

Two render paths, chosen per file and recorded in the table:

* **colour** — the vendor's full-colour mark (Gemini's gradient spark, the
  Google Cloud hexagon, NVIDIA's green eye). Drawn as an `<img>` on a neutral
  tile; the colours are the brand and are never recoloured.
* **mono** — a single-colour glyph whose own brand is black-on-white or
  white-on-black (OpenAI, xAI, OpenRouter, Groq, Ollama, ElevenLabs). Drawn as
  a CSS mask over the current text ink, so it follows the theme by itself:
  near-white on the dark app, near-black on paper, from one asset.
* **own ground** — a lockup that carries its own background (Cartesia's green
  square, Inworld's transparent PNG). Drawn as an `<img>` untouched.

## Adding one

1. Take the mark from the vendor's own brand/press page, or from a
   permissively-licensed collection that redistributes vendor originals
   (lobehub/lobe-icons is MIT and covers nearly every AI provider).
2. Prefer the **icon** variant over the wordmark.
3. Strip scripts, external references and embedded raster images; keep a
   roughly square `viewBox`. Root-level `width`/`height`/`style` go too — the
   tile sizes the mark.
4. Save it as `<family>.svg` and add the family to `PROVIDER_FAMILY_LOGOS` in
   `ProviderLogo.tsx`.
5. Add a row below. An entry without a row is a licence gap, not a shortcut.

| family | Source | Legal basis | Render | Added |
| --- | --- | --- | --- | --- |
| antigravity | lobehub/lobe-icons `antigravity-color.svg` | MIT | colour | 2026-08-23 |
| cartesia | Cartesia's own site icon, `https://cartesia.ai/favicon.svg` | vendor asset | own ground | 2026-08-23 |
| claude | lobehub/lobe-icons `claude-color.svg` | MIT | colour | 2026-08-23 |
| elevenlabs | lobehub/lobe-icons `elevenlabs.svg` | MIT | mono | 2026-08-23 |
| gemini | lobehub/lobe-icons `gemini-color.svg` | MIT | colour | 2026-08-23 |
| google-cloud | lobehub/lobe-icons `googlecloud-color.svg` (Vertex AI cards) | MIT | colour | 2026-08-23 |
| groq | lobehub/lobe-icons `groq.svg` | MIT | mono | 2026-08-23 |
| inworld | Inworld's own site icon, `https://inworld.ai/icon.png` (PNG, 180 px) | vendor asset | own ground | 2026-08-23 |
| nvidia | lobehub/lobe-icons `nvidia-color.svg` | MIT | colour | 2026-08-23 |
| ollama | lobehub/lobe-icons `ollama.svg` | MIT | mono | 2026-08-23 |
| openai | lobehub/lobe-icons `openai.svg` (also Codex cards) | MIT | mono | 2026-08-23 |
| openrouter | lobehub/lobe-icons `openrouter.svg` | MIT | mono | 2026-08-23 |
| xai | lobehub/lobe-icons `grok.svg` (xAI Grok cards) | MIT | mono | 2026-08-23 |
