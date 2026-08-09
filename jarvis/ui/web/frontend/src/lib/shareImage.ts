/**
 * Share-Stats image export — capture a styled DOM card to a PNG entirely in
 * the browser and route it to the three share actions (Copy / Save / X).
 *
 * Cloud-first: no server round-trip, one tiny dependency (html-to-image). The
 * honest X limitation (intent URLs cannot attach an image) is handled by the
 * fallback chain in {@link shareToX}.
 */
import { toBlob } from "html-to-image";
import {
  OFFICIAL_REPO_LABEL,
  OFFICIAL_REPO_URL,
  PRODUCT_NAME,
} from "@/lib/branding";

export const REPO_URL = OFFICIAL_REPO_URL;
export const REPO_LABEL = OFFICIAL_REPO_LABEL;

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
    `I've spoken ${nf(stats.userWords)} words to my ${PRODUCT_NAME} across ` +
    `${nf(stats.sessionCount)} conversations — ${stats.conversationHours.toFixed(1)} h of voice. ` +
    `Build your own:`
  );
}

export type ShareToXResult =
  | "shared" // native share sheet completed (image included)
  | "dismissed" // user cancelled the native sheet
  | "composer" // intent composer opened; image is on the clipboard to paste
  | "composer_without_image" // composer opened, clipboard image unavailable
  | "blocked" // popup blocked (e.g. WebView2 default) — image still copied
  | "blocked_without_image"; // popup and clipboard image both unavailable

/**
 * Share to X by opening the prefilled intent composer synchronously, then
 * staging the image on the clipboard for the user to paste (Ctrl/Cmd+V).
 * X intent URLs cannot attach an image directly.
 *
 * Opening the composer MUST happen before the first ``await``. Otherwise the
 * browser no longer considers it part of the click gesture and blocks the
 * popup while the share card is still rendering.
 * If the popup is blocked (the pywebview/WebView2 shell does this), the image
 * is still on the clipboard and we report ``"blocked"`` so the dialog can tell
 * the user to open X manually — never a misleading image-error.
 */
export async function shareToX(
  image: Blob | Promise<Blob>,
  text: string,
): Promise<ShareToXResult> {
  // A pre-rendered Blob lets the native share sheet start synchronously inside
  // the click gesture while still including the image. A pending render cannot
  // use this route because creating the File after awaiting it loses activation.
  if (image instanceof Blob) {
    const file = new File([image], "jarvis-stats.png", { type: "image/png" });
    const nav = navigator as Navigator & {
      canShare?: (data: { files?: File[] }) => boolean;
    };
    if (nav.canShare?.({ files: [file] }) && typeof navigator.share === "function") {
      try {
        await navigator.share({ files: [file], text: `${text} ${REPO_URL}` });
        return "shared";
      } catch (error) {
        if ((error as Error)?.name === "AbortError") return "dismissed";
        // A synchronous native-share refusal can still fall back to X.
      }
    }
  }

  const params = new URLSearchParams({ text, url: REPO_URL });
  const intentUrl = `https://twitter.com/intent/tweet?${params.toString()}`;
  let win: Window | null = null;
  const candidate = globalThis.open?.("", "_blank") ?? null;
  if (candidate) {
    try {
      // Opening about:blank gives us a truthful popup-blocker signal. Sever
      // the opener while it is still same-origin, then navigate only after the
      // new page can no longer reach back into the Jarvis window.
      candidate.opener = null;
      candidate.location.replace(intentUrl);
      win = candidate;
    } catch {
      // A handle that cannot be secured or navigated is not a usable composer.
      try {
        candidate.close();
      } catch {
        /* best-effort cleanup of the blank window */
      }
    }
  }

  // The composer and ClipboardItem are both created before the first await so
  // the browser still recognises them as consequences of the button click.
  const copy = copyImageToClipboard(image);
  await Promise.resolve(image);
  const copyResult = await copy;
  if (win) {
    return copyResult === "copied" ? "composer" : "composer_without_image";
  }
  return copyResult === "copied" ? "blocked" : "blocked_without_image";
}
