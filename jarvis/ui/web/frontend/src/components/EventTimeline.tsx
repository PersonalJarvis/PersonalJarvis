import { useState } from "react";
import { useEventStore, type EventItem } from "@/store/events";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";

const LAYER_COLOR: Record<string, string> = {
  bus:        "bg-slate-500",
  brain:      "bg-violet-500",
  voice:      "bg-emerald-500",
  audio:      "bg-emerald-500",
  tool:       "bg-amber-500",
  skill:      "bg-sky-500",
  ui:         "bg-blue-500",
  harness:    "bg-fuchsia-500",
  mcp:        "bg-teal-500",
  channel:    "bg-indigo-500",
  system:     "bg-zinc-500",
  debug:      "bg-gray-500",
};

function layerColor(layer?: string): string {
  if (!layer) return "bg-gray-500";
  return LAYER_COLOR[layer] ?? "bg-gray-500";
}

function fmtTime(ts: number): string {
  const d = new Date(ts);
  return d.toLocaleTimeString(undefined, { hour12: false }) + "." +
    String(d.getMilliseconds()).padStart(3, "0");
}

export function EventTimeline() {
  const events = useEventStore((s) => s.events);
  const visible = events.slice(0, 100);

  return (
    <ScrollArea className="h-full">
      <ul className="divide-y divide-border">
        {visible.length === 0 && (
          <li className="p-4 text-sm text-muted-foreground">
            No events yet. Emit a test event from the Debug tab.
          </li>
        )}
        {visible.map((e) => (
          <EventRow key={e.id} event={e} />
        ))}
      </ul>
    </ScrollArea>
  );
}

function EventRow({ event }: { event: EventItem }) {
  const [open, setOpen] = useState(false);
  const hasPayload = event.payload !== undefined && event.payload !== null;
  return (
    <li className="px-4 py-2 text-sm">
      <div className="flex items-center gap-3">
        <span
          className={cn("h-2 w-2 rounded-full shrink-0", layerColor(event.layer))}
          aria-hidden
        />
        <span className="font-mono text-xs text-muted-foreground tabular-nums">
          {fmtTime(event.ts)}
        </span>
        <span className="font-medium truncate flex-1">{event.name}</span>
        {event.layer && (
          <span className="text-xs text-muted-foreground uppercase">{event.layer}</span>
        )}
        {hasPayload && (
          <button
            type="button"
            className="text-xs text-muted-foreground hover:text-foreground"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
          >
            {open ? "hide" : "payload"}
          </button>
        )}
      </div>
      {open && hasPayload && (
        <pre className="mt-2 overflow-auto rounded bg-muted/50 p-2 text-xs">
          {JSON.stringify(event.payload, null, 2)}
        </pre>
      )}
    </li>
  );
}
