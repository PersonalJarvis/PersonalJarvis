import type { ErrorEntry } from "./types";

export function ErrorPanel({ errors }: { errors: ErrorEntry[] }) {
  if (errors.length === 0) return <span className="text-muted-foreground/60">—</span>;
  return (
    <ul className="space-y-1">
      {errors.map((e, i) => (
        <li key={i} className="rounded bg-destructive/10 px-2 py-1 text-[11px]">
          <span className="font-semibold text-destructive">{e.source}</span>
          {e.layer && <span className="text-muted-foreground"> · {e.layer}</span>}
          <span className="text-muted-foreground"> — {e.message}</span>
        </li>
      ))}
    </ul>
  );
}
