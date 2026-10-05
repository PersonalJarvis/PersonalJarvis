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
import { useCallback, useEffect, useRef, useState } from "react";

import {
  extractPaneDrop,
  extractPasteFiles,
  isEmptyPayload,
  nameClipboardFile,
  type PaneDropPayload,
} from "@/components/agentic/paneDrop";
import { usePaneFileDrag } from "@/components/agentic/paneFileDrag";
import { waitForNativeDrop } from "@/lib/nativeDrop";
import {
  attachChatFilesIn,
  attachmentFileUrl,
  type AgentChatSurface,
  type ChatAttachment,
} from "@/lib/agentChatApi";

export interface ChatAttachments {
  /** What will travel with the next message. */
  attachments: ChatAttachment[];
  /**
   * A picture for each held image or video, keyed by the attachment's name:
   * a local `blob:` URL when its bytes passed through this window (a paste,
   * the file picker, a claimed appshot), else the backend's copy of the file
   * (a drag from the Appshots gallery or Explorer arrives by path only).
   */
  previews: Record<string, string>;
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
  const [previews, setPreviews] = useState<Record<string, string>>({});
  const previewsRef = useRef(previews);
  previewsRef.current = previews;
  const { sessionId, cwd, provider, surface } = target;

  // Object URLs hold the picture in memory until revoked; let them go with
  // the composer.
  useEffect(() => () => Object.values(previewsRef.current).forEach(revokePreview), []);

  const attach = useCallback(
    async (payload: PaneDropPayload) => {
      if (isEmptyPayload(payload)) return;
      setAnalyzing((n) => n + 1);
      try {
        const { attachments: found, cwd: folder } = await attachChatFilesIn({
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
        const pictures = matchPreviews(found, payload.files);
        if (folder) {
          for (const item of found) {
            if (!pictures[item.name] && attachmentMedia(item)) {
              pictures[item.name] = attachmentFileUrl(folder, item.reference);
            }
          }
        }
        if (Object.keys(pictures).length > 0) {
          setPreviews((prev) => {
            const next = { ...prev };
            for (const [name, url] of Object.entries(pictures)) {
              if (next[name]) revokePreview(next[name]);
              next[name] = url;
            }
            return next;
          });
        }
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
    setPreviews((prev) => {
      if (!prev[name]) return prev;
      revokePreview(prev[name]);
      const rest = { ...prev };
      delete rest[name];
      return rest;
    });
  }, []);

  const clear = useCallback(() => {
    setAttachments([]);
    setPreviews((prev) => {
      Object.values(prev).forEach(revokePreview);
      return {};
    });
  }, []);

  return {
    attachments,
    previews,
    analyzing,
    dragging,
    dragHandlers,
    attachFiles,
    onPaste,
    remove,
    clear,
  };
}

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|avif|bmp|ico|heic|heif|tiff?)$/i;
const VIDEO_EXT = /\.(mp4|m4v|webm|mov)$/i;

/** Whether a held file can be drawn as a picture or a video, from its name. */
export function attachmentMedia(item: Pick<ChatAttachment, "name" | "kind">): "image" | "video" | null {
  if (VIDEO_EXT.test(item.name)) return "video";
  if (item.kind === "image" || IMAGE_EXT.test(item.name)) return "image";
  return null;
}

/** Only a local object URL holds memory; a backend URL has nothing to free. */
function revokePreview(url: string): void {
  if (url.startsWith("blob:")) URL.revokeObjectURL(url);
}

/**
 * Pair the images and videos the backend read with the bytes that went up,
 * for a picture in the composer. The backend may store a file under another name
 * (a clipboard image gets one, a clash gets a suffix), so an exact name match
 * comes first and a lone image on both sides pairs by being alone. Anything
 * still unpaired gets no picture rather than the wrong one.
 */
export function matchPreviews(found: ChatAttachment[], files: File[]): Record<string, string> {
  if (typeof URL.createObjectURL !== "function") return {};
  const images = files.filter((f) => f.type.startsWith("image/") || f.type.startsWith("video/"));
  const read = found.filter((item) => attachmentMedia(item) !== null);
  const out: Record<string, string> = {};
  const used = new Set<File>();
  for (const item of read) {
    const file = images.find((f) => f.name === item.name && !used.has(f));
    if (file) {
      used.add(file);
      out[item.name] = URL.createObjectURL(file);
    }
  }
  if (Object.keys(out).length === 0 && images.length === 1 && read.length === 1) {
    out[read[0].name] = URL.createObjectURL(images[0]);
  }
  return out;
}
