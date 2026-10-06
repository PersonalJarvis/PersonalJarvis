import { createPortal } from "react-dom";
import {
  X,
  Info,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  FolderOpen,
  ExternalLink,
  GripVertical,
} from "lucide-react";
import { useEventStore, type Toast } from "@/store/events";
import { cn } from "@/lib/utils";
import { openDownloadedFile, revealInFolder } from "@/lib/fileActions";
import { canNativeDrag, startNativeFileDrag } from "@/lib/nativeDrag";
import { useT } from "@/i18n";

const ICON_FOR_KIND = {
  info: Info,
  success: CheckCircle2,
  warning: AlertTriangle,
  error: XCircle,
} as const;

const STYLE_FOR_KIND: Record<Toast["kind"], string> = {
  info: "border-border bg-card/95 text-foreground",
  success: "border-primary/40 bg-card/95 text-foreground",
  warning: "border-foreground/40 bg-card/95 text-foreground",
  error: "border-destructive/50 bg-card/95 text-foreground",
};

const ACCENT_FOR_KIND: Record<Toast["kind"], string> = {
  info: "text-muted-foreground",
  success: "text-primary",
  warning: "text-foreground",
  error: "text-destructive",
};

export function ToastLayer() {
  const t = useT();
  const toasts = useEventStore((s) => s.toasts);
  const dismiss = useEventStore((s) => s.dismissToast);

  return createPortal(
    // z-[115]: above the first-run setup spotlight (z-110), whose scrim takes
    // every click outside its hole: a toast with a button ("Open System
    // Settings" after the wake-word switch asked for the microphone) must stay
    // clickable there. Also above the full-window appshot editor (z-95), whose
    // Save toast carries the "Show in folder" action. Below the caption bar
    // (z-120), which is every window's title bar. A portal to <body> because the spotlight is one too: inside the
    // shell root (an isolated stacking context) no z-index could outrank it.
    // top-12: below the caption strip — at the top edge a toast covered the
    // restart and window buttons whose outcome it reports.
    <div className="pointer-events-none fixed right-4 top-12 z-[115] flex w-[320px] flex-col gap-2">
      {toasts.map((toast) => {
        const Icon = ICON_FOR_KIND[toast.kind];
        // A saved-file toast in the desktop shell is a native drag handle: press
        // and drag it to drop the real file into any app. The buttons stay as a
        // fallback (and are the only path in a plain browser / on the VPS).
        const draggable = Boolean(toast.filePath) && canNativeDrag();
        return (
          <div
            key={toast.id}
            role="status"
            draggable={false}
            onDragStart={draggable ? (e) => e.preventDefault() : undefined}
            onMouseDown={
              draggable
                ? (e) => {
                    if (e.button === 0 && toast.filePath) {
                      startNativeFileDrag(toast.filePath);
                    }
                  }
                : undefined
            }
            title={draggable ? t("file_toast.drag_hint") : undefined}
            className={cn(
              "pointer-events-auto flex items-start gap-3 rounded-lg border px-3 py-2.5 text-sm backdrop-blur",
              "animate-in slide-in-from-right-4 fade-in duration-200",
              STYLE_FOR_KIND[toast.kind],
              draggable && "cursor-grab active:cursor-grabbing select-none",
            )}
          >
            {draggable ? (
              <GripVertical className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
            ) : (
              <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", ACCENT_FOR_KIND[toast.kind])} />
            )}
            <div className="min-w-0 flex-1 text-xs leading-relaxed">
              <div className="flex items-start gap-2">
                <span className="min-w-0 flex-1 break-words">{toast.message}</span>
                {/* Repeats collapse into this counter instead of stacking a
                    fresh box per click, which used to bury the control the
                    message is asking the user to press. */}
                {toast.count > 1 && (
                  <span
                    data-testid={`toast-repeat-${toast.count}`}
                    aria-label={`Repeated ${toast.count} times`}
                    className={cn(
                      "mt-px shrink-0 rounded-full border border-current/30 px-1.5 py-px text-micro font-semibold tabular-nums",
                      ACCENT_FOR_KIND[toast.kind],
                    )}
                  >
                    ×{toast.count}
                  </span>
                )}
              </div>
              {draggable && (
                <div className="mt-1 text-xs font-medium text-primary/80">
                  {t("file_toast.drag_hint")}
                </div>
              )}
              {toast.filePath && (
                <FileToastActions path={toast.filePath} />
              )}
              {toast.action && <ToastActionButton toast={toast} onDone={() => dismiss(toast.id)} />}
            </div>
            <button
              type="button"
              // Don't let a click on the X start a drag.
              onMouseDown={(e) => e.stopPropagation()}
              onClick={() => dismiss(toast.id)}
              className="shrink-0 text-muted-foreground transition-colors hover:text-foreground"
              aria-label="Dismiss"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        );
      })}
    </div>,
    document.body,
  );
}

/**
 * The one button a toast may carry (`pushToast(..., { action })`), in the same
 * look as the saved-file buttons below. Runs the action, then closes the toast
 * unless the action asked to stay (a retry).
 */
function ToastActionButton({ toast, onDone }: { toast: Toast; onDone: () => void }) {
  const action = toast.action;
  if (!action) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5" onMouseDown={(e) => e.stopPropagation()}>
      <button
        type="button"
        data-testid="toast-action"
        onClick={() => {
          void action.onAction();
          if (!action.keepOpen) onDone();
        }}
        className="inline-flex items-center rounded-md border border-primary/40 bg-primary/10 px-2 py-1 text-xs font-medium text-primary transition-colors hover:bg-primary/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {action.label}
      </button>
    </div>
  );
}

/**
 * "Show in folder" / "Open" actions for a toast that carries a saved file path.
 * These are the reliable fallback everywhere (and the only path in a plain
 * browser / on the VPS, where the native drag handle is unavailable).
 */
function FileToastActions({ path }: { path: string }) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);

  const onReveal = async () => {
    const ok = await revealInFolder(path);
    if (!ok) pushToast("error", t("file_toast.reveal_failed"));
  };
  const onOpen = async () => {
    const ok = await openDownloadedFile(path);
    if (!ok) pushToast("error", t("file_toast.open_failed"));
  };

  // stopPropagation on mousedown so clicking a button never starts a file drag.
  return (
    <div className="mt-2 flex flex-wrap gap-1.5" onMouseDown={(e) => e.stopPropagation()}>
      <button
        type="button"
        onClick={onReveal}
        className="inline-flex items-center gap-1 rounded-md border border-primary/40 bg-primary/10 px-2 py-1 text-xs font-medium text-primary transition-colors hover:bg-primary/20"
      >
        <FolderOpen className="h-3 w-3" />
        {t("file_toast.show_in_folder")}
      </button>
      <button
        type="button"
        onClick={onOpen}
        className="inline-flex items-center gap-1 rounded-md border border-border bg-background/40 px-2 py-1 text-xs font-medium text-foreground/90 transition-colors hover:bg-background/70"
      >
        <ExternalLink className="h-3 w-3" />
        {t("file_toast.open")}
      </button>
    </div>
  );
}
