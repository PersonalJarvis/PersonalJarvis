/**
 * Lifting a row out of the workspace explorer and carrying it to a pane.
 *
 * Dropping a file from the operating system's file manager onto an agent
 * already worked; the app's own explorer sat right beside those panes and could
 * not do the same thing. This closes that half, and the only real work is
 * turning what the explorer knows into what the drop side expects.
 *
 * The explorer speaks in paths RELATIVE to the workspace root, because that is
 * all the file-listing endpoint hands out — it is a workspace browser, not a
 * host browser. The drop endpoint speaks in absolute paths, because a file
 * dragged out of Explorer arrives absolute and it decides from the location
 * whether the file already lies inside the workspace (referenced where it is)
 * or outside it (copied in). Joining the two is this module's whole job.
 *
 * The join has to produce a path the BACKEND's platform understands, which is
 * not necessarily the one the browser runs on — the desktop app and a phone
 * pointed at the same server are the same page. So the separator is read off
 * the workspace root the server sent, never off `navigator`.
 */
import { WORKSPACE_PATH_TYPE } from "./paneDrop";

/** A drive letter (`C:\…`) or a UNC share (`\\server\…`) — Windows either way. */
function isWindowsRoot(root: string): boolean {
  return /^[a-zA-Z]:[\\/]/.test(root) || root.startsWith("\\\\");
}

/**
 * The workspace-relative `path` as an absolute one under `root`.
 *
 * A root the server has not sent yet (an explorer rendered a tick before the
 * session arrives) yields an empty string, which callers read as "not
 * draggable" — better than a path rooted at nothing.
 */
export function absoluteWorkspacePath(root: string, path: string): string {
  const base = root.trim().replace(/[\\/]+$/, "");
  const relative = path.trim().replace(/^[\\/]+/, "");
  if (!base) return "";
  if (!relative) return root.trim();
  return isWindowsRoot(base)
    ? `${base}\\${relative.replace(/\//g, "\\")}`
    : `${base}/${relative}`;
}

/**
 * The same path as a `file://` URI, for drop targets outside this page.
 *
 * Only the OUTSIDE case needs this — a pane reads {@link WORKSPACE_PATH_TYPE}
 * and never has to parse a URL. It is offered anyway because a drag that
 * carries `text/uri-list` is a drag another application can accept, and
 * withholding it would make the explorer a dead end everywhere but here.
 *
 * A UNC path keeps its host in the authority position (`file://server/share`),
 * which is the one shape a bare `file:///` prefix would silently corrupt.
 */
export function workspaceFileUri(absolute: string): string {
  const posix = absolute.replace(/\\/g, "/");
  const encoded = posix
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/")
    // A drive letter is not a path segment to be escaped; `C%3A` is a
    // directory named "C:" as far as anything reading it back is concerned.
    .replace(/^([A-Za-z])%3A/, "$1:");
  if (posix.startsWith("//")) return `file:${encoded}`;
  return `file:///${encoded.replace(/^\/+/, "")}`;
}

export interface WorkspaceEntryDrag {
  /** Absolute workspace root, as the server spelled it. */
  root: string;
  /** POSIX-style path relative to that root. */
  path: string;
}

/**
 * Load a drag with one explorer entry. Returns false when there is nothing to
 * carry, so the caller can cancel the gesture instead of starting an empty one.
 *
 * Three formats for three audiences, all describing the same file:
 *
 * * {@link WORKSPACE_PATH_TYPE} — the panes, verbatim and lossless.
 * * `text/uri-list` — other applications, and the pane's own arming check,
 *   which is what makes the drop overlay appear as the row crosses a pane.
 * * `text/plain` — anything that only takes text: a chat composer, an editor,
 *   a text field in another window.
 */
export function setWorkspaceEntryDrag(
  dt: DataTransfer,
  entry: WorkspaceEntryDrag,
): boolean {
  const absolute = absoluteWorkspacePath(entry.root, entry.path);
  if (!absolute) return false;
  dt.setData(WORKSPACE_PATH_TYPE, absolute);
  dt.setData("text/uri-list", workspaceFileUri(absolute));
  dt.setData("text/plain", absolute);
  // "copy" and not "move": the file stays where it is. A "move" cursor over a
  // terminal would promise the explorer is about to lose the row.
  dt.effectAllowed = "copy";
  return true;
}
