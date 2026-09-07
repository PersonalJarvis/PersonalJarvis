import { useT } from "@/i18n";

import type { ErrorEntry, RunTurn } from "./types";

/**
 * Recorded faults, one row each.
 *
 * A fault is a status, so it keeps its hue — but only as ink on the soft
 * destructive wash the rest of the app uses for the same job. An error says
 * what failed; it does not repaint the panel red.
 */
export function ErrorPanel({ errors }: { errors: ErrorEntry[] }) {
  const t = useT();
  if (errors.length === 0) {
    return (
      <p className="text-base text-muted-foreground">
        {t("run_inspector.errors.empty")}
      </p>
    );
  }
  return (
    <ul className="space-y-1.5" data-testid="error-panel">
      {errors.map((e, i) => (
        <ErrorRow key={`${e.source}-${i}`} entry={e} />
      ))}
    </ul>
  );
}

/**
 * Every fault of a whole run, each one carrying the turn it happened in — the
 * "this run went wrong, where?" read that used to require opening five turn
 * cards and clicking through five forensic tabs.
 */
export function RunErrorList({ turns }: { turns: RunTurn[] }) {
  const t = useT();
  const rows = turns.flatMap((turn) =>
    turn.errors.map((entry) => ({ entry, idx: turn.idx })),
  );
  if (rows.length === 0) {
    return (
      <p className="text-base text-muted-foreground">
        {t("run_inspector.errors.empty")}
      </p>
    );
  }
  return (
    <ul className="space-y-1.5" data-testid="run-errors">
      {rows.map(({ entry, idx }, i) => (
        <ErrorRow key={`${idx}-${entry.source}-${i}`} entry={entry} turnIdx={idx} />
      ))}
    </ul>
  );
}

function ErrorRow({ entry, turnIdx }: { entry: ErrorEntry; turnIdx?: number }) {
  return (
    <li className="rounded-md border border-destructive/20 bg-destructive/[0.12] px-3 py-2 text-sm">
      <div className="flex flex-wrap items-baseline gap-x-2">
        {turnIdx !== undefined && (
          <span className="font-medium text-foreground-strong">Turn {turnIdx + 1}</span>
        )}
        <span className="font-mono font-medium text-destructive">{entry.source}</span>
        {entry.layer && (
          <span className="font-mono text-muted-foreground">{entry.layer}</span>
        )}
      </div>
      <p className="mt-1 break-words text-foreground-secondary [overflow-wrap:anywhere]">
        {entry.message}
      </p>
    </li>
  );
}
