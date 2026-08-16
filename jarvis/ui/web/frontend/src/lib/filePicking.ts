/**
 * Turning "the owner dropped something on the window" into files with paths.
 *
 * A skill or plugin is a *folder*, and the browser makes getting one
 * surprisingly awkward: a file input needs the non-standard `webkitdirectory`
 * attribute and reports paths under `webkitRelativePath`, while a drag-and-drop
 * carries no paths at all until you walk the `DataTransferItem` entries
 * yourself — recursively, through a callback API older than Promises.
 *
 * Both roads end here, in the same `PickedFile[]`, so everything downstream
 * (preview, upload, the server's staging rules) only ever sees one shape.
 *
 * The one trap worth naming: `DataTransfer` entries are only valid during the
 * drop event itself. Awaiting anything before reading them empties the list —
 * so `collectDroppedFiles` grabs every entry synchronously up front and only
 * then starts walking them.
 */

export type PickedFile = {
  file: File;
  /** POSIX-style path relative to the drop, e.g. `my-skill/references/a.md`. */
  path: string;
};

type FileSystemEntryLike = {
  isFile: boolean;
  isDirectory: boolean;
  name: string;
  fullPath?: string;
  file?: (onOk: (file: File) => void, onErr?: (error: unknown) => void) => void;
  createReader?: () => {
    readEntries: (
      onOk: (entries: FileSystemEntryLike[]) => void,
      onErr?: (error: unknown) => void,
    ) => void;
  };
};

/** Strips the leading slash a `fullPath` carries and normalises separators. */
function normalisePath(raw: string): string {
  return raw.replace(/\\/g, "/").replace(/^\/+/, "").replace(/^\.\//, "");
}

/**
 * The files an `<input type="file">` produced.
 *
 * With `webkitdirectory` set, each file carries its path relative to the
 * chosen folder; without it (a single ZIP, say) the bare name is the path.
 */
export function filesFromInput(fileList: FileList | null): PickedFile[] {
  if (!fileList) return [];
  return Array.from(fileList).map((file) => ({
    file,
    path: normalisePath(
      (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name,
    ),
  }));
}

/**
 * The files a drop produced — folders walked to the leaves.
 *
 * Falls back to `dataTransfer.files` on browsers that do not expose the entry
 * API, which loses folder structure but still lets a dropped archive through.
 */
export async function collectDroppedFiles(
  dataTransfer: DataTransfer | null,
): Promise<PickedFile[]> {
  if (!dataTransfer) return [];

  // Synchronous first pass — see the note at the top of this file.
  const entries: FileSystemEntryLike[] = [];
  const looseFiles: File[] = [];
  for (const item of Array.from(dataTransfer.items ?? [])) {
    const getEntry = (
      item as DataTransferItem & { webkitGetAsEntry?: () => FileSystemEntryLike | null }
    ).webkitGetAsEntry;
    const entry = getEntry ? getEntry.call(item) : null;
    if (entry) {
      entries.push(entry);
      continue;
    }
    const file = item.getAsFile?.();
    if (file) looseFiles.push(file);
  }

  if (entries.length === 0) {
    const fallback = looseFiles.length > 0 ? looseFiles : Array.from(dataTransfer.files ?? []);
    return fallback.map((file) => ({ file, path: normalisePath(file.name) }));
  }

  const picked: PickedFile[] = [];
  for (const entry of entries) {
    await walkEntry(entry, "", picked);
  }
  return picked;
}

async function walkEntry(
  entry: FileSystemEntryLike,
  parentPath: string,
  out: PickedFile[],
): Promise<void> {
  if (entry.isFile && entry.file) {
    const file = await new Promise<File | null>((resolve) => {
      entry.file?.(resolve, () => resolve(null));
    });
    if (!file) return; // unreadable entry — skip it rather than fail the drop
    const path = entry.fullPath
      ? normalisePath(entry.fullPath)
      : normalisePath(parentPath ? `${parentPath}/${file.name}` : file.name);
    out.push({ file, path });
    return;
  }

  if (!entry.isDirectory || !entry.createReader) return;

  const basePath = entry.fullPath
    ? normalisePath(entry.fullPath)
    : normalisePath(parentPath ? `${parentPath}/${entry.name}` : entry.name);
  const reader = entry.createReader();

  // readEntries hands back at most 100 entries per call and signals the end
  // with an empty batch — a single call silently truncates a large folder.
  while (true) {
    const batch = await new Promise<FileSystemEntryLike[]>((resolve) => {
      reader.readEntries(resolve, () => resolve([]));
    });
    if (batch.length === 0) break;
    for (const child of batch) {
      await walkEntry(child, basePath, out);
    }
  }
}

/** `12.3 MB` — the size, in the words a person reads. */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let size = bytes;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(size < 10 && unit > 0 ? 1 : 0)} ${units[unit]}`;
}
