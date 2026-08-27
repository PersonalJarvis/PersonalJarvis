/**
 * Files waiting to go in with the next chat message — the drop, the paste and
 * the paperclip.
 *
 * The Agentic IDE's terminal composer has taken files since 2026-08
 * (components/agentic/PromptAttachments). The two CHATS — the front page and
 * the IDE's chat mode, which share one composer — never did: a dropped
 * screenshot landed on a bare `<textarea>`, where the browser's own default is
 * to NAVIGATE to the file, replacing the whole app with the picture. A gesture
 * that works one click away and destroys the view here reads as the app being
 * broken, not as two surfaces with different features (maintainer, 2026-08-24).
 *
 * The pieces are deliberately the terminal path's own — the same drag tracking
 * (`usePaneFileDrag`), the same payload extraction (`paneDrop`), the same
 * desktop-shell path resolution (`waitForNativeDrop`) — so the two surfaces
 * cannot drift into disagreeing about what a drop is. Only the endpoint
 * differs: a chat has no pane to type a path into, so the files are HELD here
 * and travel with the sentence when it is sent.
 */
import { useCallback, useState } from "react";

import {
  extractPaneDrop,
  extractPasteFiles,
  isEmptyPayload,
  nameClipboardFile,
  type PaneDropPayload,
} from "@/components/agentic/paneDrop";
import { usePaneFileDrag } from "@/components/agentic/paneFileDrag";
import { waitForNativeDrop } from "@/lib/nativeDrop";
import { attachChatFiles, type AgentChatSurface, type ChatAttachment } from "@/lib/agentChatApi";

export interface ChatAttachments {
  /** What will travel with the next message. */
  attachments: ChatAttachment[];
  /** How many files are being read right now — 0 when nothing is in flight. */
  analyzing: number;
  /** True while a file drag hovers the composer; drives the drop styling. */
  dragging: boolean;
  /** Spread onto the element that should accept drops. */
  dragHandlers: ReturnType<typeof usePaneFileDrag>["handlers"];
  /** Attach files chosen by hand (the paperclip) or read off the clipboard. */
  attachFiles: (files: File[]) => void;
  /** Put on the text box: claims a pasted IMAGE, never a pasted text. */
  onPaste: (event: React.ClipboardEvent) => void;
  remove: (name: string) => void;
  clear: () => void;
}

/**
 * Hold files for one chat's next message.
 *
 * `onProblem` is where the caller puts its own error line — this hook does not
 * decide how a surface reports things. It DOES decide how bad the news is: a
 * drop that carried nothing usable is a warning about what was dragged, while
 * an attach that threw is an error about the app, and collapsing the two
 * buries the second.
 */
export function useChatAttachments(
  target: {
    sessionId: string | null;
    cwd: string;
    provider: string;
    surface: AgentChatSurface;
  },
  onProblem: (message: string, severity: "warning" | "error") => void,
): ChatAttachments {
  const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
  const [analyzing, setAnalyzing] = useState(0);
  const { sessionId, cwd, provider, surface } = target;

  const attach = useCallback(
    async (payload: PaneDropPayload) => {
      if (isEmptyPayload(payload)) return;
      setAnalyzing((n) => n + 1);
      try {
        const found = await attachChatFiles({
          files: payload.files,
          paths: payload.paths,
          sessionId,
          cwd,
          provider,
          surface,
        });
        if (found.length === 0) {
          onProblem("That drop carried nothing this chat could use.", "warning");
          return;
        }
        // Keyed by name so the same file attached twice is held once — a
        // repeated reference has the model read it again for nothing.
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
    [sessionId, cwd, provider, surface, onProblem],
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
      // claims what a text box would otherwise DISCARD: an image on the
      // clipboard, which is what PrintScreen and Ctrl+V produce.
      const files = extractPasteFiles(event.clipboardData).map((f) =>
        nameClipboardFile(f, "chat"),
      );
      if (files.length === 0) return;
      event.preventDefault();
      void attach({ paths: [], files });
    },
    [attach],
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
