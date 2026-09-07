/**
 * The raw, verbatim bus-event stream of a turn (or of the session frame).
 *
 * Every other panel in the inspector is a *derivation* — latency, decision
 * path, tools. Derivations have blind spots: a realtime turn used to produce an
 * empty decision path, and a developer had no way to tell "nothing happened"
 * from "the analyzer does not model this path". This panel removes that
 * ambiguity by showing exactly what was recorded, in order, with the payload
 * one click away.
 *
 * Completeness only becomes readable through the lane split (speech / brain /
 * tool / vision / …) plus a text filter, so a 500-event Computer-Use turn is
 * still navigable — and the rows scroll inside their OWN box, so a long stream
 * never turns the whole view into one endless page.
 */
import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Copy, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { robustCopy } from "@/lib/clipboard";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

import type { RawEvent } from "./types";

/**
 * Nine lanes used to mean nine hues, which spent the product's entire colour
 * budget on a filter strip and left the one lane that matters (`error`) no
 * louder than the rest. The lane NAME is printed in the chip and the event
 * `kind` is printed in the row, so the words already carry the distinction.
 * Only `error` keeps a hue, because only `error` is a status.
 */
function isFaultLane(category: string): boolean {
  return category === "error";
}

function fmtOffset(ms: number): string {
  if (ms < 1000) return `+${ms}ms`;
  return `+${(ms / 1000).toFixed(2)}s`;
}

export function EventStream({
  events,
  truncated = false,
}: {
  events: RawEvent[];
  truncated?: boolean;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [lanes, setLanes] = useState<Set<string>>(new Set());
  const [needle, setNeedle] = useState("");
  const [open, setOpen] = useState<Set<number>>(new Set());

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const e of events) c[e.category] = (c[e.category] ?? 0) + 1;
    return c;
  }, [events]);

  const visible = useMemo(() => {
    const q = needle.trim().toLowerCase();
    return events.filter((e) => {
      if (lanes.size > 0 && !lanes.has(e.category)) return false;
      if (!q) return true;
      return (
        e.kind.toLowerCase().includes(q) ||
        e.summary.toLowerCase().includes(q) ||
        JSON.stringify(e.payload).toLowerCase().includes(q)
      );
    });
  }, [events, lanes, needle]);

  if (events.length === 0) {
    return (
      <p className="text-base text-muted-foreground">
        {t("run_inspector.stream.empty")}
      </p>
    );
  }

  const toggleLane = (name: string) =>
    setLanes((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  const toggleRow = (seq: number) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(seq)) next.delete(seq);
      else next.add(seq);
      return next;
    });

  const copyStream = async () => {
    // JSONL: one event per line — the shape a developer can pipe into jq.
    const text = visible
      .map((e) => JSON.stringify({ offset_ms: e.offset_ms, kind: e.kind, ...e.payload }))
      .join("\n");
    const ok = await robustCopy(text);
    pushToast(
      ok ? "success" : "error",
      ok
        ? `${visible.length} ${t("run_inspector.stream.copied")}`
        : t("run_inspector.stream.copy_failed"),
    );
  };

  return (
    <div className="space-y-3" data-testid="event-stream">
      {/* Lane filters + search */}
      <div className="flex flex-wrap items-center gap-1.5">
        {Object.entries(counts)
          .sort((a, b) => b[1] - a[1])
          .map(([cat, n]) => {
            // With no explicit selection every lane is on; a lane switched off
            // recedes rather than disappears, so the strip keeps its shape.
            const on = lanes.size === 0 || lanes.has(cat);
            const picked = lanes.has(cat);
            return (
              <button
                key={cat}
                type="button"
                aria-pressed={picked}
                data-testid={`lane-${cat}`}
                data-active={picked}
                onClick={() => toggleLane(cat)}
                className={cn(
                  "inline-flex h-6 items-center gap-1 whitespace-nowrap rounded-md border px-2 text-xs font-medium transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  picked
                    ? "border-accent/20 bg-accent-soft text-accent"
                    : on
                      ? "border-border bg-secondary text-muted-foreground hover:text-foreground"
                      : "border-border bg-transparent text-foreground-faint hover:text-muted-foreground",
                  isFaultLane(cat) && on && !picked && "text-destructive",
                )}
              >
                {cat}
                <span className="tabular-nums opacity-70">{n}</span>
              </button>
            );
          })}
        <div className="ml-auto flex items-center gap-1.5">
          <div className="relative">
            <Search
              aria-hidden
              className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
            />
            <Input
              value={needle}
              onChange={(e) => setNeedle(e.target.value)}
              placeholder={t("run_inspector.stream.filter")}
              aria-label={t("run_inspector.stream.filter")}
              data-testid="event-filter"
              className="h-8 w-44 pl-8 text-sm"
            />
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={copyStream}
            title={t("run_inspector.stream.copy")}
          >
            <Copy aria-hidden />
            JSONL
          </Button>
        </div>
      </div>

      {truncated && (
        <p className="rounded-md border border-warning/20 bg-warning/[0.12] px-3 py-2 text-sm text-warning">
          {t("run_inspector.stream.truncated")}
        </p>
      )}

      {/* Rows. Their own scroll box: a 500-event turn must not push the run
          detail into a page-long scroll. Fill on hover is the separation
          device — no rules between them. */}
      <ol className="max-h-[26rem] overflow-y-auto scrollbar-jarvis">
        {visible.map((e) => {
          const isOpen = open.has(e.seq);
          const hasPayload = Object.keys(e.payload ?? {}).length > 0;
          const Chevron = isOpen ? ChevronDown : ChevronRight;
          return (
            <li key={`${e.seq}-${e.ts_ms}`} data-kind={e.kind} data-category={e.category}>
              <button
                type="button"
                onClick={() => hasPayload && toggleRow(e.seq)}
                aria-expanded={hasPayload ? isOpen : undefined}
                className={cn(
                  "flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-secondary",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  !hasPayload && "cursor-default",
                )}
              >
                <span className="mt-0.5 w-4 shrink-0 text-muted-foreground">
                  {hasPayload && <Chevron aria-hidden className="h-3.5 w-3.5" />}
                </span>
                <span className="w-16 shrink-0 text-right font-mono text-sm tabular-nums text-muted-foreground">
                  {fmtOffset(e.offset_ms)}
                </span>
                <span
                  className={cn(
                    "w-48 shrink-0 font-mono text-sm",
                    isFaultLane(e.category) ? "text-destructive" : "text-foreground",
                  )}
                >
                  {e.kind}
                </span>
                {/* No truncation: the summary IS the information. Long lines
                    wrap instead of being cut at the container edge. */}
                <span className="min-w-0 flex-1 break-words text-sm text-muted-foreground [overflow-wrap:anywhere]">
                  {e.summary}
                </span>
              </button>
              {isOpen && (
                <pre className="mx-2 mb-1 overflow-x-auto rounded-md bg-secondary px-3 py-2 font-mono text-sm text-muted-foreground">
                  {JSON.stringify(e.payload, null, 2)}
                </pre>
              )}
            </li>
          );
        })}
      </ol>

      <p className="text-xs tabular-nums text-muted-foreground">
        {visible.length === events.length
          ? `${events.length} ${t("run_inspector.stream.events")}`
          : `${visible.length} / ${events.length} ${t("run_inspector.stream.events")}`}
      </p>
    </div>
  );
}
