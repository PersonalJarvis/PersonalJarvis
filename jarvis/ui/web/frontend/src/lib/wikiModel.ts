// Pure view model for the Wiki section: how the vault's folders become the
// library's groups, how a page kind is drawn, and the small formatting rules
// every wiki surface shares. No React, no DOM — the rail, the inspector, the
// search palette and the map canvas all read the same answers from here.

import type { WikiTreeFolder } from "@/lib/wikiApi";

/**
 * The groups the library shows, in display order. A vault folder maps onto
 * exactly one of them by its `kind` (see `groupOfKind`).
 */
export const WIKI_GROUPS = [
  "entity",
  "project",
  "concept",
  "session",
  "agent",
  "system",
  "other",
] as const;

export type WikiGroupId = (typeof WIKI_GROUPS)[number];

/** i18n key of each group's label, under `wiki_ui`. */
export const GROUP_LABEL_KEY: Record<WikiGroupId, string> = {
  entity: "wiki_ui.group_entity",
  project: "wiki_ui.group_project",
  concept: "wiki_ui.group_concept",
  session: "wiki_ui.group_session",
  agent: "wiki_ui.group_agent",
  system: "wiki_ui.group_system",
  other: "wiki_ui.group_other",
};

/** Map a backend folder / node kind onto a library group. */
export function groupOfKind(kind: string | undefined | null): WikiGroupId {
  switch (kind) {
    case "entity":
    case "concept":
    case "project":
    case "session":
      return kind;
    case "society":
      return "agent";
    case "meta":
      return "system";
    default:
      return "other";
  }
}

/**
 * Titles written by older curator runs keep the YAML quotes
 * (`"Ada — memory"`); a title is never meant to be shown quoted.
 */
export function cleanTitle(title: string | null | undefined, fallback = ""): string {
  const raw = (title ?? "").trim();
  const unquoted = raw.replace(/^["'“‘]+|["'”’]+$/g, "").trim();
  return unquoted || fallback;
}

export interface LibraryItem {
  slug: string;
  title: string;
  /** Seconds since the epoch, as the tree reports it. */
  mtime: number;
  folder: string;
  group: WikiGroupId;
}

export interface LibraryGroup {
  id: WikiGroupId;
  items: LibraryItem[];
}

/** Flatten the tree into display items, one per page file. */
export function libraryItems(folders: readonly WikiTreeFolder[]): LibraryItem[] {
  const out: LibraryItem[] = [];
  for (const folder of folders) {
    const group = groupOfKind(folder.kind);
    for (const file of folder.files) {
      out.push({
        slug: file.slug,
        title: cleanTitle(file.title, file.slug),
        mtime: file.mtime,
        folder: folder.name,
        group,
      });
    }
  }
  return out;
}

/**
 * Group the items for the library, alphabetically inside a group, and drop
 * groups that have nothing in them — an empty "Concepts" heading is noise.
 */
export function libraryGroups(items: readonly LibraryItem[]): LibraryGroup[] {
  const byGroup = new Map<WikiGroupId, LibraryItem[]>();
  for (const item of items) {
    const list = byGroup.get(item.group) ?? [];
    list.push(item);
    byGroup.set(item.group, list);
  }
  const collator = new Intl.Collator(undefined, { sensitivity: "base", numeric: true });
  return WIKI_GROUPS.filter((id) => (byGroup.get(id)?.length ?? 0) > 0).map((id) => ({
    id,
    items: [...(byGroup.get(id) ?? [])].sort((a, b) => collator.compare(a.title, b.title)),
  }));
}

/** The `limit` most recently changed pages, newest first. */
export function recentItems(items: readonly LibraryItem[], limit = 5): LibraryItem[] {
  return [...items].sort((a, b) => b.mtime - a.mtime).slice(0, limit);
}

/** Case- and accent-insensitive title/slug match for the rail filter. */
export function matchesFilter(item: LibraryItem, query: string): boolean {
  const q = normalise(query);
  if (!q) return true;
  return normalise(item.title).includes(q) || normalise(item.slug).includes(q);
}

function normalise(value: string): string {
  return value
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .trim();
}

/**
 * A compact age for a list row: `now`, `5m`, `3h`, `2d`, `4w`, `7mo`, `2y`
 * in English, the locale's narrow units elsewhere. `mtime` is in seconds.
 */
export function compactAge(mtimeSeconds: number, language: string, nowMs = Date.now()): string {
  if (!Number.isFinite(mtimeSeconds) || mtimeSeconds <= 0) return "";
  const seconds = Math.max(0, nowMs / 1000 - mtimeSeconds);
  const steps: Array<[number, Intl.NumberFormatOptions["unit"]]> = [
    [60 * 60 * 24 * 365, "year"],
    [60 * 60 * 24 * 30, "month"],
    [60 * 60 * 24 * 7, "week"],
    [60 * 60 * 24, "day"],
    [60 * 60, "hour"],
    [60, "minute"],
  ];
  for (const [size, unit] of steps) {
    if (seconds >= size) {
      const value = Math.floor(seconds / size);
      try {
        return new Intl.NumberFormat(language, {
          style: "unit",
          unit,
          unitDisplay: "narrow",
        }).format(value);
      } catch {
        return `${value}${String(unit).charAt(0)}`;
      }
    }
  }
  try {
    return new Intl.RelativeTimeFormat(language, { numeric: "auto" }).format(0, "second");
  } catch {
    return "now";
  }
}

/** A full, localised relative time ("2 days ago") for facts and headers. */
export function relativeAge(mtimeSeconds: number, language: string, nowMs = Date.now()): string {
  if (!Number.isFinite(mtimeSeconds) || mtimeSeconds <= 0) return "";
  const diff = mtimeSeconds - nowMs / 1000;
  const abs = Math.abs(diff);
  const steps: Array<[number, Intl.RelativeTimeFormatUnit]> = [
    [60 * 60 * 24 * 365, "year"],
    [60 * 60 * 24 * 30, "month"],
    [60 * 60 * 24 * 7, "week"],
    [60 * 60 * 24, "day"],
    [60 * 60, "hour"],
    [60, "minute"],
    [1, "second"],
  ];
  for (const [size, unit] of steps) {
    if (abs >= size || unit === "second") {
      try {
        return new Intl.RelativeTimeFormat(language, { numeric: "auto" }).format(
          Math.round(diff / size),
          unit,
        );
      } catch {
        return "";
      }
    }
  }
  return "";
}

export interface WeightedEdge {
  source: string;
  target: string;
  /** How many wikilinks this one pair carries. */
  weight: number;
  context: string;
}

/**
 * Collapse repeated wikilinks into one edge per (source, target) pair.
 *
 * An append-only log page links the same page hundreds of times; drawn
 * literally, that is a thousand overlapping arrows on a single line and the
 * map reads as a hairball. The first non-empty context is kept as the label.
 */
export function uniqueEdges(
  edges: ReadonlyArray<{ source: string; target: string; context?: string }>,
): WeightedEdge[] {
  const byPair = new Map<string, WeightedEdge>();
  for (const edge of edges) {
    if (edge.source === edge.target) continue;
    const key = `${edge.source}\u0000${edge.target}`;
    const existing = byPair.get(key);
    if (existing) {
      existing.weight += 1;
      if (!existing.context && edge.context) existing.context = edge.context;
    } else {
      byPair.set(key, {
        source: edge.source,
        target: edge.target,
        weight: 1,
        context: edge.context ?? "",
      });
    }
  }
  return [...byPair.values()];
}

/** Distinct neighbours per node (in either direction). */
export function degreeOf(edges: ReadonlyArray<{ source: string; target: string }>): Map<string, number> {
  const neighbours = new Map<string, Set<string>>();
  const add = (a: string, b: string) => {
    const set = neighbours.get(a) ?? new Set<string>();
    set.add(b);
    neighbours.set(a, set);
  };
  for (const edge of edges) {
    if (edge.source === edge.target) continue;
    add(edge.source, edge.target);
    add(edge.target, edge.source);
  }
  return new Map([...neighbours].map(([id, set]) => [id, set.size]));
}

/**
 * The shape each group is drawn as — on the map canvas and as the glyph in
 * every list. Kind is told apart by SHAPE, not hue: hue in this app means
 * status or "selected", and a page's kind is neither.
 */
export type KindShape = "circle" | "square" | "triangle" | "hexagon" | "ring" | "diamond" | "dot";

export const GROUP_SHAPE: Record<WikiGroupId, KindShape> = {
  entity: "circle",
  project: "square",
  concept: "triangle",
  session: "hexagon",
  agent: "ring",
  system: "diamond",
  other: "dot",
};

/** Trace (not fill) one kind shape centred on (x, y) with radius r. */
export function traceKindShape(
  ctx: CanvasRenderingContext2D,
  shape: KindShape,
  x: number,
  y: number,
  r: number,
): void {
  ctx.beginPath();
  switch (shape) {
    case "square": {
      const s = r * 0.9;
      const k = s * 0.35;
      ctx.moveTo(x - s + k, y - s);
      ctx.arcTo(x + s, y - s, x + s, y + s, k);
      ctx.arcTo(x + s, y + s, x - s, y + s, k);
      ctx.arcTo(x - s, y + s, x - s, y - s, k);
      ctx.arcTo(x - s, y - s, x + s, y - s, k);
      ctx.closePath();
      break;
    }
    case "triangle": {
      const s = r * 1.15;
      ctx.moveTo(x, y - s);
      ctx.lineTo(x + s * 0.92, y + s * 0.62);
      ctx.lineTo(x - s * 0.92, y + s * 0.62);
      ctx.closePath();
      break;
    }
    case "hexagon": {
      for (let i = 0; i < 6; i += 1) {
        const a = (Math.PI / 3) * i + Math.PI / 6;
        const px = x + Math.cos(a) * r;
        const py = y + Math.sin(a) * r;
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      }
      ctx.closePath();
      break;
    }
    case "diamond": {
      const s = r * 1.2;
      ctx.moveTo(x, y - s);
      ctx.lineTo(x + s, y);
      ctx.lineTo(x, y + s);
      ctx.lineTo(x - s, y);
      ctx.closePath();
      break;
    }
    case "dot":
      ctx.arc(x, y, r * 0.7, 0, Math.PI * 2);
      break;
    case "ring":
    case "circle":
    default:
      ctx.arc(x, y, r, 0, Math.PI * 2);
      break;
  }
}

/** Shapes drawn hollow — a ring is a circle with its middle left open. */
export function isHollowShape(shape: KindShape): boolean {
  return shape === "ring";
}
