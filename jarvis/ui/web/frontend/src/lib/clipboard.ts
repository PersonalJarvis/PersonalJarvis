/**
 * Robust clipboard + download helper.
 *
 * Background: embedded WebViews do not expose one dependable browser clipboard
 * path. WebView2 can truncate multi-line text, while WKWebView can reject both
 * ``navigator.clipboard.writeText`` and ``execCommand('copy')`` after the click
 * handler awaited an export request and lost its transient user activation.
 *
 * Solution: robustCopy() tries the modern API, then a selected hidden textarea,
 * then a desktop-only REST fallback that writes through the operating system.
 * The REST route is disabled on a browser/headless server, so those surfaces
 * remain scoped to the user's browser clipboard.
 */

/**
 * Robustly copies a string to the system clipboard.
 *
 * Returns true if any path succeeded, false if all paths failed.
 */
export async function robustCopy(text: string): Promise<boolean> {
  // Path A — modern clipboard API. Fast and reliable for short texts.
  try {
    if (
      typeof navigator !== "undefined" &&
      navigator.clipboard &&
      typeof navigator.clipboard.writeText === "function"
    ) {
      await navigator.clipboard.writeText(text);
      // WebView2 quirk: writeText() resolves even when only a partial
      // string was applied. We can't reliably read the result back
      // (readText needs permissions that pywebview doesn't grant by
      // default). So for multi-line content, always run the fallback
      // too — it overwrites the corrupted entry with the full text
      // if needed.
      if (text.includes("\n")) {
        if (execCommandCopy(text)) return true;
        return nativeBackendCopy(text);
      }
      return true;
    }
  } catch {
    // Path A failed — fall through to the fallback.
  }
  // Path B — classic fallback via a hidden textarea.
  if (execCommandCopy(text)) return true;
  // Path C — local desktop backend. It is intentionally unavailable on a
  // browser/headless server, where writing would target the wrong machine.
  return nativeBackendCopy(text);
}

async function nativeBackendCopy(text: string): Promise<boolean> {
  if (typeof fetch !== "function") return false;
  try {
    const response = await fetch("/api/clipboard/text", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!response.ok) return false;
    const result = (await response.json().catch(() => null)) as {
      copied?: unknown;
    } | null;
    return result?.copied === true;
  } catch {
    return false;
  }
}

function execCommandCopy(text: string): boolean {
  if (typeof document === "undefined") return false;
  const ta = document.createElement("textarea");
  ta.value = text;
  // Off-screen, so there's no layout shift / focus loss for the user.
  ta.style.position = "fixed";
  ta.style.left = "-9999px";
  ta.style.top = "0";
  ta.style.opacity = "0";
  ta.setAttribute("readonly", "");
  document.body.appendChild(ta);
  // Important: focus + select must happen before execCommand runs,
  // otherwise WebView2 has nothing to copy.
  ta.focus();
  ta.select();
  ta.setSelectionRange(0, text.length);
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  document.body.removeChild(ta);
  return ok;
}

/**
 * Trigger a file download via a Blob + a temporary ``<a download>``.
 *
 * WARNING: inside the desktop shell (pywebview/WebView2) such a browser
 * download is **silently dropped** by default — pywebview ships with
 * ``settings['ALLOW_DOWNLOADS']=False`` and its EdgeChromium handler aborts
 * with ``args.Cancel=True`` (no file, no error). On the desktop the save must
 * therefore go through ``saveOrDownload`` to the backend
 * (``/api/downloads/save`` → ~/Downloads). This path is correct only in a real
 * browser (Vite dev / headless VPS), where the browser itself drops the file
 * into the user's Downloads folder.
 */
export function downloadAs(
  filename: string,
  content: string,
  mime = "text/plain;charset=utf-8",
): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    // Keep it off-screen so there's no visual flicker on click.
    a.style.position = "fixed";
    a.style.left = "-9999px";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  } finally {
    // Release the object URL once the download stream had time to start.
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}

/**
 * Triggers a file download from a Blob (binary-safe — unlike
 * ``downloadAs``, which takes a string). Same hidden-anchor pattern;
 * used for the PNG export of the share card.
 */
export function downloadBlob(filename: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.style.position = "fixed";
    a.style.left = "-9999px";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}

/**
 * Builds a filesystem-safe filename for a voice session.
 *
 * Pattern: ``voice-session-YYYY-MM-DD_HH-mm-{slug}.{ext}``
 *  - YYYY-MM-DD_HH-mm from session.started_ms (local time)
 *  - slug from the first 3-4 words of the first user utterance if
 *    present, otherwise the first 8 characters of the session_id
 *
 * Filesystem sanitizing: only ``[a-z0-9-]`` plus hyphens.
 */
export function buildSessionFilename(
  session: { id: string; started_ms: number },
  preview: string,
  format: "markdown" | "plain" | "json",
): string {
  const ext = format === "markdown" ? "md" : format === "plain" ? "txt" : "json";
  const dt = new Date(session.started_ms);
  const pad = (n: number): string => String(n).padStart(2, "0");
  const stamp = `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}_${pad(dt.getHours())}-${pad(dt.getMinutes())}`;
  const slug = slugify(preview) || session.id.slice(0, 8);
  return `voice-session-${stamp}-${slug}.${ext}`;
}

function slugify(s: string): string {
  if (!s) return "";
  return s
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "") // strip diacritics
    .replace(/[^a-z0-9\s-]/g, " ") // turn all non-alnum into a space
    .trim()
    .split(/\s+/)
    .slice(0, 4) // max 4 words
    .join("-")
    .slice(0, 40); // hard cap
}

/**
 * MIME type for an export format.
 */
export function mimeFor(format: "markdown" | "plain" | "json"): string {
  if (format === "json") return "application/json;charset=utf-8";
  if (format === "markdown") return "text/markdown;charset=utf-8";
  return "text/plain;charset=utf-8";
}

// --- Save-to-Downloads (desktop) vs. browser download ---------------------

/** Base64-encode a Blob's bytes (no data-URL prefix). FileReader handles
 *  binary correctly and avoids large-array `String.fromCharCode` pitfalls. */
function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const res = String(reader.result);
      const comma = res.indexOf(",");
      resolve(comma >= 0 ? res.slice(comma + 1) : res);
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

export interface SaveOrDownloadArgs {
  filename: string;
  /** Text payload (use this OR `blob`). */
  text?: string;
  /** Binary payload (use this OR `text`). */
  blob?: Blob;
  /** MIME type for the text/browser path. */
  mime?: string;
  /** True in the desktop shell — route the save through the backend. */
  native: boolean;
}

/**
 * Save a file so it actually lands in the user's Downloads folder, everywhere.
 *
 * - Desktop (`native`): POST the bytes to ``/api/downloads/save``; the backend
 *   writes them into ``~/Downloads`` (pywebview silently cancels browser
 *   downloads, so this is the only path that works there). Returns the saved
 *   absolute path. On any failure it falls back to the browser download — never
 *   worse than before.
 * - Browser / VPS (`!native`): the normal blob ``<a download>`` — the real
 *   browser saves into the user's own Downloads. Returns null.
 */
export async function saveOrDownload(
  args: SaveOrDownloadArgs,
): Promise<string | null> {
  const { filename, native, mime } = args;
  const mimeType = mime ?? "text/plain;charset=utf-8";
  if (native) {
    try {
      const payload =
        args.blob ?? new Blob([args.text ?? ""], { type: mimeType });
      const contentB64 = await blobToBase64(payload);
      const res = await fetch("/api/downloads/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename, content_b64: contentB64 }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as { saved_path?: string };
      return data.saved_path ?? filename;
    } catch {
      // Fall through to the browser download — never worse than today.
    }
  }
  if (args.blob) downloadBlob(filename, args.blob);
  else downloadAs(filename, args.text ?? "", mimeType);
  return null;
}
