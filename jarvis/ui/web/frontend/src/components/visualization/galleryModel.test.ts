/**
 * The gallery's ordering rules — categories, search, sort and date headings —
 * pinned without rendering a single card.
 */
import { describe, expect, it } from "vitest";

import type { OutputSummary } from "@/hooks/useOutputs";
import type { VisualArtifact, VisualKind } from "@/hooks/useVisualArtifacts";
import {
  buildRailRows,
  countByFilter,
  dateGroupOf,
  filterRailRows,
  groupRailRows,
  searchRailRows,
  sortRailRows,
} from "@/components/visualization/galleryModel";

const NOW = new Date(2026, 9, 1, 15, 0, 0).getTime();
const HOUR = 3600;
const nowS = NOW / 1000;

function visual(slug: string, title: string, kind: VisualKind, mtime: number): VisualArtifact {
  return {
    slug,
    path: `files/${title}`,
    name: title,
    title,
    kind,
    size: 1,
    mtime,
    url: `/x/${slug}`,
    status: "success",
    utterance: `Make ${title}`,
  };
}

function run(slug: string, over: Partial<OutputSummary> = {}): OutputSummary {
  return { slug, utterance: `Task ${slug}`, status: "success", completed_at: nowS - HOUR, ...over };
}

const VISUALS = [
  visual("a", "Sales map", "page", nowS - 2 * HOUR),
  visual("b", "Logo", "vector", nowS - 30 * HOUR),
  visual("c", "Report", "document", nowS - 40 * 24 * HOUR),
];
const RUNS = [run("a"), run("b"), run("c"), run("d", { summary: "Refactored the parser" })];

describe("gallery model", () => {
  it("counts and filters by category", () => {
    const rows = buildRailRows(RUNS, VISUALS, []);
    expect(countByFilter(rows)).toEqual({ all: 4, pages: 1, images: 1, documents: 1, outputs: 1 });
    expect(filterRailRows(rows, "images").map((r) => r.key)).toEqual(["b::files/Logo"]);
  });

  it("searches titles, requests and summaries with every word", () => {
    const rows = buildRailRows(RUNS, VISUALS, []);
    expect(searchRailRows(rows, "sales MAP")).toHaveLength(1);
    expect(searchRailRows(rows, "parser")).toHaveLength(1);
    expect(searchRailRows(rows, "sales parser")).toHaveLength(0);
  });

  it("sorts by name and keeps work in progress first", () => {
    const building = run("e", { status: "running", utterance: "Artifact: Zebra board\nDraw it" });
    const rows = buildRailRows([...RUNS, building], VISUALS, [building]);
    const titles = sortRailRows(rows, "name").map((r) =>
      r.kind === "visual" ? r.visual.title : r.run.slug,
    );
    expect(titles[0]).toBe("e");
    expect(titles.slice(1, 4)).toEqual(["Logo", "Report", "Sales map"]);
  });

  it("groups by day, newest first, with work in progress on top", () => {
    const building = run("e", { status: "running", utterance: "Artifact: Board\nDraw it" });
    const rows = sortRailRows(buildRailRows([...RUNS, building], VISUALS, [building]), "newest");
    const groups = groupRailRows(rows, "newest", NOW);
    expect(groups.map((g) => g.id)).toEqual(["active", "today", "yesterday", "earlier"]);
    expect(groupRailRows(sortRailRows(rows, "name"), "name", NOW).map((g) => g.id)).toEqual([
      "active",
      "all",
    ]);
  });

  it("places moments under the right heading", () => {
    expect(dateGroupOf(nowS - 60, NOW)).toBe("today");
    expect(dateGroupOf(nowS - 24 * HOUR, NOW)).toBe("yesterday");
    expect(dateGroupOf(nowS - 4 * 24 * HOUR, NOW)).toBe("week");
    expect(dateGroupOf(nowS - 20 * 24 * HOUR, NOW)).toBe("month");
    expect(dateGroupOf(nowS - 90 * 24 * HOUR, NOW)).toBe("earlier");
  });
});
