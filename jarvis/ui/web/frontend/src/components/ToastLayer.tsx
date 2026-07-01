import {
  X,
  Info,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  FolderOpen,
  ExternalLink,
} from "lucide-react";
import { useEventStore, type Toast } from "@/store/events";
import { cn } from "@/lib/utils";
import { openDownloadedFile, revealInFolder } from "@/lib/fileActions";
import { useT } from "@/i18n";

const ICON_FOR_KIND = {
  info: Info,
  success: CheckCircle2,
  warning: AlertTriangle,
  error: XCircle,
} as const;

const STYLE_FOR_KIND: Record<Toast["kind"], string> = {
  info: "border-border bg-card/95 text-foreground",
  success: "border-primary/40 bg-card/95 text-foreground shadow-[0_0_24px_rgba(255,214,10,0.15)]",
  warning: "border-amber-500/40 bg-card/95 text-foreground",
  error: "border-destructive/50 bg-card/95 text-foreground",
};

const ACCENT_FOR_KIND: Record<Toast["kind"], string> = {
  info: "text-muted-foreground",
  success: "text-primary",
  warning: "text-amber-500",
  error: "text-destructive",
};

export function ToastLayer() {
  const toasts = useEventStore((s) => s.toasts);
  const dismiss = useEventStore((s) => s.dismissToast);

  return (
    <div className="pointer-events-none fixed right-4 top-4 z-50 flex w-[320px] flex-col gap-2">
      {toasts.map((t) => {
        const Icon = ICON_FOR_KIND[t.kind];
        return (
          <div
            key={t.id}
            role="status"
            className={cn(
              "pointer-events-auto flex items-start gap-3 rounded-lg border px-3 py-2.5 text-sm backdrop-blur",
              "animate-in slide-in-from-right-4 fade-in duration-200",
              STYLE_FOR_KIND[t.kind],
            )}
          >
            <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", ACCENT_FOR_KIND[t.kind])} />
            <div className="min-w-0 flex-1 text-xs leading-relaxed">
              <div className="break-words">{t.message}</div>
              {t.filePath && <FileToastActions path={t.filePath} />}
            </div>
            <button
              type="button"
              onClick={() => dismiss(t.id)}
              className="shrink-0 text-muted-foreground transition-colors hover:text-foreground"
              aria-label="Dismiss"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}

/**
 * "Show in folder" / "Open" actions for a toast that carries a saved file path.
 *
 * Dragging a file straight out of the embedded WebView is not reliably possible
 * on any OS, so we bring the user to the real file in their file manager (from
 * which a native drag to anywhere works) — or open it directly.
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

  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      <button
        type="button"
        onClick={onReveal}
        className="inline-flex items-center gap-1 rounded-md border border-primary/40 bg-primary/10 px-2 py-1 text-[11px] font-medium text-primary transition-colors hover:bg-primary/20"
      >
        <FolderOpen className="h-3 w-3" />
        {t("file_toast.show_in_folder")}
      </button>
      <button
        type="button"
        onClick={onOpen}
        className="inline-flex items-center gap-1 rounded-md border border-border bg-background/40 px-2 py-1 text-[11px] font-medium text-foreground/90 transition-colors hover:bg-background/70"
      >
        <ExternalLink className="h-3 w-3" />
        {t("file_toast.open")}
      </button>
    </div>
  );
}
