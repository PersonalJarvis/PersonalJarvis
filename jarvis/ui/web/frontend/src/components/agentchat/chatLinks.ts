import { defaultUrlTransform } from "react-markdown";

const LOCAL_PREFIX = "#jarvis-local=";

export type ChatLinkAction =
  | { type: "local"; path: string }
  | { type: "external"; url: string }
  | { type: "stay" }
  | { type: "allow" };

function decodePath(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

/** `file:///C:/x` and `file:///home/x` become a native path. Network hosts do not. */
function pathFromFileUri(value: string): string | null {
  const rest = value.replace(/^file:/i, "");
  if (!rest.startsWith("//")) return decodePath(rest) || null;
  const hostAndPath = rest.slice(2);
  const slash = hostAndPath.indexOf("/");
  const host = slash === -1 ? hostAndPath : hostAndPath.slice(0, slash);
  const path = slash === -1 ? "" : hostAndPath.slice(slash);
  if (/^[A-Za-z]:$/.test(host)) return decodePath(host + path) || null;
  if (host && host.toLowerCase() !== "localhost") return null;
  const decoded = decodePath(path);
  return /^\/[A-Za-z]:\//.test(decoded) ? decoded.slice(1) : decoded || null;
}

function stripAngles(raw: string): string {
  const text = raw.trim();
  if (text.length >= 2 && text.startsWith("<") && text.endsWith(">")) return text.slice(1, -1).trim();
  return text;
}

/**
 * A linked name such as `notes.md`, `photo.png` or `folder/clip.mp4`.
 * Web addresses and app downloads are not files on this computer.
 */
export function looksLikeLocalFile(raw: string): boolean {
  const text = stripAngles(raw);
  if (!text || text.length > 240 || /[\u0000\r\n]/.test(text)) return false;
  if (/^[a-z][a-z0-9+.-]*:/i.test(text)) return false;
  if (text.startsWith("/api/") || text === "/api" || text.startsWith("//")) return false;
  const parts = text.replace(/\\/g, "/").split("/");
  if (parts.some(part => part === ".." || part === "")) return false;
  const name = parts[parts.length - 1] ?? "";
  return /^[^\\/:*?"<>|]+\.[A-Za-z0-9]{1,12}$/.test(name);
}

/**
 * Absolute local path written in a chat link, or null when the address is
 * something else (a web URL, a relative name, an app download).
 */
export function nativePathFromMarkdownUrl(raw: string): string | null {
  let text = stripAngles(raw);
  if (!text || /[\u0000\r\n]/.test(text)) return null;
  const lower = text.toLowerCase();
  if (
    lower.startsWith("http://") ||
    lower.startsWith("https://") ||
    lower.startsWith("javascript:") ||
    lower.startsWith("data:") ||
    lower.startsWith("mailto:")
  ) {
    return null;
  }
  if (lower.startsWith("file:")) {
    const converted = pathFromFileUri(text);
    if (!converted) return null;
    text = converted;
  }
  if (text.startsWith("\\\\") || text.startsWith("//")) return null;
  if (text.startsWith("~/") || text.startsWith("~\\")) return text;
  if (/^[A-Za-z]:[\\/]/.test(text)) return text;
  if (text.startsWith("/") && text !== "/api" && !text.startsWith("/api/")) return text;
  return null;
}

/** Full path when the link has one, otherwise a bare filename worth opening. */
export function openableLinkTarget(raw: string): string | null {
  return nativePathFromMarkdownUrl(raw) || (looksLikeLocalFile(raw) ? stripAngles(raw) : null);
}

/** Keep a local path on a hash so the desktop window cannot navigate to it. */
export function chatUrlTransform(url: string): string {
  const local = openableLinkTarget(url);
  if (local) return LOCAL_PREFIX + encodeURIComponent(local);
  return defaultUrlTransform(url);
}

export function localPathFromChatHref(href: string): string | null {
  if (!href.startsWith(LOCAL_PREFIX)) return null;
  const path = decodePath(href.slice(LOCAL_PREFIX.length));
  return path || null;
}

/**
 * What a click on a chat link should do.
 *
 * `stay` means: do not load this address in the window. An empty href reloads
 * the current page, and a relative file name is served back as the app itself.
 */
export function chatLinkAction(href: string, label = ""): ChatLinkAction {
  const local = localPathFromChatHref(href) || openableLinkTarget(href);
  if (local) return { type: "local", path: local };
  if (/^https?:\/\//i.test(href)) return { type: "external", url: href };
  if (href.startsWith("//")) return { type: "external", url: `https:${href}` };
  if (href.startsWith("mailto:") || href.startsWith("/api/")) return { type: "allow" };
  const named = openableLinkTarget(label);
  if (named) return { type: "local", path: named };
  return { type: "stay" };
}
