import { X, Info, CheckCircle2, AlertTriangle, XCircle } from "lucide-react";
import { useEventStore, type Toast } from "@/store/events";
import { cn } from "@/lib/utils";

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
            <div className="min-w-0 flex-1 text-xs leading-relaxed">{t.message}</div>
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
