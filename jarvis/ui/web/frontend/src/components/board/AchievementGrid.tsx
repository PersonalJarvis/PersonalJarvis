import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Award, Lock, Sparkles } from "lucide-react";
import { useAchievements, type AchievementItem } from "@/hooks/useBoard";
import { cn } from "@/lib/utils";

/**
 * Vollstaendige Trophaeen-Wand. Unlocked in Primary-Farbe, locked grayed
 * mit Lock-Icon. Kein Badge-Counter fuer andere Nutzer (Plan §0 — keine
 * oeffentlichen Like-Counts); die Wand ist privat, nur der User sieht sie.
 */
export function AchievementGrid() {
  const qc = useQueryClient();
  const { data, isLoading } = useAchievements();

  // Live-Unlock via WS — wenn der Evaluator einen Unlock publisht,
  // invalidiert das den React-Query-Cache und die Karte fuellt sich
  // ohne Polling-Delay.
  useEffect(() => {
    const handler = () => qc.invalidateQueries({ queryKey: ["board", "achievements"] });
    window.addEventListener("jarvis:achievement-unlocked", handler as EventListener);
    return () => window.removeEventListener(
      "jarvis:achievement-unlocked", handler as EventListener,
    );
  }, [qc]);

  if (isLoading) {
    return <div className="h-24 animate-pulse rounded-md bg-muted/10" />;
  }
  if (!data) return null;

  const mastery = data.items.filter((i) => i.tier === "mastery");
  const reflection = data.items.filter((i) => i.tier === "reflection");

  return (
    <section className="space-y-4 rounded-xl border border-border bg-card/30 p-5 backdrop-blur">
      <header className="flex items-start gap-3">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-primary/40 bg-primary/10 text-primary">
          <Award className="h-4 w-4" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="font-display text-sm font-semibold">Achievements</h3>
          <p className="text-xs text-muted-foreground">
            {data.unlocked} von {data.total} freigeschaltet. Kein Leaderboard.
          </p>
        </div>
      </header>

      {mastery.length > 0 && (
        <div className="space-y-2">
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
            Mastery
          </div>
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {mastery.map((a) => <AchievementCard key={a.id} item={a} />)}
          </div>
        </div>
      )}

      {reflection.length > 0 && (
        <div className="space-y-2">
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
            Reflection
          </div>
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {reflection.map((a) => <AchievementCard key={a.id} item={a} />)}
          </div>
        </div>
      )}
    </section>
  );
}

function AchievementCard({ item }: { item: AchievementItem }) {
  const unlocked = !!item.unlocked_at;
  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded-lg border px-3 py-2.5 transition-colors",
        unlocked
          ? "border-primary/40 bg-primary/5"
          : "border-border/50 bg-muted/10 opacity-70",
      )}
    >
      <div
        className={cn(
          "flex h-7 w-7 shrink-0 items-center justify-center rounded-md",
          unlocked
            ? "bg-primary/20 text-primary"
            : "bg-muted/30 text-muted-foreground",
        )}
      >
        {unlocked ? <Sparkles className="h-3.5 w-3.5" /> : <Lock className="h-3 w-3" />}
      </div>
      <div className="min-w-0 flex-1">
        <div
          className={cn(
            "text-sm font-medium",
            unlocked ? "text-foreground" : "text-muted-foreground",
          )}
        >
          {item.title}
        </div>
        <div className="text-[11px] leading-snug text-muted-foreground">
          {item.description}
        </div>
        {unlocked && item.unlocked_at && (
          <div className="mt-1 text-[10px] text-muted-foreground">
            {formatUnlockDate(item.unlocked_at)}
          </div>
        )}
      </div>
    </div>
  );
}

function formatUnlockDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString("de-DE", {
      day: "2-digit", month: "2-digit", year: "numeric",
    });
  } catch {
    return iso;
  }
}
