import { describe, expect, it } from "vitest";

import {
  buildDocSections,
  type DocDiataxis,
  type DocNavSummary,
} from "./useDocs";

function doc(
  slug: string,
  section: string,
  sectionOrder: number,
  order: number,
  diataxis: DocDiataxis = "howto",
): DocNavSummary {
  return {
    title: slug,
    slug,
    summary: `Learn about ${slug}.`,
    section,
    section_order: sectionOrder,
    order,
    diataxis,
    tags: [],
    related: [],
  };
}

describe("buildDocSections", () => {
  it("uses reader journey metadata instead of Diataxis buckets", () => {
    const sections = buildDocSections({
      explanation: [doc("welcome", "Start Here", 1, 1, "explanation")],
      howto: [
        doc("voice", "Everyday Use", 2, 2),
        doc("chat", "Everyday Use", 2, 1),
      ],
    });

    expect(sections.map((section) => section.name)).toEqual([
      "Start Here",
      "Everyday Use",
    ]);
    expect(sections[1].docs.map((item) => item.slug)).toEqual(["chat", "voice"]);
  });

  it("falls back safely for legacy metadata", () => {
    const legacy = doc("legacy", "", 999, 999);
    expect(buildDocSections({ howto: [legacy] })[0].name).toBe("Other");
  });
});
