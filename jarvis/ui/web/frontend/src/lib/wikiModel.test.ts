import { describe, expect, it } from "vitest";

import {
  cleanTitle,
  compactAge,
  degreeOf,
  groupOfKind,
  libraryGroups,
  libraryItems,
  matchesFilter,
  readableSnippet,
  recentItems,
  uniqueEdges,
} from "@/lib/wikiModel";
import { stripLeadingTitle } from "@/components/wiki/PageRenderer";

const FOLDERS = [
  {
    name: "entities",
    kind: "entity",
    count: 2,
    files: [
      { slug: "zoe", title: "Zoë", mtime: 10, size: 1 },
      { slug: "ada", title: "Ada", mtime: 30, size: 1 },
    ],
  },
  { name: "concepts", kind: "concept", count: 0, files: [] },
  {
    name: "society/ada",
    kind: "society",
    count: 1,
    files: [{ slug: "MEMORY", title: '"Ada — memory"', mtime: 20, size: 1 }],
  },
];

describe("wikiModel", () => {
  it("strips YAML quotes from titles and falls back to the slug", () => {
    expect(cleanTitle('"Ada — memory"')).toBe("Ada — memory");
    expect(cleanTitle("", "fallback")).toBe("fallback");
  });

  it("maps backend kinds onto library groups", () => {
    expect(groupOfKind("society")).toBe("agent");
    expect(groupOfKind("meta")).toBe("system");
    expect(groupOfKind("project")).toBe("project");
    expect(groupOfKind("whatever")).toBe("other");
  });

  it("groups pages by kind, drops empty groups and sorts by title", () => {
    const groups = libraryGroups(libraryItems(FOLDERS));
    expect(groups.map((g) => g.id)).toEqual(["entity", "agent"]);
    expect(groups[0].items.map((i) => i.title)).toEqual(["Ada", "Zoë"]);
    expect(groups[1].items[0].title).toBe("Ada — memory");
  });

  it("lists the newest pages first and filters accent-insensitively", () => {
    const items = libraryItems(FOLDERS);
    expect(recentItems(items, 2).map((i) => i.slug)).toEqual(["ada", "MEMORY"]);
    expect(items.filter((i) => matchesFilter(i, "zoe")).map((i) => i.slug)).toEqual(["zoe"]);
  });

  it("collapses repeated wikilinks into one weighted edge", () => {
    const edges = uniqueEdges([
      { source: "log", target: "memory", context: "" },
      { source: "log", target: "memory", context: "merge" },
      { source: "log", target: "memory", context: "" },
      { source: "a", target: "a", context: "" },
      { source: "a", target: "b", context: "x" },
    ]);
    expect(edges).toHaveLength(2);
    expect(edges[0]).toMatchObject({ source: "log", target: "memory", weight: 3, context: "merge" });
    expect(degreeOf(edges).get("a")).toBe(1);
  });

  it("formats a compact age", () => {
    const now = 1_000_000 * 1000;
    expect(compactAge(1_000_000 - 3 * 3600, "en", now)).toBe("3h");
    expect(compactAge(1_000_000 - 2 * 86400, "en", now)).toBe("2d");
    expect(compactAge(0, "en", now)).toBe("");
  });

  it("turns markdown snippets into words", () => {
    expect(readableSnippet("# GitHub ## Summary see [[entities/ruben]] and [[x|the label]] **now**")).toBe(
      "GitHub Summary see ruben and the label now",
    );
  });

  it("drops a leading H1 only when it repeats the title", () => {
    expect(stripLeadingTitle("\n# Ruben\n\n## Summary\n", "Ruben")).toBe("## Summary\n");
    expect(stripLeadingTitle("# Other\nbody", "Ruben")).toBe("# Other\nbody");
  });
});
