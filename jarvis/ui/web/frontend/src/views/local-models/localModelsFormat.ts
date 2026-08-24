/**
 * Small, pure formatters shared by the Local-models panels.
 *
 * Sizes are shown in GiB with one decimal (the unit the backend's
 * `accelerator_gb` and `size_gb` already use), context windows as "32k".
 */

const GIB = 1024 ** 3;

/** "4.7 GB"; "—" for nothing. */
export function formatGb(bytes: number | null | undefined): string {
  if (!bytes || bytes <= 0) return "—";
  const gb = bytes / GIB;
  return `${gb < 10 ? gb.toFixed(1) : Math.round(gb).toString()} GB`;
}

/** Bytes → GiB as a number, for estimates. */
export function toGb(bytes: number | null | undefined): number {
  return bytes && bytes > 0 ? bytes / GIB : 0;
}

/** 131072 → "128k", 4096 → "4k", 1500 → "1.5k"; "—" when unknown. */
export function formatContext(tokens: number | null | undefined): string {
  if (!tokens || tokens <= 0) return "—";
  const k = tokens / 1024;
  if (k < 1) return String(tokens);
  return `${Number.isInteger(k) ? k : k.toFixed(1)}k`;
}

/** Percentage of `part` in `whole`, clamped 0..100; 0 when whole is empty. */
export function share(part: number, whole: number): number {
  if (!whole || whole <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((part / whole) * 100)));
}

/**
 * Rough extra memory a context window costs, in GiB: about 0.03 GiB per
 * 1k tokens per GiB of model size (the same rule of thumb the backend's
 * suggestion uses). An estimate for a chip label, never a promise.
 */
export function estimateContextGb(
  contextTokens: number,
  modelGb: number,
): number {
  return (contextTokens / 1000) * 0.03 * Math.max(modelGb, 0.5);
}

/** "in 4 min" / "in 2 h" from an ISO expiry; "" when missing or in the past. */
export function formatExpiry(
  iso: string | null | undefined,
  now: number = Date.now(),
): string {
  if (!iso) return "";
  const at = new Date(iso).getTime();
  if (Number.isNaN(at) || at <= now) return "";
  const minutes = Math.round((at - now) / 60_000);
  if (minutes < 60) return `${Math.max(minutes, 1)} min`;
  const hours = minutes / 60;
  if (hours < 48)
    return `${hours < 10 ? hours.toFixed(1).replace(/\.0$/, "") : Math.round(hours)} h`;
  return `${Math.round(hours / 24)} d`;
}
