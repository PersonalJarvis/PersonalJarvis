# Report-Tutorial — Secrets/PII review answers (LOCAL investigation)

> Produced by the LOCAL full-working-tree session in response to
> `reporttutorialpickupprompt (1).md`. Read-only investigation — nothing was
> modified, committed, pushed, or shipped. Evidence is given as `path:line`
> where relevant.

## TL;DR (read this first)

- **The "Report-Tutorial" is the `video/` Remotion project** — the source for the
  **Personal Jarvis onboarding tutorial video** that ends up on YouTube. The
  "Report" in the name is the sub-agents demo scene where the user asks for a
  **"deep-dive report on the global EV market"** and Jarvis delivers
  `ev-market-report.pdf`.
- **The git repo is safe.** The entire `video/**` tree is on the distribution
  denylist and partly `.gitignore`d, so **none of it ever reaches the public
  PersonalJarvis repo.** That is exactly why the depersonalized cloud session
  couldn't find it.
- **The real public surface is the YouTube video + thumbnail**, not git. Those are
  mostly clean: no API key, no token, no email, no desktop, no foreign windows.
  The first name "Alex" is shown on purpose (he introduces himself by name).
- **One genuine, actionable finding:** the in-video GitHub screenshot
  (`video/public/shot-github.png`, used in the CreatorIntro scene) shows the **real
  surname "Personal Jarvis Maintainer"** and GitHub login **"octocat"** in the  <!-- i18n-allow -->
  contributor list — readable at 4K / on pause. It is also **stale** (the public
  history has since been anonymized, so the video would show data that no longer
  exists in the live repo).
- **One secondary, non-video finding:** a stray ShareX capture
  `video/pythonw_xpdq2qZtJg.png` shows the **private email
  `maintainer@example.com`** twice. It is `.gitignore`d and is **NOT** used in
  the film, but it is sitting in `video/` and should be removed / ShareX repointed.

---

## A — Identify the artifact

### 1. What exactly is the "Report-Tutorial"? Exact path(s).

The **`video/` Remotion project** — the build that renders the onboarding tutorial
film. Concretely:

- Whole tree: `video/` (115 git-tracked non-`node_modules` files).
- Main deliverable: `video/out/onboarding.mp4` and `video/out/onboarding-4k.mp4`
  (the 4K upload master).
- Composition source: `video/src/intro/OnboardingVideo.tsx` +
  `video/src/intro/onboarding/scenes/*.tsx`.
- Real-app screenshots shown in it: `video/public/shot-*.png`,
  `video/public/app-home.png`.
- Voiceover script + audio: `video/vo-script.json`, `video/public/vo/*.mp3`.
- Review frames: `video/analysis/*.png`.

The literal string "Report-Tutorial" appears **only** in the pickup prompt
(`reporttutorialpickupprompt (1).md`), so it is Alex's informal label, not a
filename.

### 2. What kind of thing is it (one line)?

It is the **onboarding tutorial video** for Personal Jarvis — and it itself
contains a **report-generation demo**: the sub-agents scene asks for a *"deep-dive
report on the global EV market"* and delivers `ev-market-report.pdf`
(`video/vo-script.json:85`; `video/src/intro/onboarding/scenes/SubAgentsYT.tsx`).
That tutorial-that-demos-a-report is the "Report" + "Tutorial" combination.

### 3. Git-tracked, untracked, or generated at runtime?

**Git-tracked source, but denylisted from the public snapshot.**

- Tracked: 115 files under `video/` (TSX, JSON, screenshots, VO audio, analysis
  frames).
- Withheld from public ship: `video/**` is on the distribution denylist —
  `scripts/ci/privacy_gate/references/distribution-denylist.txt:134`
  (rationale lines 126–133).
- Additionally `.gitignore`d (never tracked at all): the rendered films
  (`git check-ignore video/out/onboarding.mp4` → IGNORED) and the stray ShareX
  PNGs in the `video/` root (`video/pythonw_xpdq2qZtJg.png` → IGNORED).

### 4. Format/type.

Mixed project: TypeScript/TSX (Remotion), JSON (VO script + generated timeline),
PNG screenshots, MP3 voiceover, and rendered MP4 films.

### 5. Last modified; hand-written or generated?

Active **late June 2026**. Onboarding scenes edited through ~Jun 27;
`video/out/onboarding.mp4` + `onboarding-4k.mp4` rendered **2026-06-27 09:27**;
thumbnails **2026-06-27 10:00**. The TSX scenes and the VO script are
**hand-authored**; the screenshots are **hand-captured** from the real app; the
MP4 films and the `timeline.json` are **generated** output.

---

## B — Publication intent

### 6. Public repo, or private/local only?

**Not the public repo.** The `video/` project is a build tool that stays private.
The *finished film* is meant to be **public on YouTube** — Alex uploads it himself
and the app embeds it by link from youtube-nocookie.com
(`IntroVideoScreen.tsx`), it is never bundled. So: source project = private;
rendered film = intentionally public (that is its whole purpose).

### 7. On the denylist / in `.gitignore`?

**Both.**

- Denylist: `video/**` —
  `scripts/ci/privacy_gate/references/distribution-denylist.txt:134`.
- `.gitignore`: rendered `video/out/*.mp4` and root-level `video/*.png`
  screenshots are ignored (confirmed via `git check-ignore`).

This double exclusion is why the public-snapshot cloud reviewer had no files to
look at.

---

## C — Leak surface (the actual review)

### 8. Does it embed real data?

Yes — by design it shows **real app screenshots** and a **real GitHub screenshot**,
the voiceover names "Alex … a developer from Germany", and the wake word is
"Hey Alex". One stray (non-video) screenshot also embeds the private email. No
logs, transcripts, or example API responses with secrets.

### 9. Per-category search results (every hit with evidence)

**Maintainer real name (Maintainer / Maintainer + first name)**  <!-- i18n-allow -->
- Surname "Personal Jarvis Maintainer": **only in pixels** of `video/public/shot-github.png`  <!-- i18n-allow -->
  (contributor list). **Zero** hits as text in any tracked `video/` file
  (full-text scan of all tracked non-binary files returned no `Maintainer`/`Maintainer`;  <!-- i18n-allow -->
  the one `maintainer` match was a false positive — "queue-microtask" in
  `video/package-lock.json:4264`).
- First name "Alex": text in 11 tracked files — `video/vo-script.json:15,16,53`
  and scene files (`CreatorIntro.tsx`, `SetupWake.tsx`, `Examples.tsx`,
  `ComputerUseYT.tsx`, `SubAgentsYT.tsx`, `SpokenCommand.tsx`,
  `generated/timeline.json`, plus the older `scenes/MorningOverview.tsx`,
  `VoiceChat.tsx`, `WakeWord.tsx`); also in pixels of `shot-apikeys.png`,
  `shot-wake-crop.png`, `shot-outputs.png`. **This is intentional** — he introduces
  himself by first name in the film.

**Personal email(s)**
- `maintainer@example.com`: **only in pixels** of
  `video/pythonw_xpdq2qZtJg.png` (shown twice — "Connected as …" on the OpenAI
  Codex and Antigravity rows). This file is `.gitignore`d and is **not referenced
  by any scene**, so it is **not in the film**. Zero email hits as text in tracked
  files.

**GitHub login**
- `octocat`: in pixels of `shot-github.png` (commit-author line +
  contributor list). Not present as text.

**C:\Users\<name> / personal filesystem paths** — none found in tracked `video/`
text files.

**Machine/user identifiers, Windows SIDs (S-1-5-21-…)** — none.

**Internal project ids** — none. (`shot-outputs.png` shows a mission UUID
`mission_019efb1e-dfcc`, but that is a random run id, not an internal project id,
and that screenshot is not used in the film.)

**Private IPs (192.168.* / 10.* / 172.16–31.*)** — none.

**API keys / tokens / private-key blocks / JWTs** — **none in clear text.**
`shot-apikeys.png` shows the Claude key **masked** (dots) and the OpenAI field
**empty** ("Enter openai_api_key…"). No visible token anywhere.

### 10. Screenshots / images — do they show secrets?

- `video/public/shot-github.png` — **shows the real name "Personal Jarvis Maintainer" + login  <!-- i18n-allow -->
  "octocat"** (contributor list). **Appears in the film** (CreatorIntro,
  `video/src/intro/onboarding/scenes/CreatorIntro.tsx:29`, displayed near native
  width → readable at 4K). **Primary finding.**
- `video/pythonw_xpdq2qZtJg.png` — shows the **private email** twice. **Not in the
  film**; stray ShareX capture, `.gitignore`d.
- `video/public/shot-outputs.png` — shows an internal **German mission prompt**
  ("…That was just rigid. Could you do a deep dive?…") + first name.
  **Not referenced by any scene → not in the film.**
- `video/public/shot-apikeys.png` — first name in the sidebar; key **masked**, no
  token. In the film (SetupKeys).
- `video/public/shot-wake-crop.png` — "Hey Alex" / "called: Alex" (first name).
  In the film (SetupWake). Intentional.
- `video/public/app-home.png` — **cleanly depersonalized**: shows "Jarvis" /
  "Hey Jarvis", no personal data. In the film (RealApp).
- `video/public/personal-jarvis-onboarding-thumbnail.png` (public YouTube
  thumbnail) — only the first name in the "Hey Alex …" command cards; no email,
  token, surname, or desktop. Clean.
- `video/analysis/*.png` (48 review frames) — spot-checked `int_01.png`
  ("Works with your world" plugin grid) and `k_01.png` ("Personal Jarvis" title) —
  clean, no PII. The denylist flags these as possibly carrying personalized demo
  text in pixels, which is why the whole tree is withheld.

No screenshot shows the desktop wallpaper, foreign window titles, the taskbar, or
other-app content — they are all in-app captures.

### 11. Embedded URLs / IDs — intended-public or accidental?

- YouTube video id `FXz1HclXL1g` lives in the **frontend** (`IntroVideoScreen.tsx`),
  not in the `video/` project — **intended public** (the published onboarding
  video).
- `https://discord.gg/6VzzNDwUwV` in `video/README.md:46` is **Remotion's official
  template Discord** (boilerplate from the starter), **not** Alex's server — not
  sensitive.
- The plugins scene names "Gmail, Calendar, Telegram, Discord, Spotify, GitHub"
  only as **brand labels** — no invite links, no handles.
- The outro says *"the link is in the description below"* — no hard-coded URL in
  the video.
- Other URLs found are npm/eslint sponsor links in `video/package-lock.json` —
  boilerplate, non-sensitive.

---

## D — Scope

### 12. One file or a whole directory? Everything in scope.

A **whole directory: `video/`**. But the part that actually reaches the public
(the rendered YouTube film + thumbnail) is a small subset:

**In the finished film (real public surface):**
`shot-github.png` (CreatorIntro), `shot-apikeys.png` (SetupKeys),
`shot-wake-crop.png` (SetupWake), `app-home.png` (RealApp), the VO audio
(`video/public/vo/*.mp3`), and the thumbnail.

**Tracked but NOT in the film (lower urgency — withheld from public anyway):**
`shot-outputs.png`, `shot-settings.png`, `shot-wake.png`, the 48 `analysis/*.png`
review frames, the TSX/JSON source, and the older IntroVideo scenes.

**Not in the film and `.gitignore`d:** `pythonw_xpdq2qZtJg.png`,
`video/out/*.mp4`.

### 13. Anything the `pii-scrub.tsv` manifest would mask? (ship-gate result)

**Important nuance:** because `video/**` is on the distribution denylist, the real
ship gate **never exports or scans it** — it is cut at step 1 (tracked-files-only)
+ the denylist. So the scanner output for these files is "not shipped", by design.

Applying the `pii-scrub.tsv` rules **manually** to the tracked *text* would mask:
- `Personal Jarvis Maintainer` → `Personal Jarvis Maintainer`  <!-- i18n-allow -->
- `Alex` → `Alex` (e.g. `video/vo-script.json:15,16,53`)
- `Maintainer` → `Maintainer`  <!-- i18n-allow -->
- `octocat` → `octocat`
- `maintainer@example.com` → **block-only** (would block a ship if it appeared
  in a non-exempt shipping file)

**Critical limitation:** the deterministic scrubber is **text-only**. It does **not**
see PII baked into PNG pixels — the real name in `shot-github.png` and the email in
`pythonw_xpdq2qZtJg.png` would **survive** a text scrub untouched. That is precisely
why the maintainer chose to denylist the entire `video/` tree rather than rely on
the scrubber (denylist rationale, distribution-denylist.txt:131–133).

### 14. Specific recent change/commit that triggered this concern?

The **upcoming public YouTube upload** of the onboarding film. The 4K master and
thumbnail were rendered 2026-06-27 (`video/out/onboarding-4k.mp4`,
`personal-jarvis-onboarding-thumbnail*.png`), and the wake-word debug audio shows
Alex actively rehearsing the voiceover ("In this short onboarding video I'll show
you…"). The concern is therefore not about the git repo (protected) but about
**what a YouTube viewer can see/hear** in the published film and thumbnail.

---

## Recommendation (what to actually do before publishing)

1. **Re-shoot or redact the GitHub screenshot.** `shot-github.png` is the only
   genuine accidental leak in the published film: it shows the real surname and
   GitHub login, and it is stale (the public history was anonymized afterwards, so
   it shows data that no longer exists live). Either recapture it from the
   anonymized repo, blur the commit-author line + contributor sidebar, or
   consciously accept the surname being public.
2. **Remove the stray ShareX capture.** `video/pythonw_xpdq2qZtJg.png` embeds the
   private email; delete it and repoint ShareX out of the repo (a known issue —
   stray `pythonw_*.png` are ShareX captures, not a Jarvis artifact).
3. **Everything else is fine to ship as-is.** First name "Alex" / "Hey Alex",
   the masked-key API screenshot, the clean `app-home.png`, and the thumbnail
   carry no secrets and reflect the maintainer's intended public identity.
4. **Git side needs no action** — `video/**` is denylisted + partly gitignored and
   cannot reach the public repo.
