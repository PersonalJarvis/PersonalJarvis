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
 * (`usePaneFileDrag`) and the same payload extraction (`paneDrop`) so the two surfaces
 * cannot drift into disagreeing about what a drop is. Only the endpoint
 * differs: a chat has no pane to type a path into, so the files are HELD here
 * and travel with the sentence when it is sent.
 */
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import {
  extractPaneDrop,
  extractPasteFiles,
  isEmptyPayload,
  nameClipboardFile,
  type PaneDropPayload,
} from "@/components/agentic/paneDrop";
import { usePaneFileDrag } from "@/components/agentic/paneFileDrag";
import {
  attachChatFilesIn,
  attachmentFileUrl,
  type AgentChatSurface,
  type ChatAttachment,
} from "@/lib/agentChatApi";
import { translate } from "@/i18n";

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
  /** Hand over the held files and empty the draft WITHOUT releasing their pictures. */
  take: () => HeldFiles;
  /** Put files handed over by `take` back into the draft. */
  restore: (files: HeldFiles) => void;
}

/** The files one draft holds, with their pictures. */
export interface HeldFiles {
  attachments: ChatAttachment[];
  previews: Record<string, string>;
}

const NOTHING_HELD: HeldFiles = { attachments: [], previews: {} };

/**
 * Held files per draft, outside React. A composer unmounts whenever the
 * person switches app sections; the typed text already waited in a draft
 * store, and files that vanished beside it read as the app dropping half the
 * message (maintainer, 2026-10-05). The outer key is the owner (a chat store),
 * so two chats with the same session key never share files.
 */
const parked = new WeakMap<object, Map<string, HeldFiles>>();
type AttachmentTarget = { key: string; discarded?: boolean };
const attachmentTargets = new WeakMap<object, Map<string, AttachmentTarget>>();
const listeners = new Set<() => void>();

function attachmentTarget(owner: object, key: string): AttachmentTarget {
  let targets = attachmentTargets.get(owner);
  if (!targets) {
    targets = new Map();
    attachmentTargets.set(owner, targets);
  }
  let target = targets.get(key);
  if (!target) {
    target = { key };
    targets.set(key, target);
  }
  return target;
}

function heldFor(owner: object, key: string): HeldFiles {
  return parked.get(owner)?.get(key) ?? NOTHING_HELD;
}

function updateHeld(owner: object, key: string, update: (current: HeldFiles) => HeldFiles): void {
  const current = heldFor(owner, key);
  const next = update(current);
  if (next === current) return;
  let drafts = parked.get(owner);
  if (!drafts) {
    drafts = new Map();
    parked.set(owner, drafts);
  }
  if (next.attachments.length === 0 && Object.keys(next.previews).length === 0) drafts.delete(key);
  else drafts.set(key, next);
  listeners.forEach((listener) => listener());
}

/** Let go of the pictures of files handed over by `take` and never restored. */
export function releaseHeldFiles(files: HeldFiles): void {
  Object.values(files.previews).forEach(revokePreview);
}

/** Restore a submission to its assigned chat, including after session creation. */
export function restoreHeldFiles(owner: object, key: string, files: HeldFiles): void {
  updateHeld(owner, key, (current) => ({
    attachments: [
      ...files.attachments,
      ...current.attachments.filter((item) => !files.attachments.some((back) => back.name === item.name)),
    ],
    previews: { ...files.previews, ...current.previews },
  }));
}

/** Session creation gives the blank draft a stable key without losing new files. */
export function moveHeldFiles(owner: object, from: string, to: string): void {
  if (from === to) return;
  // Uploads already in flight follow this draft to its assigned session.
  // A later blank chat gets a fresh target instead of inheriting this alias.
  attachmentTarget(owner, from).key = to;
  attachmentTargets.get(owner)?.delete(from);
  const held = heldFor(owner, from);
  updateHeld(owner, from, () => NOTHING_HELD);
  restoreHeldFiles(owner, to, held);
}

/** Explicit New Chat discards the previous blank draft, including late uploads. */
export function discardHeldFiles(owner: object, key: string): void {
  attachmentTarget(owner, key).discarded = true;
  attachmentTargets.get(owner)?.delete(key);
  updateHeld(owner, key, (current) => {
    releaseHeldFiles(current);
    return NOTHING_HELD;
  });
}

/** Let go of every draft's held files for one owner, pictures included. */
export function forgetHeldFiles(owner: object): void {
  attachmentTargets.delete(owner);
  const drafts = parked.get(owner);
  if (!drafts) return;
  drafts.forEach((held) => Object.values(held.previews).forEach(revokePreview));
  parked.delete(owner);
  listeners.forEach((listener) => listener());
}

function subscribeHeld(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * Hold files for one chat's next message.
 *
 * `onProblem` is where the caller puts its own error line — this hook does not
 * decide how a surface reports things. It DOES decide how bad the news is: a
 * drop that carried nothing usable is a warning about what was dragged, while
 * an attach that threw is an error about the app, and collapsing the two
 * buries the second.
 *
 * `draft` keeps the files across unmounts: the same owner and key get them
 * back, the way a composer gets its unsent text back. Without it the files
 * live and die with the composer.
 */
export function useChatAttachments(
  target: {
    sessionId: string | null;
    cwd: string;
    provider: string;
    surface: AgentChatSurface;
  },
  onProblem: (message: string, severity: "warning" | "error") => void,
  draft?: { owner: object; key: string },
): ChatAttachments {
  const ownRef = useRef<object | null>(null);
  ownRef.current ??= {};
  const owner = draft?.owner ?? ownRef.current;
  const key = draft?.key ?? "";
  const kept = draft !== undefined;
  const held = useSyncExternalStore(subscribeHeld, () => heldFor(owner, key));
  const { attachments, previews } = held;
  const [analyzing, setAnalyzing] = useState(0);
  const { sessionId, cwd, provider, surface } = target;

  // Object URLs hold the picture in memory until revoked. Files that are not
  // kept for a draft go with the composer; kept ones wait for send or remove.
  useEffect(() => {
    if (kept) return;
    return () => updateHeld(owner, key, (current) => {
      Object.values(current.previews).forEach(revokePreview);
      return NOTHING_HELD;
    });
  }, [kept, owner, key]);

  const attach = useCallback(
    async (payload: PaneDropPayload) => {
      if (isEmptyPayload(payload)) return;
      const uploadTarget = attachmentTarget(owner, key);
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
          onProblem(translate("agent_chat.drop_nothing_usable"), "warning");
          return;
        }
        const pictures = matchPreviews(found, payload.files);
        if (uploadTarget.discarded) {
          Object.values(pictures).forEach(revokePreview);
          return;
        }
        if (folder) {
          for (const item of found) {
            if (!pictures[item.name] && attachmentMedia(item)) {
              pictures[item.name] = attachmentFileUrl(folder, item.reference);
            }
          }
        }
        // Written to the draft this drop was made in, even when the person
        // switched away while the file was still being read.
        updateHeld(owner, uploadTarget.key, (current) => {
          const nextPreviews = { ...current.previews };
          for (const [name, url] of Object.entries(pictures)) {
            if (nextPreviews[name]) revokePreview(nextPreviews[name]);
            nextPreviews[name] = url;
          }
          return {
            // Keyed by name so the same file attached twice is held once — a
            // repeated reference has the model read it again for nothing.
            attachments: [
              ...current.attachments,
              ...found.filter((item) => !current.attachments.some((held) => held.name === item.name)),
            ],
            previews: nextPreviews,
          };
        });
      } catch (e) {
        onProblem((e as Error).message, "error");
      } finally {
        setAnalyzing((n) => Math.max(0, n - 1));
      }
    },
    [sessionId, cwd, provider, surface, onProblem, owner, key],
  );

  const { dragging, handlers: dragHandlers } = usePaneFileDrag(
    useCallback(
      (dt: DataTransfer) => {
        // Read before returning: browsers clear DataTransfer after this event.
        // Native path hints are not a file-read grant on every WebView backend;
        // use the granted File bytes or our own explorer's receipt everywhere.
        void attach(extractPaneDrop(dt));
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
    updateHeld(owner, key, (current) => {
      const rest = { ...current.previews };
      if (rest[name]) revokePreview(rest[name]);
      delete rest[name];
      return { attachments: current.attachments.filter((a) => a.name !== name), previews: rest };
    });
  }, [owner, key]);

  const clear = useCallback(() => {
    updateHeld(owner, key, (current) => {
      Object.values(current.previews).forEach(revokePreview);
      return NOTHING_HELD;
    });
  }, [owner, key]);

  const take = useCallback((): HeldFiles => {
    const current = heldFor(owner, key);
    updateHeld(owner, key, () => NOTHING_HELD);
    return current;
  }, [owner, key]);

  const restore = useCallback((files: HeldFiles) => {
    restoreHeldFiles(owner, key, files);
  }, [owner, key]);

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
    take,
    restore,
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
