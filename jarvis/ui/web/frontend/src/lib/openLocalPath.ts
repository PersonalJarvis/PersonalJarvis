/**
 * Open a file or folder on this computer.
 *
 * The desktop app is a web view. Sending the path to `window.open` or letting
 * a link navigate would load it inside that window and reload the app. This
 * asks the backend to hand the path to the operating system instead.
 */
export async function openLocalPath(path: string): Promise<boolean> {
  if (!path) return false;
  try {
    const res = await fetch("/api/settings/open-path", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (!res.ok) return false;
    const data = (await res.json().catch(() => null)) as { opened?: boolean } | null;
    return data?.opened === true;
  } catch {
    // The bridge can be absent. A failed open must not navigate this window.
    return false;
  }
}
