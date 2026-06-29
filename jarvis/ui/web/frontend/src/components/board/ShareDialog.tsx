import { useEffect, useRef, useState, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Copy, Download, Share2, X } from "lucide-react";

import { ShareCard } from "@/components/board/ShareCard";
import { useShareHandle } from "@/hooks/useShareHandle";
import {
  buildShareText,
  copyImageToClipboard,
  renderCardBlob,
  shareToX,
  type ShareStats,
} from "@/lib/shareImage";
import { saveOrDownload } from "@/lib/clipboard";
import { useCapabilities } from "@/hooks/useCapabilities";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  stats: ShareStats;
}

type Status =
  | { kind: "idle" }
  | { kind: "busy"; msgKey: string }
  | { kind: "ok"; msgKey: string }
  | { kind: "error"; msgKey: string };

const PREVIEW = 320;
const SCALE = PREVIEW / 1080;

/**
 * Modal that previews the {@link ShareCard} and exposes Copy Image / Save as
 * PNG / Share on X. The visible preview is a CSS-scaled copy; an off-screen
 * full-size card is the capture target so the exported PNG stays crisp at
 * 1080². Status is shown inline (no toast dependency).
 */
export function ShareDialog({ open, onOpenChange, stats }: Props) {
  const t = useT();
  const caps = useCapabilities();
  const native = caps.data?.native_file_actions ?? false;
  const cardRef = useRef<HTMLDivElement>(null);
  const [handle, setHandle] = useShareHandle();
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  // Pre-rendered PNG cache. Rendering on dialog-open (not on click) keeps the
  // clipboard write inside the user gesture — a slow render makes Chrome reject
  // a write that resolves too late, which is what made Copy fall back to Save.
  const blobRef = useRef<Blob | null>(null);
  const blobPromiseRef = useRef<Promise<Blob> | null>(null);

  function ensureBlob(): Promise<Blob> {
    if (blobRef.current) return Promise.resolve(blobRef.current);
    if (blobPromiseRef.current) return blobPromiseRef.current;
    const el = cardRef.current;
    if (!el) return Promise.reject(new Error("share: card not mounted"));
    const p = renderCardBlob(el).then((b) => {
      blobRef.current = b;
      return b;
    });
    blobPromiseRef.current = p;
    return p;
  }

  useEffect(() => {
    if (open) setStatus({ kind: "idle" });
  }, [open]);

  // Invalidate + pre-warm the image whenever the card content changes (open,
  // handle, or the live stat numbers).
  useEffect(() => {
    blobRef.current = null;
    blobPromiseRef.current = null;
    if (!open) return;
    const id = setTimeout(() => {
      ensureBlob().catch(() => {
        /* pre-warm is best-effort; the click path retries + surfaces errors */
      });
    }, 250);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    open,
    handle,
    stats.userWords,
    stats.jarvisWords,
    stats.conversationHours,
    stats.sessionCount,
    stats.longestStreak,
  ]);

  const busy = status.kind === "busy";

  async function run(action: () => Promise<void>): Promise<void> {
    if (!cardRef.current) return;
    setStatus({ kind: "busy", msgKey: "board_view.share.status_generating" });
    try {
      await action();
    } catch {
      setStatus({ kind: "error", msgKey: "board_view.share.status_error" });
    }
  }

  const onCopy = () =>
    run(async () => {
      // Cached blob → instant clipboard write inside the gesture; otherwise the
      // in-flight render promise is handed to the clipboard directly.
      const res = await copyImageToClipboard(blobRef.current ?? ensureBlob());
      if (res === "copied") {
        setStatus({ kind: "ok", msgKey: "board_view.share.status_copied" });
      } else {
        await saveOrDownload({
          filename: "jarvis-stats.png",
          blob: await ensureBlob(),
          native,
        });
        setStatus({ kind: "ok", msgKey: "board_view.share.status_copy_unsupported" });
      }
    });

  const onSave = () =>
    run(async () => {
      await saveOrDownload({
        filename: "jarvis-stats.png",
        blob: await ensureBlob(),
        native,
      });
      setStatus({ kind: "ok", msgKey: "board_view.share.status_saved" });
    });

  const onShareX = () =>
    run(async () => {
      const res = await shareToX(await ensureBlob(), buildShareText(stats));
      switch (res) {
        case "shared":
          setStatus({ kind: "ok", msgKey: "board_view.share.status_shared" });
          break;
        case "dismissed":
          setStatus({ kind: "idle" }); // user cancelled — clear the spinner, stay quiet
          break;
        case "composer":
          setStatus({ kind: "ok", msgKey: "board_view.share.status_composer" });
          break;
        case "blocked":
          setStatus({ kind: "error", msgKey: "board_view.share.status_blocked" });
          break;
        default:
          setStatus({ kind: "error", msgKey: "board_view.share.status_error" });
      }
    });

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 w-[min(440px,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-white/[0.08] bg-card p-5 shadow-2xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95"
          data-testid="share-dialog"
        >
          <div className="mb-3 flex items-start justify-between gap-3">
            <div>
              <Dialog.Title className="font-display text-sm font-semibold">
                {t("board_view.share.title")}
              </Dialog.Title>
              <Dialog.Description className="text-xs text-muted-foreground">
                {t("board_view.share.subtitle")}
              </Dialog.Description>
            </div>
            <Dialog.Close
              className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-white/[0.06] hover:text-foreground"
              aria-label="Close"
            >
              <X className="h-4 w-4" />
            </Dialog.Close>
          </div>

          {/* Scaled, non-captured preview */}
          <div
            className="mx-auto mb-4 overflow-hidden rounded-xl border border-white/[0.06]"
            style={{ width: PREVIEW, height: PREVIEW }}
          >
            <div
              style={{
                width: 1080,
                height: 1080,
                transform: `scale(${SCALE})`,
                transformOrigin: "top left",
              }}
            >
              <ShareCard {...stats} handle={handle} />
            </div>
          </div>

          {/* Off-screen full-size capture target. Kept opaque (only moved
              off-screen) so html-to-image captures a non-transparent PNG. */}
          <div
            aria-hidden
            style={{
              position: "fixed",
              left: -99999,
              top: 0,
              pointerEvents: "none",
            }}
          >
            <ShareCard ref={cardRef} {...stats} handle={handle} />
          </div>

          {/* Handle */}
          <label className="mb-3 block">
            <span className="mb-1 block text-[11px] text-muted-foreground">
              {t("board_view.share.handle_label")}
            </span>
            <div className="flex items-center rounded-lg border border-white/[0.08] bg-white/[0.03] px-2.5">
              <span className="text-sm text-muted-foreground">@</span>
              <input
                value={handle}
                onChange={(e) => setHandle(e.target.value)}
                placeholder={t("board_view.share.handle_placeholder")}
                className="w-full bg-transparent px-1.5 py-1.5 text-sm outline-none"
                data-testid="share-handle-input"
              />
            </div>
          </label>

          {/* Actions */}
          <div className="grid grid-cols-3 gap-2">
            <ActionButton
              icon={<Copy className="h-3.5 w-3.5" />}
              label={t("board_view.share.copy_image")}
              onClick={onCopy}
              disabled={busy}
              testId="share-copy"
            />
            <ActionButton
              icon={<Download className="h-3.5 w-3.5" />}
              label={t("board_view.share.save_png")}
              onClick={onSave}
              disabled={busy}
              testId="share-save"
            />
            <ActionButton
              icon={<Share2 className="h-3.5 w-3.5" />}
              label={t("board_view.share.share_on_x")}
              onClick={onShareX}
              disabled={busy}
              primary
              testId="share-x"
            />
          </div>

          {status.kind !== "idle" && (
            <p
              className={cn(
                "mt-3 text-[11px]",
                status.kind === "error" ? "text-destructive" : "text-muted-foreground",
              )}
              data-testid="share-status"
            >
              {t(status.msgKey)}
            </p>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function ActionButton({
  icon,
  label,
  onClick,
  disabled,
  primary,
  testId,
}: {
  icon: ReactNode;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  primary?: boolean;
  testId: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      data-testid={testId}
      className={cn(
        "inline-flex flex-col items-center justify-center gap-1 rounded-lg border px-2 py-2.5 text-[11px] font-medium transition-colors",
        primary
          ? "border-primary/40 bg-primary/[0.10] text-primary hover:bg-primary/[0.16]"
          : "border-white/[0.08] bg-white/[0.03] hover:border-primary/40 hover:bg-primary/[0.06]",
        disabled && "opacity-50",
      )}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}
