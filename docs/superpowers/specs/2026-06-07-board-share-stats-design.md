# Board "Share Stats" — Design Spec

**Date:** 2026-06-07
**Status:** Approved (design), pending implementation
**Area:** `jarvis/ui/web/frontend` (Board view) — 100% client-side, no server change

## Goal

Let a user share their Personal Jarvis usage as a polished image, in the spirit
of Strava / Duolingo / GitHub-Wrapped recap cards (the user supplied a private
community-card reference). From the Board, a **Share** button opens a dialog showing a
1080×1080 stats card with three actions: **Copy Image**, **Save as PNG**, and
**Share on X**.

This is purely additive and entirely browser-side. It honors the cloud-first
doctrine: no server round-trip, one tiny dependency, works in any modern browser
and in the pywebview/WebView2 desktop shell, degrades gracefully where a browser
API is missing.

## Constraints & honest limitations

- **X cannot attach an image to an intent URL.** X/Twitter Web Intent links
  carry text/url only. The real flow on desktop is: copy (or save) the PNG, then
  open the prefilled composer, then the user pastes the image (Ctrl/Cmd+V). On
  mobile, the Web Share API (`navigator.share({files})`) can include the image
  directly. The "Share on X" action implements both paths with a fallback chain.
- **The private reference card is unverified** (no public screenshot). We build to
  the well-documented recap-card pattern, not a pixel copy.
- **Handle is not hardcoded.** The user's X handle lives in `localStorage`
  (`board.share.handle`), editable in the dialog, empty by default. This keeps
  personal data out of the repo (important for the depersonalized public
  release).

## Card content (single-hero layout, 1080×1080)

```
        ✦  PERSONAL JARVIS
                10,874
              WORDS SPOKEN
        18,712 spoken by Jarvis
        27.9 h talked · 888 chats
        ▓▓▓▓▓▓▓░░  23-day best streak
   @handle (optional) · github.com/PersonalJarvis/PersonalJarvis
```

- **Brand mark:** the original Jarvis desktop icon (`jarvis.ico`, the gold
  sparkle on black) rendered from a new crisp raster `public/jarvis-mark-256.png`.
- **Hero number:** the user's own spoken word count (`totals.user_words`).
- **Supporting stats:** `jarvis_words`, `conversation_hours`, `session_count`,
  `longest_streak` (rendered as a small progress bar + label).
- **Signature line:** optional `@handle` + the project URL
  `github.com/PersonalJarvis/PersonalJarvis`.
- **Style:** exact Board vocabulary — dark gradient, `font-display`, signal-yellow
  (`hsl(50 100% 52%)`) hero, sky accent for the Jarvis stat, `rounded-[20px]`,
  inset-highlight shadow, corner glow blob.

All numbers come from the already-fetched `useBoardSummary()` totals (all-time).
No new endpoint.

## Architecture

### New files

1. **`src/components/board/ShareCard.tsx`**
   - Pure presentational card. `forwardRef<HTMLDivElement>` so the export logic
     can capture the DOM node.
   - Fixed intrinsic size 1080×1080 (the on-screen preview scales it down with a
     CSS `transform: scale(...)` wrapper; the captured node stays at full res).
   - Props: the summary totals + streak + optional handle. No data fetching here.

2. **`src/components/board/ShareDialog.tsx`**
   - Radix Dialog (`@radix-ui/react-dialog`, already installed; pattern from
     `DocsSearchModal.tsx`).
   - Renders the scaled `ShareCard` preview, an editable handle input, and the
     three action buttons with inline status ("Copied ✓", "Saved ✓",
     "Generating…", error text). No external toast dependency.
   - Awaits `document.fonts.ready` on mount so the first export embeds fonts.

3. **`src/lib/shareImage.ts`**
   - `renderCardBlob(node)` — `html-to-image` `toBlob` with
     `pixelRatio: Math.max(2, devicePixelRatio)`, solid `backgroundColor`,
     `cacheBust: true`.
   - `copyImageToClipboard(node)` — Safari-safe `ClipboardItem` (pass the blob
     **promise**, do not await first); returns `'copied' | 'unsupported'`.
   - `saveCardAsPng(node, filename)` — blob → objectURL → `<a download>` → revoke.
   - `shareToX(node, text, url)` — try `navigator.share({files})` first; else
     copy image + `window.open('https://twitter.com/intent/tweet?...')`.
   - `buildShareText(totals)` — factual text incl. the repo URL.

4. **`src/hooks/useShareHandle.ts`**
   - `localStorage`-backed handle (`board.share.handle`), `[handle, setHandle]`.

5. **`src/components/board/ShareDialog.test.tsx`**
   - Vitest + RTL. Mock `html-to-image` and clipboard. Assert: Share button in
     header; dialog opens; three actions present; Copy invokes `clipboard.write`;
     repo URL present on the card.

### Modified files

- **`src/views/BoardView.tsx`** — add a **Share** button (lucide `Share2`) in the
  `ViewHeader` `right` slot next to Refresh; local `open` state; render
  `<ShareDialog>` with the summary data.
- **`src/lib/clipboard.ts`** — add `downloadBlob(filename, blob)` (the existing
  `downloadAs` is string-only). Reused by `saveCardAsPng`.
- **`package.json`** — add `html-to-image` (~5 KB gzip, zero deps).
- **`src/i18n/locales/{en,de,es}.json`** — add `board_view.share.*` keys; English
  is the canonical source (language policy).
- **`public/jarvis-mark-256.png`** — new raster of the sparkle mark (generated
  from `jarvis.ico`'s 256 frame).

## Action flows (fallback chains)

- **Copy Image:** `ClipboardItem` PNG write → on unsupported → `saveCardAsPng` +
  status "Saved the PNG instead — attach it to your post."
- **Save as PNG:** always available (anchor download). Filename `jarvis-stats.png`.
- **Share on X:** `navigator.canShare({files})` + `navigator.share` (mobile, image
  included) → else copy image to clipboard + open
  `https://twitter.com/intent/tweet?text=<text>&url=https://github.com/PersonalJarvis/PersonalJarvis`
  + status "Image copied — paste it (Ctrl/Cmd+V) into your post."

## Error handling

Every action is wrapped; a render failure shows quiet inline error text in the
dialog, never crashes the Board. Image export waits for `document.fonts.ready`
and renders at `pixelRatio ≥ 2` for crisp, font-correct output on a solid
background (never transparent — bad for social).

## Out of scope (YAGNI)

Server-side OG image, multi-slide "Wrapped", story/landscape presets (square only,
per decision), server persistence of the handle, analytics.

## Testing

- New `ShareDialog.test.tsx` (first Board frontend test) per the patterns in
  `WikiSearch.test.tsx` / `ObsidianSetupDialog.test.tsx`.
- `tsc` + `npm run build` clean; `npm run test` green.
- Manual: open Board → Share → preview renders with icon + repo URL → Save PNG
  produces a crisp 1080² file → Copy Image → Share on X opens composer.
