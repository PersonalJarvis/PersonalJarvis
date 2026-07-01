/**
 * Top-bar button: cancels ALL non-terminal missions.
 *
 * shadcn's AlertDialog isn't installed (see the brief), so we build the
 * confirmation modal ourselves — based on the existing Card primitives.
 * A wrapper backdrop + Esc/outside click closes it, "Confirm" cancels.
 */
import { useEffect, useState } from "react";
import { AlertTriangle, Loader2, Skull } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { cancelAllMissions } from "./api";
import { selectActiveCount, useMissionsStore } from "./store";
import { useShallow } from "zustand/react/shallow";

export function GlobalKillButton() {
  const t = useT();
  const [open, setOpen] = useState(false);
  const qc = useQueryClient();
  const activeCount = useMissionsStore(useShallow(selectActiveCount));

  const mut = useMutation({
    mutationFn: cancelAllMissions,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["missions"] });
      setOpen(false);
    },
  });

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <>
      <Button
        variant="destructive"
        size="sm"
        onClick={() => setOpen(true)}
        disabled={activeCount === 0 || mut.isPending}
        title={t("global_kill_button.cancel_all_running")}
      >
        <Skull className="mr-1.5 h-4 w-4" />
        Kill All ({activeCount})
      </Button>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
          onClick={(e) => {
            if (e.target === e.currentTarget) setOpen(false);
          }}
        >
          <div className="card-outline mx-4 w-full max-w-md bg-card p-5 shadow-2xl">
            <div className="flex items-start gap-3">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-destructive/40 bg-destructive/10">
                <AlertTriangle className="h-5 w-5 text-destructive" />
              </div>
              <div className="min-w-0 flex-1">
                <h3 className="font-display text-base font-semibold">
                  {t("global_kill_button.cancel_all_title")}
                </h3>
                <p className="mt-1 text-sm text-muted-foreground">
                  {activeCount} {t("global_kill_button.cancel_all_body")}
                </p>
                {mut.isError && (
                  <p className="mt-2 rounded border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
                    {t("global_kill_button.error_prefix")}: {(mut.error as Error).message}
                  </p>
                )}
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setOpen(false)}
                disabled={mut.isPending}
              >
                {t("common.cancel")}
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={() => mut.mutate()}
                disabled={mut.isPending}
              >
                {mut.isPending ? (
                  <>
                    <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                    {t("global_kill_button.stopping")}
                  </>
                ) : (
                  <>
                    <Skull className="mr-1.5 h-4 w-4" />
                    {t("global_kill_button.confirm_stop_all")}
                  </>
                )}
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
