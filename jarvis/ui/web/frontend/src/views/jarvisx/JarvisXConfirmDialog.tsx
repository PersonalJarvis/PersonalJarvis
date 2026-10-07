import { useEffect, useRef } from "react";

import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";

/**
 * An in-app confirmation. Never `window.confirm`: a native dialog blocks the
 * desktop WebView's loop and looks foreign in both themes.
 */
export function JarvisXConfirmDialog({
  title,
  body,
  confirmLabel,
  onCancel,
  onConfirm,
}: {
  title: string;
  body: string;
  confirmLabel: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const t = useT();
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    cancelRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onCancel();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onCancel]);

  return (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center bg-scrim/60 p-4"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) onCancel();
      }}
    >
      <div
        role="alertdialog"
        aria-modal="true"
        aria-label={title}
        data-testid="jarvisx-confirm"
        className="w-full max-w-[420px] rounded-xl border border-border bg-card p-6 shadow-2xl"
      >
        <h3 className="text-base font-semibold text-foreground">{title}</h3>
        <p className="mt-2 text-sm text-muted-foreground">{body}</p>
        <div className="mt-5 flex justify-end gap-2">
          <Button ref={cancelRef} type="button" size="sm" variant="ghost" onClick={onCancel}>
            {t("common.cancel")}
          </Button>
          <Button type="button" size="sm" variant="destructive" onClick={onConfirm} data-testid="jarvisx-confirm-ok">
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
