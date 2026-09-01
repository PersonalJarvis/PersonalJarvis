import type { ErrorEntry } from "./types";

/**
 * Recorded faults for one run. `fault` ink on the normal lift surface — an
 * error says what failed, it does not repaint the panel red.
 */
export function ErrorPanel({ errors }: { errors: ErrorEntry[] }) {
  if (errors.length === 0)
    return <span className="text-body text-muted-foreground">—</span>;
  return (
    <ul className="space-y-1">
      {errors.map((e, i) => (
        <li key={i} className="rounded-md bg-secondary px-2 py-1.5 text-meta">
          <span className="font-semibold text-destructive">{e.source}</span>
          {e.layer && <span className="text-muted-foreground"> · {e.layer}</span>}
          <span className="text-muted-foreground"> — {e.message}</span>
        </li>
      ))}
    </ul>
  );
}
