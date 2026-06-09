/**
 * Share-Stats image export — capture a styled DOM card to a PNG entirely in
 * the browser and route it to the three share actions (Copy / Save / X).
 *
 * Cloud-first: no server round-trip, one tiny dependency (html-to-image). The
 * honest X limitation (intent URLs cannot attach an image) is handled by the
 * fallback chain in {@link shareToX}.
 */
import { toBlob } from "html-to-image";

export const REPO_URL = "https://github.com/PersonalJarvis/PersonalJarvis";
export const REPO_LABEL = "github.com/PersonalJarvis/PersonalJarvis";

export interface ShareStats {
  userWords: number;
  jarvisWords: number;
  conversationHours: number;
  sessionCount: number;
  longestStreak: number;
}

/** Reject a promise if it has not settled within ``ms`` — so a stalled font
 * fetch can never freeze the dialog on "Generating…" forever. */
function withTimeout<T>(p: Promise<T>, ms: number, label: string): Promise<T> {
  return Promise.race([
    p,
    new Promise<T>((_, reject) =>
      setTimeout(() => reject(new Error(`${label}: timeout after ${ms} ms`)), ms),
    ),
  ]);
}

/**
 * Capture a DOM node to a PNG Blob at high pixel density. Waits for web fonts
 * so the first export embeds Space Grotesk instead of falling back to a system
 * font, and renders on a solid background (never transparent — bad for social).
 * Bounded by a 12 s timeout so a blocked font/image fetch fails loudly instead
 * of hanging the dialog.
 */
export async function renderCardBlob(node: HTMLElement): Promise<Blob> {
  if (typeof document !== "undefined" && document.fonts?.ready) {
    try {
      await withTimeout(document.fonts.ready, 4000, "share-image fonts");
    } catch {
      /* Font Loading API absent (jsdom) or slow — proceed with what we have */
    }
  }
  const blob = await withTimeout(
    toBlob(node, {
      pixelRatio: Math.max(2, Math.round(globalThis.devicePixelRatio || 1)),
      cacheBust: true,
      backgroundColor: "#0b0b0f",
    }),
    12000,
    "share-image",
  );
  if (!blob) throw new Error("share-image: empty blob");
  return blob;
}

/**
 * Copy a PNG to the clipboard. The image is passed as a Blob (pre-rendered) or
 * a still-pending ``Promise<Blob>``; either way it is handed straight to
 * {@link ClipboardItem} so ``clipboard.write`` stays inside the user gesture
 * (Safari-safe, and avoids Chrome rejecting a write that resolves too late).
 * Returns ``"unsupported"`` when the browser lacks image clipboard support.
 */
export async function copyImageToClipboard(
  image: Blob | Promise<Blob>,
): Promise<"copied" | "unsupported"> {
  if (
    typeof ClipboardItem !== "undefined" &&
    typeof navigator !== "undefined" &&
    navigator.clipboard?.write
  ) {
    try {
      const item = new ClipboardItem({ "image/png": image });
      await navigator.clipboard.write([item]);
      return "copied";
    } catch {
      /* fall through — caller downloads instead */
    }
  }
  return "unsupported";
}

/** Factual, link-free tweet body. The repo URL travels in the intent ``url``. */
export function buildShareText(stats: ShareStats): string {
  const nf = (n: number) => n.toLocaleString("en-US");
  return (
    `I've spoken ${nf(stats.userWords)} words to my Personal Jarvis across ` +
    `${nf(stats.sessionCount)} conversations — ${stats.conversationHours.toFixed(1)} h of voice. ` +
    `Build your own:`
  );
}

export type ShareToXResult =
  | "shared" // native share sheet completed (image included)
  | "dismissed" // user cancelled the native sheet — no further action
  | "composer" // intent composer opened; image is on the clipboard to paste
  | "blocked" // popup blocked (e.g. WebView2 default) — image still copied
  | "error"; // image render failed

/**
 * Share to X with the best path each platform allows:
 *   1. Mobile / capable browsers → Web Share API WITH the image file.
 *   2. Desktop → copy the image to the clipboard, then open the prefilled
 *      ``/intent/tweet`` composer; the user pastes the image (Ctrl/Cmd+V).
 * X intent URLs cannot attach an image, so (2) is the only desktop option.
 * If the popup is blocked (the pywebview/WebView2 shell does this), the image
 * is still on the clipboard and we report ``"blocked"`` so the dialog can tell
 * the user to open X manually — never a misleading image-error.
 */
export async function shareToX(
  blob: Blob,
  text: string,
): Promise<ShareToXResult> {
  const file = new File([blob], "jarvis-stats.png", { type: "image/png" });
  const nav = navigator as Navigator & {
    canShare?: (data: { files?: File[] }) => boolean;
  };
  if (nav.canShare?.({ files: [file] }) && typeof navigator.share === "function") {
    try {
      await navigator.share({ files: [file], text: `${text} ${REPO_URL}` });
      return "shared";
    } catch (err) {
      // User dismissed the native sheet — stop, don't pop a second composer.
      if ((err as Error)?.name === "AbortError") return "dismissed";
      // Any other failure falls through to the intent composer below.
    }
  }

  // Desktop fallback: stage the image on the clipboard for pasting.
  try {
    if (typeof ClipboardItem !== "undefined" && navigator.clipboard?.write) {
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
    }
  } catch {
    /* clipboard is best-effort here */
  }

  const params = new URLSearchParams({ text, url: REPO_URL });
  const win = globalThis.open?.(
    `https://twitter.com/intent/tweet?${params.toString()}`,
    "_blank",
    "noopener,noreferrer",
  );
  return win ? "composer" : "blocked";
}
