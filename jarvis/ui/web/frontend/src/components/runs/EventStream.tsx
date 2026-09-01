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
 * still navigable.
 */
import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Copy, Search } from "lucide-react";

import { robustCopy } from "@/lib/clipboard";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

import type { RawEvent } from "./types";

/**
 * Nine lanes used to mean nine hues — slate, violet, fuchsia, sky, cyan, rose
 * and near-white — which spent the product's entire colour budget on a filter
 * strip and left the one lane that matters (`error`) no louder than the rest.
 *
 * The lane NAME is printed in the chip and the event `kind` is printed in the
 * row, so the words already carry the distinction. Only `error` keeps a hue,
 * because only `error` is a status.
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
      <span className="text-body text-muted-foreground">
        {t("run_inspector.stream.empty")}
      </span>
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
    <div className="space-y-stack" data-testid="event-stream">
      {/* Lane filters + search */}
      <div className="flex flex-wrap items-center gap-1.5">
        {Object.entries(counts)
          .sort((a, b) => b[1] - a[1])
          .map(([cat, n]) => {
            // With no explicit selection every lane is on; a lane switched off
            // recedes rather than disappears, so the strip keeps its shape.
            const active = lanes.size === 0 || lanes.has(cat);
            return (
              <button
                key={cat}
                type="button"
                data-testid={`lane-${cat}`}
                data-active={lanes.has(cat)}
                onClick={() => toggleLane(cat)}
                className={`inline-flex items-center gap-1 rounded-full bg-secondary px-2 py-0.5 text-micro font-medium transition-opacity ${
                  isFaultLane(cat) ? "text-destructive" : "text-foreground"
                } ${active ? "" : "opacity-40"}`}
              >
                {cat}
                <span className="tabular-nums text-muted-foreground">{n}</span>
              </button>
            );
          })}
        <div className="ml-auto flex items-center gap-1.5">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground" />
            <input
              type="text"
              value={needle}
              onChange={(e) => setNeedle(e.target.value)}
              placeholder={t("run_inspector.stream.filter")}
              data-testid="event-filter"
              className="h-7 w-40 rounded-md bg-input pl-6 pr-2 text-micro text-foreground outline-none placeholder:text-faint-foreground focus:ring-2 focus:ring-border-strong"
            />
          </div>
          <button
            type="button"
            onClick={copyStream}
            title={t("run_inspector.stream.copy")}
            className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-micro text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
          >
            <Copy className="h-3 w-3" />
            JSONL
          </button>
        </div>
      </div>

      {truncated && (
        <div className="rounded-md bg-secondary px-2 py-1 text-micro text-warning">
          {t("run_inspector.stream.truncated")}
        </div>
      )}

      {/* Rows. Fill on hover is the separation device — no rules between them. */}
      <ol>
        {visible.map((e) => {
          const isOpen = open.has(e.seq);
          const hasPayload = Object.keys(e.payload ?? {}).length > 0;
          return (
            <li key={`${e.seq}-${e.ts_ms}`} data-kind={e.kind} data-category={e.category}>
              <button
                type="button"
                onClick={() => hasPayload && toggleRow(e.seq)}
                className={`flex w-full items-start gap-2 rounded-md px-2 py-1 text-left transition-colors hover:bg-secondary ${
                  hasPayload ? "" : "cursor-default"
                }`}
              >
                <span className="mt-[3px] w-4 shrink-0 text-muted-foreground">
                  {hasPayload ? (
                    isOpen ? (
                      <ChevronDown className="h-3 w-3" />
                    ) : (
                      <ChevronRight className="h-3 w-3" />
                    )
                  ) : null}
                </span>
                <span className="w-16 shrink-0 text-right font-mono text-micro tabular-nums text-muted-foreground">
                  {fmtOffset(e.offset_ms)}
                </span>
                <span
                  className={`w-48 shrink-0 font-mono text-micro ${
                    isFaultLane(e.category) ? "text-destructive" : "text-foreground"
                  }`}
                >
                  {e.kind}
                </span>
                {/* No truncation: the summary IS the information. Long lines
                    wrap instead of being cut at the container edge. */}
                <span className="min-w-0 flex-1 break-words text-micro text-muted-foreground [overflow-wrap:anywhere]">
                  {e.summary}
                </span>
              </button>
              {isOpen && (
                <pre className="overflow-x-auto rounded-md bg-secondary px-3 py-2 font-mono text-micro text-muted-foreground">
                  {JSON.stringify(e.payload, null, 2)}
                </pre>
              )}
            </li>
          );
        })}
      </ol>

      <div className="text-micro tabular-nums text-muted-foreground">
        {visible.length === events.length
          ? `${events.length} ${t("run_inspector.stream.events")}`
          : `${visible.length} / ${events.length} ${t("run_inspector.stream.events")}`}
      </div>
    </div>
  );
}
