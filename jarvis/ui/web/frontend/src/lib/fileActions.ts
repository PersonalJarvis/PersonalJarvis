/**
 * Native file actions for a file already saved to the user's Downloads folder.
 *
 * Why this exists: HTML5 cannot reliably drag a real local file out of an
 * embedded WebView. Windows and macOS use a native drag bridge, while these
 * actions remain the universal fallback: reveal the saved file in the OS file
 * manager or open it directly. Both are desktop-only backend calls; on a
 * headless VPS the routes 404 and these return false.
 */

/** Open the OS file manager with `path` selected. Returns true on success. */
export async function revealInFolder(path: string): Promise<boolean> {
  return postFileAction("/api/downloads/reveal", path, "revealed");
}

/** Open `path` with its default application. Returns true on success. */
export async function openDownloadedFile(path: string): Promise<boolean> {
  return postFileAction("/api/downloads/open", path, "opened");
}

async function postFileAction(
  url: string,
  path: string,
  okKey: "revealed" | "opened",
): Promise<boolean> {
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (!res.ok) return false;
    const data = (await res.json().catch(() => null)) as Record<
      string,
      unknown
    > | null;
    return Boolean(data?.[okKey]);
  } catch {
    return false;
  }
}
