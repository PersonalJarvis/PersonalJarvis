/**
 * Shared vocabulary of the Spend & Tokens section: colours, formatters, tone.
 *
 * Colours are picked mid-luminance on purpose. They sit on the dark app
 * background and on the light one without a second table, which is the rule
 * every chart in this app follows (see `board/WordsTrendChart`): a hue that
 * only works in one appearance is a bug in the other.
 */
import type { CostRole, CostSurface, PriceSource } from "@/hooks/useCosts";

/** One hue per role — the chart stack, the legend and the tables share it. */
export const ROLE_COLORS: Record<CostRole, string> = {
  realtime: "hsl(199 90% 62%)", // sky — the voice you talk to
  tool: "hsl(50 100% 52%)", // signal yellow — the app's primary
  pipeline: "hsl(268 72% 68%)", // violet — the classic brain path
  agent: "hsl(152 58% 52%)", // green — coding agents
  worker: "hsl(18 88% 62%)", // orange — autonomous missions
  stt: "hsl(340 75% 65%)", // rose — the ear
  tts: "hsl(30 90% 60%)", // amber — the voice
};

export const ROLE_ORDER: CostRole[] = [
  "realtime",
  "tool",
  "pipeline",
  "agent",
  "worker",
  "stt",
  "tts",
];

/** Fallback for a bucket key that is not a role (provider, model, day). */
export const NEUTRAL_COLOR = "hsl(0 0% 62%)";

export function roleColor(key: string): string {
  return ROLE_COLORS[key as CostRole] ?? NEUTRAL_COLOR;
}

/**
 * A stable colour for an arbitrary key (provider or model).
 *
 * Hashed rather than assigned by index so a provider keeps its colour when a
 * filter changes the row order — a bar that changes hue on every re-render
 * reads as a different thing each time.
 */
export function keyColor(key: string): string {
  if (key in ROLE_COLORS) return ROLE_COLORS[key as CostRole];
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) hash = (hash * 31 + key.charCodeAt(i)) | 0;
  const hue = Math.abs(hash) % 360;
  return `hsl(${hue} 62% 60%)`;
}

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

/**
 * Money, at the precision the amount deserves.
 *
 * Sub-cent spend is real here — a single tool call costs $0.0004 — so small
 * amounts keep four decimals instead of rounding to "$0.00", which would tell
 * the user their most frequent call is free.
 */
export function formatMoney(usd: number, eurPerUsd: number, currency: "usd" | "eur"): string {
  const value = currency === "eur" ? usd * eurPerUsd : usd;
  const symbol = currency === "eur" ? "€" : "$";
  const abs = Math.abs(value);
  if (abs === 0) return `${symbol}0.00`;
  if (abs < 0.01) return `${symbol}${value.toFixed(4)}`;
  if (abs < 100) return `${symbol}${value.toFixed(2)}`;
  return `${symbol}${value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

/** Compact token counts: 1 240 → 1.2k, 12 823 750 → 12.8M. */
export function formatTokens(n: number): string {
  const abs = Math.abs(n);
  if (abs >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(2)}B`;
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export function formatExact(n: number): string {
  return n.toLocaleString();
}

export function formatShare(share: number): string {
  if (share <= 0) return "0%";
  if (share < 0.001) return "<0.1%";
  return `${(share * 100).toFixed(share < 0.1 ? 1 : 0)}%`;
}

/** Axis tick for a `day` (`2026-08-23`) or `hour` (`2026-08-23T14:00`) key. */
export function formatBucketTick(key: string, bucket: "day" | "hour"): string {
  const d = new Date(bucket === "hour" ? `${key}:00` : `${key}T00:00:00`);
  if (Number.isNaN(d.getTime())) return key;
  return bucket === "hour"
    ? d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export function formatBucketFull(key: string, bucket: "day" | "hour"): string {
  const d = new Date(bucket === "hour" ? `${key}:00` : `${key}T00:00:00`);
  if (Number.isNaN(d.getTime())) return key;
  return bucket === "hour"
    ? d.toLocaleString(undefined, {
        weekday: "short",
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      })
    : d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

export function formatTimestamp(ms: number): string {
  if (!ms) return "—";
  return new Date(ms).toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ---------------------------------------------------------------------------
// i18n keys — one place decides what a role / surface / price source is called
// ---------------------------------------------------------------------------

export function roleLabelKey(role: string): string {
  return `costs_view.role.${role.replace(/-/g, "_")}`;
}

export function surfaceLabelKey(surface: string): string {
  return `costs_view.surface.${surface.replace(/-/g, "_")}`;
}

export function priceSourceLabelKey(source: string): string {
  return `costs_view.price_source.${source}`;
}

/** Status tone for a price source, matching the shared `StatusDot` tones. */
export function priceSourceTone(source: PriceSource): "ok" | "warn" | "off" | "error" {
  switch (source) {
    case "recorded":
      return "ok";
    case "derived":
      return "warn";
    case "free":
      return "off";
    case "subscription":
      // Not a warning and not a gap: the number is sound, it just is not a
      // bill. It reads as its own thing rather than borrowing another's tone.
      return "ok";
    default:
      return "error";
  }
}

export const ALL_SURFACES: CostSurface[] = [
  "voice",
  "agent-chat",
  "mission",
  "agentic-ide",
  "jarvis-voice",
];

/**
 * The top-level areas the section can be read as, and which surfaces each one
 * covers.
 *
 * Surfaces are how the read model records a line item; these are how a person
 * thinks about the app. The two are not the same shape — "Jarvis" is three
 * surfaces at once — so the grouping lives here rather than being inferred.
 * `surfaces: []` means no filter at all, which is what "Overall" is.
 */
export interface SurfaceGroup {
  id: string;
  surfaces: CostSurface[];
  /** Roles that can occur in this area, in the order they should be offered. */
  roles: CostRole[];
}

export const SURFACE_GROUPS: SurfaceGroup[] = [
  { id: "all", surfaces: [], roles: ROLE_ORDER },
  {
    id: "jarvis",
    surfaces: ["voice", "agent-chat", "mission"],
    roles: ["realtime", "tool", "pipeline", "agent", "worker"],
  },
  { id: "agentic-ide", surfaces: ["agentic-ide"], roles: ["agent", "worker"] },
  { id: "jarvis-voice", surfaces: ["jarvis-voice"], roles: ["stt", "tts"] },
];

/** The group whose surfaces are exactly the ones selected, if any. */
export function surfaceGroupFor(surfaces: readonly string[]): SurfaceGroup | null {
  const want = [...surfaces].sort().join(",");
  if (!want) return SURFACE_GROUPS[0];
  return SURFACE_GROUPS.find((g) => g.surfaces.length > 0 && [...g.surfaces].sort().join(",") === want) ?? null;
}

export function surfaceGroupLabelKey(id: string): string {
  return `costs_view.area.${id.replace(/-/g, "_")}`;
}
