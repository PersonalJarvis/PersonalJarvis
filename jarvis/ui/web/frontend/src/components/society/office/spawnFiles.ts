/**
 * Files dropped on the spawn point, held until the agents they are for exist.
 *
 * A drop on a pane goes straight to that pane's terminal. Here the terminal
 * does not exist yet, so a drop is only READ now and handed to each new pane
 * once it is live. The reading is the pane's own — the same drag tracking
 * (`usePaneFileDrag`), payload extraction (`paneDrop`) and desktop-shell path
 * resolution (`waitForNativeDrop`) — so the two surfaces cannot disagree about
 * what a drop is.
 */
import { useCallback, useState, type ClipboardEvent } from "react";
import {
  extractPaneDrop,
  extractPasteFiles,
  isEmptyPayload,
  nameClipboardFile,
  type PaneDropPayload,
} from "@/components/agentic/paneDrop";
import { usePaneFileDrag } from "@/components/agentic/paneFileDrag";
import type { DropAttachment } from "@/lib/agenticIdeApi";
import { waitForNativeDrop } from "@/lib/nativeDrop";

/** One held file: a real path the drag carried, or bytes it gave no path for. */
export interface HeldFile {
  key: string;
  name: string;
  path?: string;
  file?: File;
}

function baseName(path: string): string {
  return path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || path;
}

/** `held` plus whatever in `payload` is not held yet. Pure. */
export function holdDrop(held: readonly HeldFile[], payload: PaneDropPayload): HeldFile[] {
  const next = [...held];
  const seen = new Set(held.map((h) => h.key));
  const add = (item: HeldFile) => {
    if (seen.has(item.key)) return;
    seen.add(item.key);
    next.push(item);
  };
  for (const path of payload.paths) {
    add({ key: `path:${path.replace(/\\/g, "/").toLowerCase()}`, name: baseName(path), path });
  }
  for (const file of payload.files) {
    add({ key: `file:${file.name}:${file.size}:${file.lastModified}`, name: file.name, file });
  }
  return next;
}

/** The held files in the shape the attach endpoint takes. Pure. */
export function heldPayload(held: readonly HeldFile[]): PaneDropPayload {
  return {
    paths: held.flatMap((h) => (h.path ? [h.path] : [])),
    files: held.flatMap((h) => (h.file ? [h.file] : [])),
  };
}

/**
 * The brief, plus every attached file the analysis did not cover. Pure.
 *
 * A described file travels as an attachment and reaches the agent that way; a
 * file too large to read would otherwise vanish from the brief, so its
 * reference is written in. Never at the very end: a trailing `@path` holds the
 * CLI's completion popup open, and the Enter that follows picks a suggestion
 * instead of submitting.
 */
export function briefWithFiles(
  brief: string,
  references: readonly string[],
  analysis: readonly DropAttachment[],
): string {
  const described = new Set(analysis.map((a) => a.reference));
  const loose = references.filter((r) => !described.has(r));
  if (loose.length === 0) return brief;
  return `${brief}\n\nFiles for this task: ${loose.join(" ")} - open them as needed.`;
}

export function useSpawnFiles() {
  const [held, setHeld] = useState<HeldFile[]>([]);
  const hold = useCallback((payload: PaneDropPayload) => {
    if (!isEmptyPayload(payload)) setHeld((prev) => holdDrop(prev, payload));
  }, []);

  const { dragging, handlers } = usePaneFileDrag(
    useCallback(
      (dt: DataTransfer) => {
        // Both reads happen BEFORE any await: a DataTransfer empties the moment
        // this handler returns, and the desktop shell only answers a listener
        // that was already in place when the drop happened.
        const payload = extractPaneDrop(dt);
        void waitForNativeDrop().then((detail) => {
          if (!detail?.paths.length) return hold(payload);
          // Inside the desktop shell the real path beats a byte copy of the
          // same file; drop the copies the shell just accounted for.
          const named = new Set(detail.names.map((n) => n.toLowerCase()));
          hold({
            paths: Array.from(new Set([...payload.paths, ...detail.paths])),
            files: payload.files.filter((f) => !named.has(f.name.toLowerCase())),
          });
        });
      },
      [hold],
    ),
  );

  /** On the text box: claims a pasted IMAGE, never pasted text. */
  const onPaste = useCallback(
    (event: ClipboardEvent) => {
      const files = extractPasteFiles(event.clipboardData).map((f) => nameClipboardFile(f, "spawn"));
      if (files.length === 0) return;
      event.preventDefault();
      hold({ paths: [], files });
    },
    [hold],
  );

  const addFiles = useCallback((files: File[]) => hold({ paths: [], files }), [hold]);
  const remove = useCallback((key: string) => setHeld((prev) => prev.filter((h) => h.key !== key)), []);
  /** Forget exactly these — a file dropped while a launch ran stays for the next one. */
  const release = useCallback((used: readonly HeldFile[]) => {
    const keys = new Set(used.map((h) => h.key));
    setHeld((prev) => prev.filter((h) => !keys.has(h.key)));
  }, []);

  return { held, dragging, handlers, onPaste, addFiles, remove, release };
}
