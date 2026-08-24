/**
 * Files going in with the next instruction — the drop, the paste, and the strip
 * of chips that shows what is waiting.
 *
 * Extracted from the grid's composer so every surface that writes to an agent
 * behaves the same. A drop that works in one place and does nothing one click
 * away is read as the app being broken, not as two surfaces with different
 * features (maintainer, 2026-08-24) — and this repo has had two such surfaces
 * more than once.
 *
 * The desktop shell is why the drop handler is not three lines. Inside it a
 * dragged file usually arrives with NO path — the page is handed bytes and a
 * name, by design — while the host process can see where the file really lies
 * and reports it a moment later (jarvis/ui/native_drop.py). Waiting for that
 * answer is the difference between the agent opening the user's own file and
 * the agent opening a copy of it that nothing else knows about.
 *
 * What a drop DOES here is deliberately not what a drop on a terminal pane
 * does. The pane types a path, which is right for "read this file". A prompt
 * bar holds the file instead, has it READ — an image described, a document
 * extracted — and sends that along with the sentence being typed. Several
 * coding CLIs cannot open an image at all, so without the description the user
 * drops a picture of a broken layout, types "fix this", and the agent receives
 * a path and a pronoun.
 */
import { useCallback, useState } from "react";
import { FileText, ImageIcon, Loader2, X } from "lucide-react";

import { usePaneFileDrag } from "@/components/agentic/paneFileDrag";
import {
  extractPaneDrop,
  extractPasteFiles,
  isEmptyPayload,
  nameClipboardFile,
  type PaneDropPayload,
} from "@/components/agentic/paneDrop";
import { waitForNativeDrop } from "@/lib/nativeDrop";
import { attachToTerminal, type DropAttachment } from "@/lib/agenticIdeApi";
import { cn } from "@/lib/utils";

export interface PromptAttachments {
  /** What will travel with the next instruction. */
  attachments: DropAttachment[];
  /** How many files are being read right now — 0 when nothing is in flight. */
  analyzing: number;
  /** True while a file drag hovers the surface; drives the drop styling. */
  dragging: boolean;
  /** Spread onto the element that should accept drops. */
  dragHandlers: ReturnType<typeof usePaneFileDrag>["handlers"];
  /** Attach files chosen by hand (the paperclip) or read off the clipboard. */
  attachFiles: (files: File[]) => void;
  /** Put on the element that should take pasted images; text paste is left alone. */
  onPaste: (event: React.ClipboardEvent) => void;
  remove: (name: string) => void;
  clear: () => void;
}

/**
 * Hold files for one agent's next instruction.
 *
 * `target` is the pane's call-sign; with none, a drop is refused through
 * `onProblem` rather than silently swallowed. `onProblem` is where the caller
 * puts its own toast or inline error — this hook does not decide how a surface
 * reports things. It DOES decide how bad the news is, and the two are not the
 * same: a drop that carried nothing usable is a warning about what was
 * dragged, while an attach that threw is an error about the app. Collapsing
 * them into one level buries the second.
 */
export function usePromptAttachments(
  target: string,
  onProblem: (message: string, severity: "warning" | "error") => void,
): PromptAttachments {
  const [attachments, setAttachments] = useState<DropAttachment[]>([]);
  const [analyzing, setAnalyzing] = useState(0);

  const attach = useCallback(
    async (payload: PaneDropPayload) => {
      if (isEmptyPayload(payload)) return;
      if (!target) {
        onProblem("Pick a session first — a dropped file belongs to one.", "warning");
        return;
      }
      setAnalyzing((n) => n + 1);
      try {
        const result = await attachToTerminal(target, {
          ...payload,
          analyze: true,
          // Held, not typed: the user is still writing the sentence that says
          // what to do with the file, and it goes in with that sentence.
          deliver: false,
        });
        const found = result.analysis ?? [];
        if (found.length === 0) {
          onProblem("That drop carried nothing this prompt could use.", "warning");
          return;
        }
        // Keyed by name so the same file dropped twice is held once — a
        // repeated reference has the agent read it again for nothing.
        setAttachments((prev) => [
          ...prev,
          ...found.filter((item) => !prev.some((held) => held.name === item.name)),
        ]);
      } catch (e) {
        onProblem((e as Error).message, "error");
      } finally {
        setAnalyzing((n) => Math.max(0, n - 1));
      }
    },
    [target, onProblem],
  );

  const { dragging, handlers: dragHandlers } = usePaneFileDrag(
    useCallback(
      (dt: DataTransfer) => {
        // Both reads happen BEFORE any await, and they have to: a DataTransfer
        // empties the moment this handler returns, and the desktop shell only
        // answers a listener that was already in place when the drop happened.
        const payload = extractPaneDrop(dt);
        void waitForNativeDrop().then((detail) => {
          if (!detail?.paths.length) return void attach(payload);
          // Inside the desktop shell the host knows where the file really
          // lies. Prefer that over uploading its bytes — same file, no copy —
          // and drop the byte copies the shell just accounted for so nothing
          // is attached twice.
          const named = new Set(detail.names.map((n) => n.toLowerCase()));
          return void attach({
            paths: Array.from(new Set([...payload.paths, ...detail.paths])),
            files: payload.files.filter((f) => !named.has(f.name.toLowerCase())),
          });
        });
      },
      [attach],
    ),
  );

  const attachFiles = useCallback(
    (files: File[]) => {
      if (files.length > 0) void attach({ paths: [], files });
    },
    [attach],
  );

  const onPaste = useCallback(
    (event: React.ClipboardEvent) => {
      // Text paste belongs to the browser and must keep working — this only
      // claims what a text box would otherwise discard: an IMAGE on the
      // clipboard, which is what PrintScreen and Ctrl+V produce.
      const files = extractPasteFiles(event.clipboardData).map((f) =>
        nameClipboardFile(f, target || "chat"),
      );
      if (files.length === 0) return;
      event.preventDefault();
      void attach({ paths: [], files });
    },
    [attach, target],
  );

  const remove = useCallback((name: string) => {
    setAttachments((prev) => prev.filter((a) => a.name !== name));
  }, []);

  const clear = useCallback(() => setAttachments([]), []);

  return {
    attachments,
    analyzing,
    dragging,
    dragHandlers,
    attachFiles,
    onPaste,
    remove,
    clear,
  };
}

/**
 * The files waiting to go in with the next prompt.
 *
 * Each chip says what was actually LEARNED from the file, not just that one was
 * attached. That distinction is the whole feature: "screenshot.png" tells the
 * user nothing about whether the agent will be able to see it, while "described"
 * and "not described" are the two outcomes they need to be able to tell apart
 * before they press Send — and the second one happens for real, on any install
 * whose providers cannot see images.
 */
export function AttachmentStrip({
  attachments,
  analyzing,
  onRemove,
}: {
  attachments: DropAttachment[];
  analyzing: number;
  onRemove: (name: string) => void;
}) {
  return (
    <div
      data-testid="agentic-attachments"
      className="mb-2 flex max-h-20 shrink-0 flex-wrap items-center gap-1.5 overflow-y-auto scrollbar-jarvis"
    >
      {attachments.map((item) => {
        const read = item.described_by !== "none" && item.detail.length > 0;
        return (
          <span
            key={item.name}
            data-testid={`agentic-attachment-${item.name}`}
            title={
              read
                ? `${item.detail.slice(0, 400)}${item.detail.length > 400 ? "…" : ""}`
                : item.note || "Attached as a file."
            }
            className={cn(
              "flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px]",
              read
                ? "border-primary/40 bg-primary/10 text-foreground"
                : "border-border text-muted-foreground",
            )}
          >
            {item.kind === "image" ? (
              <ImageIcon className="h-3 w-3 shrink-0" />
            ) : (
              <FileText className="h-3 w-3 shrink-0" />
            )}
            <span className="max-w-[12rem] truncate font-mono">{item.name}</span>
            <span className="shrink-0 text-[10px] text-muted-foreground">
              {read
                ? item.described_by === "vision"
                  ? "described"
                  : "text read"
                : "not described"}
            </span>
            <button
              type="button"
              aria-label={`Remove ${item.name}`}
              data-testid={`agentic-attachment-remove-${item.name}`}
              onClick={() => onRemove(item.name)}
              className="shrink-0 rounded text-muted-foreground hover:text-destructive"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        );
      })}
      {analyzing > 0 && (
        <span
          data-testid="agentic-attachment-working"
          className="flex items-center gap-1.5 rounded-md border border-dashed border-border px-2 py-1 text-[11px] text-muted-foreground"
        >
          <Loader2 className="h-3 w-3 animate-spin" />
          Reading {analyzing === 1 ? "the dropped file" : `${analyzing} dropped files`}…
        </span>
      )}
    </div>
  );
}
