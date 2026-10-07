/**
 * Copy an edited appshot (a PNG) to the clipboard, on every surface.
 *
 * Desktop first goes through the backend, which writes the OS clipboard
 * natively (`POST /api/appshot/clipboard`): the embedded WebView cannot be
 * trusted with images — WebView2 may never settle `navigator.clipboard.write`
 * and WKWebView rejects it once the click handler has awaited the export.
 * A browser (or a desktop route that refuses) uses the browser clipboard,
 * raced against a timeout so a promise that never settles cannot hang the
 * button.
 */

export const BROWSER_COPY_TIMEOUT_MS = 5_000;

export interface CopyOptions {
  /** True in the desktop shell (`native_file_actions`). */
  native: boolean;
  /** The error message when the browser clipboard never answers. */
  timeoutMessage: string;
  /** Test seams. */
  fetchImpl?: typeof fetch;
  clipboard?: Pick<Clipboard, "write"> | null;
  timeoutMs?: number;
}

/** Resolves once the picture is on the clipboard; rejects with a reason. */
export async function copyAppshotPng(blob: Blob, options: CopyOptions): Promise<"native" | "browser"> {
  const fetchImpl = options.fetchImpl ?? fetch;
  let nativeReason = "";
  if (options.native) {
    try {
      const response = await fetchImpl("/api/appshot/clipboard", {
        method: "POST",
        headers: { "content-type": "image/png" },
        body: blob,
      });
      if (response.ok) return "native";
      const body = (await response.json().catch(() => null)) as { detail?: string } | null;
      nativeReason = body?.detail || `HTTP ${response.status}`;
    } catch (error) {
      nativeReason = (error as Error).message;
    }
  }
  const clipboard =
    options.clipboard !== undefined ? options.clipboard : typeof navigator !== "undefined" ? navigator.clipboard : null;
  if (!clipboard || typeof ClipboardItem === "undefined") {
    throw new Error(nativeReason || "no clipboard is available here");
  }
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([
      clipboard.write([new ClipboardItem({ "image/png": blob })]),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(options.timeoutMessage)), options.timeoutMs ?? BROWSER_COPY_TIMEOUT_MS);
      }),
    ]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
  return "browser";
}
