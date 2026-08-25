/** Small number/time formatters the agent board and the insight page share. */

/** `850ms`, `12s`, `5m`, `2h` — the board's compact duration. */
export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${Math.floor(ms)}ms`;
  if (ms < 60_000) return `${Math.floor(ms / 1000)}s`;
  if (ms < 3_600_000) {
    const m = Math.floor(ms / 60_000);
    const s = Math.floor((ms % 60_000) / 1000);
    return s > 0 && m < 10 ? `${m}m ${s}s` : `${m}m`;
  }
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

/** `+0.0s`, `+12s`, `+3m 05s` — an offset from the run's start. */
export function formatOffset(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "+0s";
  if (ms < 60_000) return `+${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.floor((ms % 60_000) / 1000);
  return `+${m}m ${String(s).padStart(2, "0")}s`;
}

/** Date + time in the viewer's locale — "24 Aug, 19:13". */
export function formatClock(ms: number | null | undefined, locale?: string): string {
  if (!ms || !Number.isFinite(ms)) return "—";
  try {
    return new Intl.DateTimeFormat(locale, {
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(ms));
  } catch {
    return new Date(ms).toISOString().slice(0, 16).replace("T", " ");
  }
}

/** `1.2 KB`, `26 KB`, `3.1 MB`. */
export function formatBytes(size: number): string {
  if (!Number.isFinite(size) || size < 0) return "—";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(size < 10 * 1024 ? 1 : 0)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

/** `$0.0123` — costs stay in dollars because that is what providers bill in. */
export function formatUsd(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "—";
  return `$${value < 0.01 ? value.toFixed(4) : value.toFixed(2)}`;
}
