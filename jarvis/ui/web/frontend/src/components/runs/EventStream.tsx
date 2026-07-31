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

// Lane -> colour. An unknown category degrades to the neutral system tone
// instead of rendering unstyled (BUG-008 string contract).
const LANE: Record<string, { dot: string; chip: string; text: string }> = {
  lifecycle: { dot: "bg-slate-400", chip: "bg-slate-400/10 text-slate-300 ring-slate-400/25", text: "text-slate-300" },
  speech: { dot: "bg-emerald-400", chip: "bg-emerald-400/10 text-emerald-300 ring-emerald-400/25", text: "text-emerald-300" },
  brain: { dot: "bg-violet-400", chip: "bg-violet-400/10 text-violet-300 ring-violet-400/25", text: "text-violet-300" },
  tool: { dot: "bg-amber-400", chip: "bg-amber-400/10 text-amber-300 ring-amber-400/25", text: "text-amber-300" },
  agent: { dot: "bg-fuchsia-400", chip: "bg-fuchsia-400/10 text-fuchsia-300 ring-fuchsia-400/25", text: "text-fuchsia-300" },
  vision: { dot: "bg-sky-400", chip: "bg-sky-400/10 text-sky-300 ring-sky-400/25", text: "text-sky-300" },
  latency: { dot: "bg-cyan-400", chip: "bg-cyan-400/10 text-cyan-300 ring-cyan-400/25", text: "text-cyan-300" },
  error: { dot: "bg-rose-500", chip: "bg-rose-500/10 text-rose-300 ring-rose-500/25", text: "text-rose-300" },
  system: { dot: "bg-muted-foreground/50", chip: "bg-muted/40 text-muted-foreground ring-border", text: "text-muted-foreground" },
};

function lane(category: string) {
  return LANE[category] ?? LANE.system;
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
    return <span className="text-muted-foreground/60">{t("run_inspector.stream.empty")}</span>;
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
    pushToast(ok ? "success" : "error", ok
      ? `${visible.length} ${t("run_inspector.stream.copied")}`
      : t("run_inspector.stream.copy_failed"));
  };

  return (
    <div className="space-y-2" data-testid="event-stream">
      {/* Lane filters + search */}
      <div className="flex flex-wrap items-center gap-1.5">
        {Object.entries(counts)
          .sort((a, b) => b[1] - a[1])
          .map(([cat, n]) => {
            const active = lanes.size === 0 || lanes.has(cat);
            return (
              <button
                key={cat}
                type="button"
                data-testid={`lane-${cat}`}
                data-active={lanes.has(cat)}
                onClick={() => toggleLane(cat)}
                className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ring-inset transition-opacity ${lane(cat).chip} ${active ? "" : "opacity-35"}`}
              >
                <span className={`h-1.5 w-1.5 rounded-full ${lane(cat).dot}`} />
                {cat}
                <span className="tabular-nums opacity-70">{n}</span>
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
              className="h-6 w-40 rounded-md border border-border/70 bg-background/60 pl-6 pr-2 text-[11px] outline-none placeholder:text-muted-foreground/70 focus:border-border"
            />
          </div>
          <button
            type="button"
            onClick={copyStream}
            title={t("run_inspector.stream.copy")}
            className="inline-flex h-6 items-center gap-1 rounded-md border border-border/70 px-2 text-[10px] text-muted-foreground transition-colors hover:border-border hover:text-foreground"
          >
            <Copy className="h-3 w-3" />
            JSONL
          </button>
        </div>
      </div>

      {truncated && (
        <div className="rounded-md border border-amber-400/30 bg-amber-400/5 px-2 py-1 text-[10px] text-amber-300">
          {t("run_inspector.stream.truncated")}
        </div>
      )}

      {/* Rows */}
      <ol className="divide-y divide-border/40 overflow-hidden rounded-md border border-border/50">
        {visible.map((e) => {
          const isOpen = open.has(e.seq);
          const hasPayload = Object.keys(e.payload ?? {}).length > 0;
          return (
            <li key={`${e.seq}-${e.ts_ms}`} data-kind={e.kind} data-category={e.category}>
              <button
                type="button"
                onClick={() => hasPayload && toggleRow(e.seq)}
                className={`flex w-full items-start gap-2 px-2 py-1 text-left transition-colors hover:bg-muted/30 ${hasPayload ? "" : "cursor-default"}`}
              >
                <span className="mt-[3px] w-4 shrink-0 text-muted-foreground">
                  {hasPayload ? (
                    isOpen ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />
                  ) : null}
                </span>
                <span className="w-16 shrink-0 text-right font-mono text-[10px] tabular-nums text-muted-foreground">
                  {fmtOffset(e.offset_ms)}
                </span>
                <span className={`mt-[5px] h-1.5 w-1.5 shrink-0 rounded-full ${lane(e.category).dot}`} />
                <span className={`w-48 shrink-0 font-mono text-[11px] ${lane(e.category).text}`}>
                  {e.kind}
                </span>
                {/* No truncation: the summary IS the information. Long lines
                    wrap instead of being cut at the container edge. */}
                <span className="min-w-0 flex-1 break-words text-[11px] text-foreground/80 [overflow-wrap:anywhere]">
                  {e.summary}
                </span>
              </button>
              {isOpen && (
                <pre className="overflow-x-auto border-t border-border/40 bg-background/60 px-3 py-2 font-mono text-[10px] leading-relaxed text-muted-foreground">
                  {JSON.stringify(e.payload, null, 2)}
                </pre>
              )}
            </li>
          );
        })}
      </ol>

      <div className="text-[10px] text-muted-foreground">
        {visible.length === events.length
          ? `${events.length} ${t("run_inspector.stream.events")}`
          : `${visible.length} / ${events.length} ${t("run_inspector.stream.events")}`}
      </div>
    </div>
  );
}
