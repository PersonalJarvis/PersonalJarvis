/**
 * Reading a drop or a paste that lands on a terminal pane.
 *
 * Text and custom MIME data can come from a hostile page. They cannot grant
 * permission to read a local path. Internal explorer drags carry an opaque,
 * single-use receipt held by this origin; external drops carry File
 * bytes. Native path hints are not a file-read grant on every WebView backend.
 *
 * The hard constraint that shapes this file: **a DataTransfer is emptied the
 * moment the event handler returns.** Reading it after an `await` yields an
 * empty list — a bug that reads as "dropping sometimes does nothing". So every
 * extraction below is synchronous, and the async work happens afterwards on
 * what was already pulled out.
 */

export interface PaneDropPayload {
  /** Filesystem paths authorized by this page's own explorer. */
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
 * The type carries a receipt, never a path supplied by the drop source.
 */
export const WORKSPACE_PATH_TYPE = "application/x-jarvis-workspace-path";

interface WorkspaceDrag {
  receipt: string;
  paths: string[];
  expiresAt: number;
}

const DRAG_STORAGE_KEY = "jarvis:workspace-drag-receipt";
const DRAG_LIFETIME_MS = 120_000;
let workspaceDrag: (WorkspaceDrag & { stored: boolean }) | null = null;
let expiryTimer: ReturnType<typeof setTimeout> | undefined;
let cleanupInstalled = false;

function clearWorkspaceDrag(): void {
  try {
    const stored = JSON.parse(localStorage.getItem(DRAG_STORAGE_KEY) ?? "null");
    if (stored?.receipt === workspaceDrag?.receipt) localStorage.removeItem(DRAG_STORAGE_KEY);
  } catch {
    // Blocked/corrupt storage cannot authorize a drop; the local fallback expires too.
  }
  workspaceDrag = null;
  clearTimeout(expiryTimer);
}

/** Register paths from an explorer row in this page, not from drag metadata. */
export function setWorkspaceDragPaths(dt: DataTransfer, paths: readonly string[]): boolean {
  clearWorkspaceDrag();
  // Attachment APIs frame paths as lines. A filename containing a newline must
  // never become a second file-read request; byte uploads still accept it.
  if (!paths.length || paths.some((path) => !path || /[\r\n]/.test(path))) return false;
  // getRandomValues also works on HTTP LAN origins where randomUUID is absent.
  const receipt = Array.from(crypto.getRandomValues(new Uint8Array(32)), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
  const record: WorkspaceDrag = { receipt, paths: [...paths], expiresAt: Date.now() + DRAG_LIFETIME_MS };
  let stored = false;
  try {
    // Same-origin storage lets a detached IDE window hand a file to the main
    // window. Foreign pages cannot mint or change this record.
    localStorage.setItem(DRAG_STORAGE_KEY, JSON.stringify(record));
    stored = true;
  } catch {
    // Storage-disabled browsers retain same-window drags without trusting MIME paths.
  }
  workspaceDrag = { ...record, stored };
  expiryTimer = setTimeout(clearWorkspaceDrag, DRAG_LIFETIME_MS);
  if (!cleanupInstalled) {
    window.addEventListener("dragend", clearWorkspaceDrag);
    cleanupInstalled = true;
  }
  dt.setData(WORKSPACE_PATH_TYPE, receipt);
  return true;
}

function consumeWorkspaceDrag(receipt: string): string[] | null {
  if (!/^[0-9a-f]{64}$/.test(receipt)) return null;
  let record: WorkspaceDrag | null = null;
  try {
    const stored = JSON.parse(localStorage.getItem(DRAG_STORAGE_KEY) ?? "null");
    if (stored?.receipt === receipt) {
      localStorage.removeItem(DRAG_STORAGE_KEY);
      record = stored;
    }
  } catch {
    // A stored receipt that cannot be read/consumed fails closed.
  }
  if (!record && workspaceDrag?.receipt === receipt && !workspaceDrag.stored) record = workspaceDrag;
  if (workspaceDrag?.receipt === receipt) clearWorkspaceDrag();
  if (
    !record || !Number.isFinite(record.expiresAt) || record.expiresAt <= Date.now() ||
    !Array.isArray(record.paths) || record.paths.length === 0 ||
    record.paths.some((path) => typeof path !== "string" || !path || /[\r\n]/.test(path))
  ) return null;
  return record.paths;
}

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
 * Everything usable in a drop, pulled out synchronously.
 */
export function extractPaneDrop(dt: DataTransfer | null): PaneDropPayload {
  const out: PaneDropPayload = { paths: [], files: [] };
  if (!dt) return out;

  const receipt = dt.getData(WORKSPACE_PATH_TYPE);
  const paths = consumeWorkspaceDrag(receipt);
  if (paths) {
    out.paths = paths;
    return out;
  }

  // Never interpret text/plain, file:// URIs or forged workspace MIME as local
  // paths, even alongside real files. Upload only the bytes the browser grants.
  for (const file of Array.from(dt.files ?? [])) {
    // A directory dropped into a pane arrives as a zero-byte, type-less entry;
    // there is nothing to attach and copying it would produce an empty file.
    if (file.size === 0 && !file.type) continue;
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
