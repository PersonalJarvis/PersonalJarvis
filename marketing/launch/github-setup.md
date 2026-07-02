# GitHub launch setup — manual steps

Everything in this file must be clicked by a maintainer; none of it can (or
should) be automated. Order doesn't matter.

## 1. Social preview image

The rendered card lives at **`assets/brand/social-preview.png`** (1280×640,
~230 KB — within GitHub's 1 MB limit). Source: `assets/brand/social-preview.html`,
re-render with
`powershell assets/brand/render.ps1 -In social-preview.html -Out social-preview.png -W 1280 -H 640 -Scale 2`
(then downscale the 2× shot to 1280×640).

Where to set it: **github.com/PersonalJarvis/PersonalJarvis → Settings →
General → Social preview → Edit → Upload an image**. This image appears in
every link preview (X, Discord, Slack, HN comments) — set it *before* the
launch posts go out.

## 2. Repository topics

Settings → General → Topics (the gear next to "About"). Suggested set
(GitHub allows 20; these 16 cover search + discovery):

```
voice-assistant  ai-agent  ai-assistant  jarvis  voice-control
speech-recognition  agents  multi-agent  llm  automation
self-hosted  privacy  computer-use  mcp  whisper  python
```

Also in the About box: set the description to the hero one-liner
("Talk to your computer — and watch it do the work. Open-source, privacy-first
voice agent.") and the website to the Discord invite or project site.

## 3. Good first issues

Create the issues from `marketing/launch/good-first-issues.md` (sanity-check
each against current `main` first), label them `good first issue` + an area
label — the CONTRIBUTING badge already links to that label filter.

## 4. Optional: native video player in the README

GitHub renders committed GIFs inline (that's what ships now), but a *native
video player* only works with GitHub-uploaded assets: edit the README **in the
GitHub web editor**, drag `assets/video/personal-jarvis-demo.mp4` into the
editor, and it uploads to `github.com/user-attachments/...` and embeds a real
player with sound. If you do that, replace the GIF `<img>` + MP4 link with the
generated attachment URL. Purely optional — the GIF autoplays and needs no click.

## 5. Posting the launch drafts

- `show-hn.md` — post text + prepared answers.
- `reddit-r-localllama.md`, `reddit-r-selfhosted.md` — one per subreddit; keep
  a few hours apart so comments can be answered properly.
- `x-thread.md` — attach the video to tweet 1. For best X quality upload the
  4K master (render lives at `jarvis-promo-video/out/readme-hero-4k.mp4`;
  re-render with `npx remotion render src/index.ts PersonalJarvisReadme
  out/readme-hero-4k.mp4 --scale 2 --crf 16` inside `jarvis-promo-video/`).

Nothing in `marketing/launch/` is published automatically — every post needs a
human click, deliberately.
