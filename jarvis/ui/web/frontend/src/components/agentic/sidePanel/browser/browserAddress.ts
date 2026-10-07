const LOCAL_HOST = /^(localhost|127(?:\.\d{1,3}){3}|\[::1\]|0\.0\.0\.0)(?::\d+)?(?:[/?#]|$)/i;
const BARE_PORT = /^:?\d{2,5}(?:[/?#]|$)/;

/**
 * Turn what the reader typed into an address the browser surface can load.
 *
 * A bare port (`5173`, `:5173`) and a local host go over plain http, the way
 * dev servers listen; anything else without a scheme gets https. Only web
 * schemes pass: `javascript:`, `file:` and friends return null.
 */
export function normalizeBrowserAddress(raw: string): string | null {
  const text = raw.trim();
  if (!text || /\s/.test(text)) return null;
  let candidate: string;
  if (BARE_PORT.test(text)) candidate = `http://localhost:${text.replace(/^:/, "")}`;
  else if (/^[a-z][a-z\d+.-]*:\/\//i.test(text)) candidate = text;
  else if (LOCAL_HOST.test(text)) candidate = `http://${text}`;
  else candidate = `https://${text}`;
  try {
    const parsed = new URL(candidate);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
    if (!parsed.hostname) return null;
    return parsed.href;
  } catch {
    return null;
  }
}
