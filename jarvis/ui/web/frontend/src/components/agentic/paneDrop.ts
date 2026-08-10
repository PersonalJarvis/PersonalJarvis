/**
 * Reading a drop or a paste that lands on a terminal pane.
 *
 * A browser hands a web page two very different things depending on where the
 * drag came from, and the difference decides whether anything has to be copied:
 *
 * * **A real path.** Explorer and Finder put the file's location in
 *   `text/uri-list` (and usually `text/plain`). Nothing needs copying — the
 *   agent can open the file where it lies.
 * * **Only bytes.** A screenshot pasted from the clipboard, an image dragged
 *   off a web page, anything from a sandboxed source. There is no path to be
 *   had — that is a deliberate browser restriction, not something to work
 *   around — so those bytes have to be written somewhere the agent can reach.
 *
 * Both are collected here and sent together; the backend copies only what it
 * has to.
 *
 * The hard constraint that shapes this file: **a DataTransfer is emptied the
 * moment the event handler returns.** Reading it after an `await` yields an
 * empty list — a bug that reads as "dropping sometimes does nothing". So every
 * extraction below is synchronous, and the async work happens afterwards on
 * what was already pulled out.
 */

export interface PaneDropPayload {
  /** Real filesystem paths the drag carried, if any. */
  paths: string[];
  /** Raw files, for everything the browser gave no path for. */
  files: File[];
}

/**
 * The drag type the app's OWN file explorer uses.
 *
 * A drag that starts inside the page could dress itself up as a `file://` URI
 * and travel the same route as an Explorer drag, but that round-trip is lossy
 * exactly where it matters: a Windows drive letter and a UNC share
 * (`\\server\share`) do not survive being parsed back out of a URL, and the
 * failure is silent — the agent is handed a path to nowhere.
 *
 * So an in-app drag carries the path VERBATIM under its own type. It is
 * newline-separated for the same reason the wire format is: one gesture may
 * eventually mean several entries.
 */
export const WORKSPACE_PATH_TYPE = "application/x-jarvis-workspace-path";

/** True when this payload has nothing worth sending. */
export function isEmptyPayload(payload: PaneDropPayload): boolean {
  return payload.paths.length === 0 && payload.files.length === 0;
}

/**
 * Is there a FILE in this drag — the only question a pane may ask while a drag
 * is still in flight?
 *
 * A browser deliberately seals a drag's contents until it is dropped: during
 * `dragenter`/`dragover` only `DataTransfer.types` is readable, never the data
 * itself. So this is a type check, and it has to be, but it is enough to tell
 * the two cases apart that matter:
 *
 * * `Files` — a real drag out of Explorer, Finder, or a file manager.
 * * `text/uri-list` — the one shape some Linux file managers send instead, so
 *   dropping a file there keeps working.
 * * `WORKSPACE_PATH_TYPE` — a row lifted out of the app's own file explorer.
 * * **`text/plain` ALONE is not a file.** That is what selected TEXT looks
 *   like, and text is what a user drags by accident: brush over terminal
 *   output with the mouse held down and the browser lifts the selection into a
 *   drag nobody asked for. A pane that arms on this announces "drop your file
 *   here" at someone holding nothing (BUG-110).
 *
 * An internal mission card carries none of these types, so tossing one across
 * the grid stays invisible to the panes it flies over.
 */
export function dragCarriesFiles(dt: DataTransfer | null): boolean {
  if (!dt) return false;
  const types = Array.from(dt.types ?? []);
  return (
    types.includes("Files") ||
    types.includes("text/uri-list") ||
    types.includes(WORKSPACE_PATH_TYPE)
  );
}

/**
 * A `file://` URI as a native path; anything else unchanged.
 *
 * Windows URIs carry a leading slash before the drive letter
 * (`file:///C:/x` → `C:/x`), which is why this cannot be a plain `slice(7)`.
 */
function unwrapFileUri(value: string): string {
  if (!/^file:/i.test(value)) return value;
  let path: string;
  try {
    path = decodeURIComponent(new URL(value).pathname);
  } catch {
    return value;
  }
  if (path.length > 2 && path[0] === "/" && path[2] === ":") path = path.slice(1);
  return path;
}

/**
 * Does this string look like a filesystem path rather than dragged prose?
 *
 * Dragging selected TEXT also fills `text/plain`, and sending that as a path
 * would have the backend try to read a sentence off the disk. A path is
 * single-line and either absolute (`/x`, `C:\x`, `\\server\share`) or a
 * `file://` URI — a relative-looking fragment is far more likely to be text.
 */
function looksLikePath(value: string): boolean {
  const trimmed = value.trim();
  if (!trimmed || /[\r\n]/.test(trimmed)) return false;
  if (/^file:/i.test(trimmed)) return true;
  if (/^[a-zA-Z]:[\\/]/.test(trimmed)) return true;
  if (trimmed.startsWith("\\\\")) return true;
  return trimmed.startsWith("/");
}

/**
 * Everything usable in a drop, pulled out synchronously.
 *
 * Files and paths are matched up by NAME so a single drag does not produce
 * both a path and a byte copy of the same file: when Explorer gives us the
 * path, the `File` object beside it is redundant.
 */
export function extractPaneDrop(dt: DataTransfer | null): PaneDropPayload {
  const out: PaneDropPayload = { paths: [], files: [] };
  if (!dt) return out;

  // The app's own explorer, when it is the source. Trusted as-is and taken
  // INSTEAD of the text forms rather than alongside them: the same drag also
  // carries `text/plain` for the benefit of anything that only understands
  // text, and reading both would attach every entry twice.
  const internal = dt.getData(WORKSPACE_PATH_TYPE) || "";
  if (internal.trim()) {
    for (const line of internal.split(/[\r\n]+/)) {
      const candidate = line.trim();
      if (candidate) out.paths.push(candidate);
    }
    return out;
  }

  const raw = dt.getData("text/uri-list") || dt.getData("text/plain") || "";
  for (const line of raw.split(/[\r\n]+/)) {
    const candidate = line.trim();
    if (!candidate || candidate.startsWith("#")) continue;
    const unwrapped = unwrapFileUri(candidate);
    if (looksLikePath(unwrapped)) out.paths.push(unwrapped);
  }

  const named = new Set(
    out.paths.map((p) => p.replace(/\\/g, "/").split("/").pop()?.toLowerCase() ?? ""),
  );
  for (const file of Array.from(dt.files ?? [])) {
    // A directory dropped into a pane arrives as a zero-byte, type-less entry;
    // there is nothing to attach and copying it would produce an empty file.
    if (file.size === 0 && !file.type) continue;
    if (named.has(file.name.toLowerCase())) continue;
    out.files.push(file);
  }

  return out;
}

/**
 * Files worth attaching from a paste.
 *
 * Text paste is left entirely alone — xterm already handles that, and grabbing
 * it here would break ordinary copy-paste into the agent. This only picks up
 * the case xterm cannot express: an IMAGE on the clipboard, which is exactly
 * what pressing PrintScreen and Ctrl+V produces.
 */
export function extractPasteFiles(data: DataTransfer | null): File[] {
  if (!data) return [];
  const files: File[] = [];
  for (const item of Array.from(data.items ?? [])) {
    if (item.kind !== "file") continue;
    const file = item.getAsFile();
    if (file && file.size > 0) files.push(file);
  }
  return files;
}

/**
 * A clipboard image arrives named `image.png` — every screenshot would land
 * under the same name and the user could not tell them apart in the agent's
 * output. Give it the pane and a timestamp instead.
 */
export function nameClipboardFile(file: File, paneName: string): File {
  const generic = !file.name || /^image\.[a-z0-9]+$/i.test(file.name);
  if (!generic) return file;
  const ext = (file.type.split("/")[1] || "png").replace(/[^a-z0-9]/gi, "");
  const stamp = new Date()
    .toISOString()
    .replace(/[-:]/g, "")
    .replace(/\..+$/, "")
    .replace("T", "-");
  return new File([file], `${paneName.toLowerCase()}-paste-${stamp}.${ext}`, {
    type: file.type,
  });
}
