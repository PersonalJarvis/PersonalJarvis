// FrontierSwitchModal — blockierendes Modal das beim Boot oeffnet wenn
// Hauptjarvis automatisch auf ein neueres Provider-Modell gewechselt hat.
//
// User-Mandat 2026-04-28: Auto-Switch ist erlaubt; nach Switch muss der
// User mit OK bestaetigen. Das Modal verschwindet nicht ohne OK.
// Quelle: GET /api/frontier/pending; Quittung via POST /api/frontier/ack.
import { useEffect, useState } from "react";
import { Sparkles, ArrowRight } from "lucide-react";
import { cn } from "@/lib/utils";

interface FrontierSwitch {
  provider: string;
  tier: string;
  old_model: string;
  new_model: string;
  switched_at: number;
}

const POLL_INTERVAL_MS = 30_000;

export function FrontierSwitchModal() {
  const [pending, setPending] = useState<FrontierSwitch[]>([]);
  const [acking, setAcking] = useState(false);

  // Initial-Load + Polling. WS-Subscribe waere robuster, aber Polling
  // mit 30s ist genug — der Switch passiert nur einmal beim Boot, und
  // das Modal blockiert sowieso bis der User klickt.
  useEffect(() => {
    let cancelled = false;

    const fetchPending = async () => {
      try {
        const res = await fetch("/api/frontier/pending");
        if (!res.ok) return;
        const data = (await res.json()) as FrontierSwitch[];
        if (!cancelled) setPending(data);
      } catch {
        // ignoriert — naechster Tick versucht's wieder
      }
    };

    void fetchPending();
    const t = setInterval(fetchPending, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

  const handleOk = async () => {
    if (acking) return;
    setAcking(true);
    try {
      await fetch("/api/frontier/ack", { method: "POST" });
      setPending([]);
    } catch {
      // Falls der Ack-Call failt: Modal sichtbar lassen, User kann
      // erneut klicken. Server-Side bleibt der Pending-State erhalten.
    } finally {
      setAcking(false);
    }
  };

  if (pending.length === 0) return null;

  // Group by provider fuer schoenere Darstellung wenn beide Tiers
  // gleichzeitig gewechselt sind (z.B. Anthropic Opus 4.7 → 4.8 + Haiku 4.5 → 4.6).
  const byProvider = pending.reduce<Record<string, FrontierSwitch[]>>((acc, s) => {
    if (!acc[s.provider]) acc[s.provider] = [];
    acc[s.provider].push(s);
    return acc;
  }, {});

  return (
    <div
      className={cn(
        "fixed inset-0 z-[60] flex items-center justify-center",
        "bg-background/80 backdrop-blur-sm",
        "animate-in fade-in duration-200",
      )}
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="frontier-modal-title"
    >
      <div
        className={cn(
          "w-full max-w-lg rounded-2xl border border-primary/40 bg-card",
          "p-6 shadow-[0_0_60px_rgba(255,214,10,0.18)]",
          "animate-in zoom-in-95 fade-in duration-200",
        )}
      >
        <div className="mb-4 flex items-center gap-2">
          <Sparkles className="h-5 w-5 text-primary" />
          <h2
            id="frontier-modal-title"
            className="text-base font-semibold text-foreground"
          >
            Frontier-Modell{pending.length === 1 ? "" : "e"} aktualisiert
          </h2>
        </div>

        <p className="mb-4 text-sm text-muted-foreground">
          Jarvis hat beim Start ein neueres Modell beim Anbieter erkannt
          und automatisch gewechselt. Bitte bestaetige.
        </p>

        <div className="mb-5 space-y-3">
          {Object.entries(byProvider).map(([provider, switches]) => (
            <div
              key={provider}
              className="rounded-lg border border-border bg-background/40 p-3"
            >
              <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-primary">
                {provider}
              </div>
              <ul className="space-y-1.5">
                {switches.map((s) => (
                  <li
                    key={`${s.provider}-${s.tier}`}
                    className="flex items-center gap-2 text-xs"
                  >
                    <span className="rounded border border-border bg-secondary/40 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                      {s.tier}
                    </span>
                    <span className="font-mono text-muted-foreground line-through">
                      {s.old_model}
                    </span>
                    <ArrowRight className="h-3 w-3 shrink-0 text-primary" />
                    <span className="font-mono text-foreground">
                      {s.new_model}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="flex justify-end">
          <button
            type="button"
            onClick={handleOk}
            disabled={acking}
            className={cn(
              "rounded-md border border-primary bg-primary px-4 py-2",
              "text-sm font-semibold text-primary-foreground",
              "transition-opacity hover:opacity-90",
              "disabled:cursor-not-allowed disabled:opacity-50",
            )}
          >
            {acking ? "Bestaetige…" : "OK"}
          </button>
        </div>
      </div>
    </div>
  );
}
