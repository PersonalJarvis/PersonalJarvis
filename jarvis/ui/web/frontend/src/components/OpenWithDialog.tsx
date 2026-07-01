/**
 * "Open with…" chooser for an Outputs artifact.
 *
 * Self-contained dialog: lists the apps the backend detected on this host
 * (editors like VS Code, the OS default app, a browser) and lets the user pick
 * one, optionally remembering it as the default. It never touches the global
 * event store; its only outputs are the `onPick` / `onClose` callbacks.
 */
import { useEffect, useState } from "react";
import { AppWindow, FileText, Globe, Loader2, X } from "lucide-react";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import type { OpenerInfo } from "@/hooks/useOutputs";

export interface OpenWithDialogProps {
  /** The apps that can open the artifact (from `useOpeners`). */
  openers: OpenerInfo[];
  /** True while the opener list is still being detected. Shows a spinner
   *  instead of the misleading "no apps detected" hint during that window. */
  loading?: boolean;
  /** Called with the chosen opener id and whether to remember it as default. */
  onPick: (opener: string, remember: boolean) => void;
  /** Called when the user dismisses the dialog (X, Escape, click-outside). */
  onClose: () => void;
}

/** Localise the structural openers; editor labels are proper names from the
 *  backend (VS Code, Cursor, …) and pass through unchanged. */
function openerLabel(o: OpenerInfo, t: (k: string) => string): string {
  if (o.id === "default") return t("outputs_view.opener_default");
  if (o.id === "browser") return t("outputs_view.opener_browser");
  return o.label;
}

function OpenerIcon({ id }: { id: string }) {
  const cls = "h-4 w-4 text-muted-foreground";
  if (id === "default") return <FileText className={cls} />;
  if (id === "browser") return <Globe className={cls} />;
  return <AppWindow className={cls} />;
}

export function OpenWithDialog({
  openers,
  loading = false,
  onPick,
  onClose,
}: OpenWithDialogProps) {
  const t = useT();
  const [remember, setRemember] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/70 backdrop-blur-sm"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="w-full max-w-sm rounded-xl border border-border bg-card p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-foreground">
            {t("outputs_view.open_with_title")}
          </h3>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-muted-foreground hover:bg-secondary/40"
            aria-label={t("common.cancel")}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {openers.length === 0 ? (
          <div className="flex items-center justify-center gap-2 py-4 text-center text-xs text-muted-foreground">
            {loading ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                {t("outputs_view.open_with_loading")}
              </>
            ) : (
              t("outputs_view.open_with_empty")
            )}
          </div>
        ) : (
          <ul className="flex flex-col gap-1">
            {openers.map((o) => (
              <li key={o.id}>
                <button
                  type="button"
                  onClick={() => onPick(o.id, remember)}
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded-lg border border-transparent",
                    "px-3 py-2 text-left text-sm text-foreground/90",
                    "hover:border-primary/40 hover:bg-primary/10",
                  )}
                >
                  <OpenerIcon id={o.id} />
                  <span className="truncate">{openerLabel(o, t)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}

        {openers.length > 0 && (
          <label className="mt-4 flex cursor-pointer items-center gap-2 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
              className="h-3.5 w-3.5 accent-primary"
            />
            {t("outputs_view.open_with_remember")}
          </label>
        )}
      </div>
    </div>
  );
}
