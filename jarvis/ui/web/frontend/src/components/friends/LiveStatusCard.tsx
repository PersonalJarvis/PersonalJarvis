// === F-FRIENDS [F4] · feature/friends-section · ruben-2026-05-01 ===
import { Activity } from "lucide-react";

/**
 * Display-Komponente fuer EINEN ausgehenden StatusUpdate.
 *
 * In Phase F4 ist diese Komponente nur die UI-Library — sie wird in F5
 * an einen WebSocket-Stream angeschlossen, der die letzten N Updates
 * pro Friend anzeigt.
 *
 * Schema-Gleichschritt mit ``jarvis.friends.schemas.StatusUpdate``:
 *   - event_type, timestamp_ns, fields, profile_used.
 */
export interface StatusUpdateView {
  event_type: string;
  timestamp_ns: number;
  fields: Record<string, unknown>;
  profile_used: "minimal" | "standard" | "detailed";
}

const PROFILE_BADGE_CLASS: Record<StatusUpdateView["profile_used"], string> = {
  minimal: "border-muted-foreground/40 bg-muted/30 text-muted-foreground",
  standard: "border-primary/40 bg-primary/10 text-primary",
  detailed: "border-amber-400/40 bg-amber-400/10 text-amber-400",
};

function formatTimestamp(ns: number): string {
  if (!ns) return "-";
  const ms = Math.floor(ns / 1_000_000);
  const date = new Date(ms);
  return date.toLocaleTimeString();
}

export function LiveStatusCard({ update }: { update: StatusUpdateView }) {
  const fieldEntries = Object.entries(update.fields);
  return (
    <div className="rounded-lg border border-border bg-card/40 p-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Activity className="h-3.5 w-3.5 text-primary" />
          <span className="font-display text-xs font-semibold text-foreground">
            {update.event_type}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={`rounded-full border px-1.5 py-0.5 text-[9px] uppercase tracking-wider ${PROFILE_BADGE_CLASS[update.profile_used]}`}
          >
            {update.profile_used}
          </span>
          <span className="text-[10px] text-muted-foreground">
            {formatTimestamp(update.timestamp_ns)}
          </span>
        </div>
      </div>
      {fieldEntries.length > 0 && (
        <dl className="mt-2 space-y-0.5 text-[11px]">
          {fieldEntries.map(([key, value]) => (
            <div key={key} className="flex gap-2">
              <dt className="text-muted-foreground">{key}:</dt>
              <dd className="truncate text-foreground/90">{String(value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
