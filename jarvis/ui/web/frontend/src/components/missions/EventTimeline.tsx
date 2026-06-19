/**
 * Vertikale Timeline der Events einer selektierten Mission.
 *
 * Auto-scroll zum Ende: ScrollArea-ref + scrollHeight, getriggert wenn neue
 * Events ankommen. Wir scrollen nur wenn der User bereits "in der Naehe vom
 * Ende" ist (toleranz 60px), sonst stoeren wir manuelles Scrollen.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Clock } from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { cn } from "@/lib/utils";
import type { EventEnvelope } from "@/types/missions";
import { useMissionsStore } from "./store";

const ACTOR_COLOR: Record<string, string> = {
  hauptjarvis: "text-primary",
  kontrollierer: "text-sky-300",
  worker: "text-emerald-300",
  critic: "text-purple-300",
  ui: "text-muted-foreground",
  system: "text-muted-foreground",
};

export function EventTimeline() {
  const events = useMissionsStore(
    useShallow((s) => {
      if (!s.selectedMissionId) return [] as EventEnvelope[];
      return s.eventsByMission[s.selectedMissionId] ?? [];
    }),
  );
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const distanceFromBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distanceFromBottom < 60) {
      el.scrollTop = el.scrollHeight;
    }
  }, [events.length]);

  const items = useMemo(() => events, [events]);

  if (items.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center text-xs text-muted-foreground">
        <Clock className="h-7 w-7 text-muted-foreground/50" />
        <p>Noch keine Events fuer diese Mission.</p>
      </div>
    );
  }

  return (
    <div ref={scrollRef} className="h-full overflow-y-auto scrollbar-jarvis">
      <ol className="space-y-1 p-3">
        {items.map((env, idx) => {
          const id = `${env.event_id}-${idx}`;
          const isOpen = !!expanded[id];
          const actorClass = ACTOR_COLOR[env.source_actor] ?? "text-muted-foreground";
          return (
            <li
              key={id}
              className="rounded-md border border-border/60 bg-card/30"
            >
              <button
                type="button"
                onClick={() => setExpanded((p) => ({ ...p, [id]: !p[id] }))}
                className="flex w-full items-start gap-2 px-2 py-1.5 text-left hover:bg-background/40"
              >
                {isOpen ? (
                  <ChevronDown className="mt-0.5 h-3 w-3 text-muted-foreground" />
                ) : (
                  <ChevronRight className="mt-0.5 h-3 w-3 text-muted-foreground" />
                )}
                <span className="w-16 shrink-0 font-mono text-[10px] text-muted-foreground">
                  {formatTime(env.ts_ms)}
                </span>
                <span className="flex-1 truncate font-mono text-[11px] text-foreground/90">
                  {env.payload.event_type}
                </span>
                <span className={cn("text-[10px] uppercase tracking-wider", actorClass)}>
                  {env.source_actor}
                </span>
                {env.worker_id && (
                  <span className="font-mono text-[10px] text-muted-foreground/70">
                    w{env.worker_id.slice(0, 6)}
                  </span>
                )}
              </button>
              {isOpen && (
                <pre className="mx-2 mb-2 max-h-40 overflow-auto rounded border border-border bg-background/60 p-2 text-[10px] leading-relaxed">
                  {JSON.stringify(env.payload, null, 2)}
                </pre>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function formatTime(ms: number): string {
  try {
    const d = new Date(ms);
    return d.toLocaleTimeString();
  } catch {
    return "—";
  }
}
