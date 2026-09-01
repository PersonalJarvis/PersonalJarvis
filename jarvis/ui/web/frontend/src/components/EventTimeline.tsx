import { useState } from "react";
import { useEventStore, type EventItem } from "@/store/events";
import { ScrollArea } from "@/components/ui/scroll-area";

/*
 * There is no colour key here any more.
 *
 * Twelve layers used to get twelve dots out of Tailwind's own palette —
 * slate, violet, sky, blue, fuchsia, indigo, zinc, gray — which is twelve
 * literal colours and, worse, twelve hues in a product where hue means life,
 * fault, degraded or identity and nothing else. A reader had no key to decode
 * them with, and the row already prints the layer's name in words a few
 * columns to the right. The dot was a legend for a legend.
 */

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
          <li className="p-4 text-body text-muted-foreground">
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
    <li className="px-4 py-2">
      <div className="flex items-center gap-stack">
        <span className="font-mono text-meta tabular-nums text-muted-foreground">
          {fmtTime(event.ts)}
        </span>
        <span className="min-w-0 flex-1 truncate text-body text-foreground">{event.name}</span>
        {event.layer && (
          <span className="shrink-0 text-meta text-muted-foreground">{event.layer}</span>
        )}
        {hasPayload && (
          <button
            type="button"
            className="shrink-0 rounded-md px-1.5 py-0.5 text-meta font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
          >
            {open ? "hide" : "payload"}
          </button>
        )}
      </div>
      {open && hasPayload && (
        <pre className="mt-2 overflow-auto rounded-md bg-secondary p-2 font-mono text-meta">
          {JSON.stringify(event.payload, null, 2)}
        </pre>
      )}
    </li>
  );
}
