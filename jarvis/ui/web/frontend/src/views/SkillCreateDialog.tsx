/**
 * SkillCreateDialog — Stub nach Filesystem-Reset 2026-04-25.
 * Vollstaendige Implementation folgt; aktuell minimaler Close-only-Dialog
 * damit der TS-Build durchlaeuft und die SkillsView nicht crasht.
 */
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";

export function SkillCreateDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
  onCreated?: (name: string) => void;
}) {
  const t = useT();
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="flex w-full max-w-md flex-col rounded-xl border border-border bg-card shadow-lg">
        <div className="flex items-start justify-between gap-4 border-b border-border p-5">
          <div className="min-w-0 flex-1">
            <h3 className="font-display text-base font-semibold">
              {t("skill_create_dialog.title")}
            </h3>
            <p className="mt-1 text-[11px] text-muted-foreground">
              {t("skill_create_dialog.rebuild_notice_a")}{" "}
              <code>~/.jarvis/skills/</code> {t("skill_create_dialog.rebuild_notice_b")}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-border p-4">
          <Button type="button" variant="ghost" onClick={onClose}>
            {t("common.close")}
          </Button>
        </div>
      </div>
    </div>
  );
}
