import { Keyboard, Loader2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { requestPermission } from "@/lib/permissionsApi";
import { cn } from "@/lib/utils";
import { SHORTCUTS_FEATURE } from "./ShortcutsStatusNote";
import { WRAPPING_ACTION_BUTTON } from "./promptActions";

/**
 * The dismissible tip after the first UI-started dictation: "Dictate from any
 * app", with the one action that makes it true. The ask happens FROM the click
 * (`POST /api/permissions/input_monitoring/request`); whatever macOS needs next
 * (its dialog, or the Settings switch) is explained by the permission card,
 * which is not suppressed here. `onDone` closes the tip either way.
 */
export function ShortcutsTip({
  onDone,
  className,
}: {
  onDone: () => void;
  className?: string;
}) {
  const t = useT();
  const [busy, setBusy] = useState(false);

  const enable = async () => {
    setBusy(true);
    try {
      await requestPermission("input_monitoring", { feature: SHORTCUTS_FEATURE });
    } catch {
      // The Shortcuts page carries the permanent explanation and a retry; the
      // tip must not turn into an error message of its own.
    } finally {
      setBusy(false);
      onDone();
    }
  };

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="shortcuts-tip"
      className={cn("flex items-start gap-2 text-meta text-muted-foreground", className)}
    >
      <Keyboard aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="font-medium text-foreground">{t("permissions.shortcuts.tip_title")}</p>
        <p className="mt-0.5 break-words">{t("permissions.shortcuts.tip_body")}</p>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <Button
            type="button"
            size="sm"
            className={WRAPPING_ACTION_BUTTON}
            disabled={busy}
            onClick={() => void enable()}
            data-action="enable"
          >
            {busy && <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden />}
            {t("permissions.shortcuts.enable")}
          </Button>
          <Button
            type="button"
            size="sm"
            className={WRAPPING_ACTION_BUTTON}
            variant="ghost"
            disabled={busy}
            onClick={onDone}
            data-action="dismiss"
          >
            {t("permissions.inline.dismiss")}
          </Button>
        </div>
      </div>
    </div>
  );
}
