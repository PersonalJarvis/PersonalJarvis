/**
 * Open an external URL in the user's *real* browser.
 *
 * Why this exists: the desktop app embeds WebView2 (pywebview, `gui:
 * "edgechromium"`). That shell silently drops `window.open(...)` and
 * `target="_blank"` — the new tab never appears. So OAuth-authorize and
 * token-creation pages (plugin "connect") opened to nothing.
 *
 * Design — bridge-first, no fragile shell detection. We always ask the backend
 * to open the URL (`POST /api/settings/open-external`). The backend opens the
 * OS default browser *only when it has a local display* (the desktop install,
 * or a browser tab on the same machine) and reports `{opened: true}`. On a
 * remote/VPS server there is no display, it reports `{opened: false}`, and we
 * fall back to `window.open` so the URL opens in the user's own remote browser.
 * This way the desktop shell always reaches a real browser, and remote browsers
 * keep working — without depending on a shell-detection flag that can be unset.
 */

function openInThisBrowser(url: string): Window | null {
  try {
    return window.open(url, "_blank", "noopener,noreferrer");
  } catch {
    return null;
  }
}

/**
 * Open `url` in the user's browser. Resolves true when a browser was
 * reached (backend bridge reported opened, or a fallback tab was created),
 * false otherwise. Never throws.
 * Tries the local bridge first, falls back to a `window.open` tab.
 */
export async function openExternalUrl(url: string): Promise<boolean> {
  if (!url) return false;
  try {
    const res = await fetch("/api/settings/open-external", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    if (res.ok) {
      const data = (await res.json().catch(() => null)) as { opened?: boolean } | null;
      if (data?.opened) return true;
    }
  } catch {
    // Bridge unreachable — fall through to a best-effort window.open.
  }
  try {
    const tab = openInThisBrowser(url);
    return tab !== null;
  } catch {
    return false;
  }
}
