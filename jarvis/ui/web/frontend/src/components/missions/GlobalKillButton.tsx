/**
 * Top-Bar-Button: cancelt ALLE non-terminalen Missions.
 *
 * shadcn-AlertDialog ist nicht installiert (siehe Brief), darum bauen wir das
 * Confirmation-Modal selbst — basierend auf den vorhandenen Card-Primitives.
 * Ein Wrapper-Backdrop + Esc/Outside-Click schliesst, "Bestaetigen" cancelled.
 */
import { useEffect, useState } from "react";
import { AlertTriangle, Loader2, Skull } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { cancelAllMissions } from "./api";
import { selectActiveCount, useMissionsStore } from "./store";
import { useShallow } from "zustand/react/shallow";

export function GlobalKillButton() {
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
        title="Alle laufenden Missions abbrechen"
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
                  Alle Missions abbrechen?
                </h3>
                <p className="mt-1 text-sm text-muted-foreground">
                  {activeCount} laufende Mission(s) werden hart gestoppt.
                  Worker-Subprocesses werden via Job-Object terminiert; bereits
                  geschriebene Diffs bleiben in den Worktrees liegen.
                </p>
                {mut.isError && (
                  <p className="mt-2 rounded border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
                    Fehler: {(mut.error as Error).message}
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
                Abbrechen
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
                    Stoppe…
                  </>
                ) : (
                  <>
                    <Skull className="mr-1.5 h-4 w-4" />
                    Ja, alle stoppen
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
