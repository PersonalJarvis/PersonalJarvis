/**
 * Which files the editor shows with a viewer instead of as text.
 *
 * Everything not listed here is opened as text first; the server says whether
 * it really is text, and a file that is not falls back to a read-only peek.
 */
export type ViewKind = "image" | "pdf" | "audio" | "video" | "document";

const KINDS: Record<ViewKind, ReadonlySet<string>> = {
  image: new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "avif"]),
  pdf: new Set(["pdf"]),
  audio: new Set(["mp3", "wav", "ogg", "oga", "m4a", "aac", "flac", "weba", "opus"]),
  video: new Set(["mp4", "m4v", "webm", "ogv", "mov"]),
  // Office and e-book formats: the server extracts their text for reading.
  document: new Set(["docx", "doc", "odt", "rtf", "xlsx", "xls", "ods", "pptx", "ppt", "odp", "epub"]),
};

const extensionOf = (path: string): string => {
  const name = (path.split("/").pop() ?? path).toLowerCase();
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1) : "";
};

export function viewKindOf(path: string): ViewKind | null {
  const extension = extensionOf(path);
  for (const kind of Object.keys(KINDS) as ViewKind[]) if (KINDS[kind].has(extension)) return kind;
  return null;
}

/** Text files that can also be shown rendered (Ctrl+Shift+V). */
export type RenderKind = "markdown" | "html" | "svg";

export function renderKindOf(path: string): RenderKind | null {
  const extension = extensionOf(path);
  if (["md", "markdown", "mdx", "mdown", "mkd"].includes(extension)) return "markdown";
  if (["html", "htm"].includes(extension)) return "html";
  if (extension === "svg") return "svg";
  return null;
}
