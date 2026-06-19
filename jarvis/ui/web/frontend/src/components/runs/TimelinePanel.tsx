import type { RunTurn } from "./types";

export function TimelinePanel({ turn }: { turn: RunTurn }) {
  if (turn.timeline.length === 0) return <span className="text-muted-foreground/60">n/a</span>;
  return (
    <ol className="space-y-0.5 font-mono text-[10px]">
      {turn.timeline.map((ev, i) => (
        <li key={i} className="flex gap-2">
          <span className="w-12 shrink-0 text-right text-muted-foreground">+{(ev.offset_ms / 1000).toFixed(2)}s</span>
          <span className="w-44 shrink-0 truncate">{ev.kind}</span>
          <span className="flex-1 truncate text-muted-foreground">{ev.summary}</span>
        </li>
      ))}
    </ol>
  );
}
