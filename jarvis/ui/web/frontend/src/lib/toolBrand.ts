/**
 * Tool name → brand mark, for the reasoning steps of a turn.
 *
 * A tool step only knows the tool's registry name ("gmail_search",
 * "plugin-gmail", "google_calendar_list", "run_shell"). The turn view wants
 * what the Claude app shows for a tool call: the service's mark in a small
 * tile and a readable label. This module is the pure mapping — no React, no
 * store — so the rule ("which brand does this tool belong to?") is unit-
 * testable on its own and identical wherever a tool name is rendered.
 *
 * The brand set is derived from the SVGs bundled under `src/assets/brands`
 * (the same files the Plugins store uses), so adding a mark
 * there lights it up here too — nothing to register. Matching is on
 * underscore-separated TOKENS, never raw substrings: "linear_issues" is
 * Linear, "nonlinear_solver" is not. Multi-token brands ("google_calendar",
 * "cal_com") are tried before single-token ones so the most specific brand
 * wins.
 */

const BUNDLED_BRAND_LOGOS = import.meta.glob("../assets/brands/*.svg", {
  eager: true,
  query: "?url",
  import: "default",
}) as Record<string, string>;

/** Display names where the file id's auto-humanised form would be wrong. */
const BRAND_DISPLAY_NAMES: Record<string, string> = {
  cal_com: "Cal.com",
  clickup: "ClickUp",
  github: "GitHub",
  gmail: "Gmail",
  google_calendar: "Google Calendar",
  google_drive: "Google Drive",
  home_assistant: "Home Assistant",
  youtube_music: "YouTube Music",
};

/** Tokens that name a wrapper, not an action — dropped from the label. */
const NOISE_TOKENS = new Set(["plugin", "tool", "mcp"]);

interface Brand {
  id: string;
  tokens: string[];
  label: string;
  logoUrl: string;
}

function humanise(tokens: string[]): string {
  return tokens
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** Every bundled brand, most specific (most tokens, then longest) first. */
const BRANDS: Brand[] = Object.entries(BUNDLED_BRAND_LOGOS)
  .map(([path, logoUrl]) => {
    const id = path.replace(/^.*\//, "").replace(/\.svg$/, "");
    const tokens = id.split("_");
    return {
      id,
      tokens,
      label: BRAND_DISPLAY_NAMES[id] ?? humanise(tokens),
      logoUrl,
    };
  })
  .sort(
    (a, b) => b.tokens.length - a.tokens.length || b.id.length - a.id.length,
  );

export interface ToolBrand {
  /** Readable label: "Gmail · search", "Google Calendar · list", "run shell". */
  label: string;
  /** Bundled SVG for the brand tile, when the tool belongs to a known brand. */
  logoUrl?: string;
  /** Two-letter fallback for the tile when there is no logo. */
  monogram: string;
  /** The brand id (asset file stem) the tool resolved to, if any. */
  brandId?: string;
}

/** "plugin-gmail" → ["plugin", "gmail"]; "Google.Calendar list" → ["google", "calendar", "list"]. */
export function tokenizeToolName(toolName: string): string[] {
  return toolName
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(Boolean);
}

/** Index at which `needle` occurs as a contiguous run inside `hay`, or -1. */
function indexOfRun(hay: string[], needle: string[]): number {
  outer: for (let i = 0; i + needle.length <= hay.length; i++) {
    for (let j = 0; j < needle.length; j++) {
      if (hay[i + j] !== needle[j]) continue outer;
    }
    return i;
  }
  return -1;
}

/** Two letters: initials of a multi-word label, else its first two letters. */
export function monogramFor(label: string): string {
  const words = label.split(/[\s·]+/).filter(Boolean);
  if (words.length === 0) return "??";
  const raw =
    words.length >= 2
      ? words[0].charAt(0) + words[1].charAt(0)
      : words[0].slice(0, 2);
  return raw.toUpperCase().padEnd(2, "·");
}

/**
 * Resolve a tool name to its brand mark and a readable label.
 *
 * Known brand: the remaining tokens (minus wrapper words like "plugin")
 * become the action — "gmail_search" → "Gmail · search", "plugin-gmail" →
 * "Gmail". Unknown tool: the tokens joined with spaces — "run_shell" →
 * "run shell" — and a monogram instead of a logo.
 */
export function resolveToolBrand(toolName: string): ToolBrand {
  const tokens = tokenizeToolName(toolName ?? "");
  if (tokens.length === 0) {
    return { label: (toolName ?? "").trim() || "?", monogram: "??" };
  }

  for (const brand of BRANDS) {
    const at = indexOfRun(tokens, brand.tokens);
    if (at === -1) continue;
    const rest = [...tokens.slice(0, at), ...tokens.slice(at + brand.tokens.length)].filter(
      (w) => !NOISE_TOKENS.has(w),
    );
    const label = rest.length ? `${brand.label} · ${rest.join(" ")}` : brand.label;
    return {
      label,
      logoUrl: brand.logoUrl,
      monogram: monogramFor(brand.label),
      brandId: brand.id,
    };
  }

  const plain = tokens.filter((w) => !NOISE_TOKENS.has(w));
  const label = (plain.length ? plain : tokens).join(" ");
  return { label, monogram: monogramFor(label) };
}
