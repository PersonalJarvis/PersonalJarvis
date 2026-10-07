/**
 * The pure model behind the Artifacts gallery — which cards exist, which a
 * category or a search lets through, in what order, under which date heading.
 *
 * No React and no fetching: the view hands in the runs and visuals it read,
 * this module turns them into cards. Kept separate so the ordering rules are
 * testable without rendering a single iframe.
 */
import type { OutputSummary } from "@/hooks/useOutputs";
import { cleanRequest, requestHeadline } from "@/lib/runRequest";
import { visualId, type VisualArtifact } from "@/hooks/useVisualArtifacts";

/** One card — a build in progress, an artifact, or a run without one. */
export type RailRow =
  | { kind: "build"; run: OutputSummary; key: string }
  | { kind: "visual"; run: OutputSummary | null; visual: VisualArtifact; key: string }
  | { kind: "run"; run: OutputSummary; key: string };

/** The library's categories — what the gallery's side rail offers. */
export type RailFilter = "all" | "pages" | "images" | "documents" | "outputs";

export const RAIL_FILTERS: readonly RailFilter[] = [
  "all",
  "pages",
  "images",
  "documents",
  "outputs",
];

/** How the gallery orders its cards. */
export type GallerySort = "newest" | "oldest" | "name";

/** The date headings the gallery groups by when it sorts by time. */
export type GalleryGroupId = "active" | "today" | "yesterday" | "week" | "month" | "earlier" | "all";

export interface GalleryGroup {
  id: GalleryGroupId;
  rows: RailRow[];
}

/**
 * A `create_artifact` mission's prompt leads with `Artifact: <title>` and the
 * user's request right after (jarvis/artifacts/brief.py). The run list
 * already strips the quality lead, so the run's `utterance` starts with that
 * line — which is how a running build is recognised and labelled here.
 */
export function parseArtifactUtterance(
  utterance: string | undefined,
): { title: string; request: string } | null {
  const text = (utterance ?? "").trim();
  const match = /^Artifact:[ \t]*([^\n]+)/.exec(text);
  if (!match) return null;
  const rest = text.slice(match[0].length).trim();
  const request = rest.split(/\n\s*\n/)[0]?.trim() ?? "";
  return { title: match[1].trim(), request };
}

/** What a run is called when it has no page title of its own. */
export function runTitle(run: OutputSummary): string {
  return (
    parseArtifactUtterance(run.utterance)?.title || requestHeadline(run.utterance ?? "") || run.slug
  );
}

/** The run's moment for ordering — when it ended, else when it began. */
export function runWhen(run: OutputSummary): number {
  return run.completed_at ?? run.started_at ?? 0;
}

/** A card's moment, in epoch seconds. */
export function rowWhen(row: RailRow): number {
  return row.kind === "visual" ? row.visual.mtime : runWhen(row.run);
}

/** A card's title — the page's own `<title>`, the brief's title, or the request. */
export function rowTitle(row: RailRow): string {
  if (row.kind === "visual") return row.visual.title;
  return runTitle(row.run);
}

/** The user's words behind a card, cleaned of the brief's scaffolding. */
export function rowRequest(row: RailRow): string {
  const utterance = row.kind === "visual" ? row.visual.utterance : row.run.utterance;
  return parseArtifactUtterance(utterance)?.request || cleanRequest(utterance) || "";
}

/** Still being made — a build, or a run (or a run's page) that is running. */
export function isActiveRow(row: RailRow): boolean {
  if (row.kind === "build") return true;
  if (row.kind === "visual") return row.visual.status === "running";
  return row.run.status === "running";
}

/** The category a card belongs to (besides "all"). */
export function rowCategory(row: RailRow): Exclude<RailFilter, "all"> {
  if (row.kind === "build") return "pages";
  if (row.kind === "run") return "outputs";
  switch (row.visual.kind) {
    case "page":
      return "pages";
    case "document":
      return "documents";
    default:
      return "images";
  }
}

/**
 * The cards, in their natural order: builds in progress, then running runs,
 * then every artifact and every artifact-less run newest first. A run with at
 * least one artifact is reached through its artifact cards (its other files
 * sit behind the stage's Files tab), so it gets no card of its own.
 */
export function buildRailRows(
  runs: OutputSummary[],
  visuals: VisualArtifact[],
  building: OutputSummary[],
): RailRow[] {
  const bySlug = new Map(runs.map((run) => [run.slug, run]));
  const visualSlugs = new Set(visuals.map((v) => v.slug));
  const buildSlugs = new Set(building.map((r) => r.slug));

  const rest: Array<{ row: RailRow; running: boolean; when: number }> = [];
  for (const visual of visuals) {
    rest.push({
      row: { kind: "visual", run: bySlug.get(visual.slug) ?? null, visual, key: visualId(visual) },
      running: visual.status === "running",
      when: visual.mtime,
    });
  }
  for (const run of runs) {
    if (visualSlugs.has(run.slug) || buildSlugs.has(run.slug)) continue;
    rest.push({
      row: { kind: "run", run, key: `run:${run.slug}` },
      running: run.status === "running",
      when: runWhen(run),
    });
  }
  rest.sort((a, b) => Number(b.running) - Number(a.running) || b.when - a.when);

  return [
    ...building.map((run): RailRow => ({ kind: "build", run, key: `build:${run.slug}` })),
    ...rest.map((entry) => entry.row),
  ];
}

/** The cards a category lets through. */
export function filterRailRows(rows: RailRow[], filter: RailFilter): RailRow[] {
  if (filter === "all") return rows;
  return rows.filter((row) => rowCategory(row) === filter);
}

/** How many cards each category holds. */
export function countByFilter(rows: RailRow[]): Record<RailFilter, number> {
  const counts: Record<RailFilter, number> = {
    all: rows.length,
    pages: 0,
    images: 0,
    documents: 0,
    outputs: 0,
  };
  for (const row of rows) counts[rowCategory(row)] += 1;
  return counts;
}

/** Cards whose title, request, summary or filename contain every search word. */
export function searchRailRows(rows: RailRow[], query: string): RailRow[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return rows;
  return rows.filter((row) => {
    const haystack = [
      rowTitle(row),
      rowRequest(row),
      row.kind === "visual" ? row.visual.name : "",
      row.run?.summary ?? "",
      row.run?.slug ?? "",
    ]
      .join(" ")
      .toLowerCase();
    return words.every((word) => haystack.includes(word));
  });
}

/** The cards in the chosen order; work in progress always leads. */
export function sortRailRows(rows: RailRow[], sort: GallerySort): RailRow[] {
  const sorted = [...rows];
  sorted.sort((a, b) => {
    const active = Number(isActiveRow(b)) - Number(isActiveRow(a));
    if (active !== 0) return active;
    if (sort === "name") return rowTitle(a).localeCompare(rowTitle(b), undefined, { sensitivity: "base" });
    return sort === "oldest" ? rowWhen(a) - rowWhen(b) : rowWhen(b) - rowWhen(a);
  });
  return sorted;
}

function startOfDay(ms: number): number {
  const day = new Date(ms);
  day.setHours(0, 0, 0, 0);
  return day.getTime();
}

/** Which date heading a moment falls under, seen from `now`. */
export function dateGroupOf(seconds: number, now: number): GalleryGroupId {
  const ms = seconds * 1000;
  const today = startOfDay(now);
  const DAY = 86_400_000;
  if (ms >= today) return "today";
  if (ms >= today - DAY) return "yesterday";
  if (ms >= today - 6 * DAY) return "week";
  if (ms >= today - 29 * DAY) return "month";
  return "earlier";
}

/**
 * Sorted cards cut into headed groups: work in progress on top, then by date
 * when the order is by time. Sorting by name is one flat group — a date
 * heading over an alphabetical list would split it in random places.
 */
export function groupRailRows(rows: RailRow[], sort: GallerySort, now: number): GalleryGroup[] {
  const active = rows.filter(isActiveRow);
  const rest = rows.filter((row) => !isActiveRow(row));
  const groups: GalleryGroup[] = [];
  if (active.length > 0) groups.push({ id: "active", rows: active });
  if (sort === "name") {
    if (rest.length > 0) groups.push({ id: "all", rows: rest });
    return groups;
  }
  const byId = new Map<GalleryGroupId, RailRow[]>();
  for (const row of rest) {
    const id = dateGroupOf(rowWhen(row), now);
    const list = byId.get(id);
    if (list) list.push(row);
    else byId.set(id, [row]);
  }
  // Rows arrive sorted, so the groups appear in the same direction.
  for (const [id, list] of byId) groups.push({ id, rows: list });
  return groups;
}

/** "12 min ago" in the UI language — what tells two same-named cards apart. */
export function relativeWhen(seconds: number, now: number, language: string): string {
  if (!seconds) return "";
  const diff = Math.round(seconds - now / 1000);
  const abs = Math.abs(diff);
  const rtf = new Intl.RelativeTimeFormat(language, { numeric: "auto" });
  if (abs < 60) return rtf.format(0, "second");
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86_400) return rtf.format(Math.round(diff / 3600), "hour");
  if (abs < 7 * 86_400) return rtf.format(Math.round(diff / 86_400), "day");
  return new Date(seconds * 1000).toLocaleDateString(language, {
    month: "short",
    day: "numeric",
    year: abs > 300 * 86_400 ? "numeric" : undefined,
  });
}
