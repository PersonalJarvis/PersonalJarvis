import { describe, expect, it } from "vitest";

import {
  historyFor,
  latestFor,
  parseDoNotRecord,
  parseObservations,
  shortenCategory,
} from "@/views/profile/provenance";

const FILE = `---
schema_version: 3
identity:
  name: Ruben
---

# About the user

## Observations over time

_Jarvis appends here when it learns something new. Format: \`[YYYY-MM-DD] <field>: <value>  — "<evidence quote>"\`._

<!-- curator:observations:start -->
- [2026-08-29] identity.primary_language: de — "always answer me in German"
- [2026-08-29] identity.timezone: Europe/Berlin — "I am in Berlin"
- [2026-09-01] identity.primary_language: de — "manual edit via profile UI"
- [2026-09-02] values.top_values: clarity — "I like things clear"
- [2026-09-02] note: something free-form the merger wrote
<!-- curator:observations:end -->

## Do Not Record

_Jarvis deliberately does NOT store any of the following categories:_

- Political or religious beliefs (echo-chamber risk)
- Health or mental-health diagnoses (GDPR Art. 9, breach of trust)
- Relationship conflicts as triggers for later quotes
- MBTI type or similar pseudo-scientific labels
`;

describe("parseObservations", () => {
  it("reads date, cluster, field, value and evidence", () => {
    const obs = parseObservations(FILE);
    expect(obs[0]).toEqual({
      date: "2026-08-29",
      cluster: "identity",
      field: "primary_language",
      value: "de",
      evidence: "always answer me in German",
    });
  });

  it("skips lines whose label is not cluster.field", () => {
    const obs = parseObservations(FILE);
    expect(obs).toHaveLength(4);
    expect(obs.some((o) => o.field === "note")).toBe(false);
  });

  it("ignores the Format: example in the section's own prose", () => {
    // The prose sits ABOVE the start marker, so scoping to the markers is what
    // keeps "<field>: <value>" from parsing as a fact.
    const obs = parseObservations(FILE);
    expect(obs.every((o) => o.date.startsWith("2026-"))).toBe(true);
  });

  it("parses a file with no curator markers at all", () => {
    const obs = parseObservations('- [2026-01-02] values.motivations: building — "I like building"');
    expect(obs).toHaveLength(1);
    expect(obs[0].value).toBe("building");
  });

  it("accepts a line with no evidence quote", () => {
    const obs = parseObservations("- [2026-01-02] work_style.focus_mode: deep-work");
    expect(obs[0]).toMatchObject({ value: "deep-work", evidence: "" });
  });

  it("keeps an em dash that belongs to the value", () => {
    const obs = parseObservations('- [2026-01-02] values.pet_peeves: Noise — Chaos — "too loud"');
    expect(obs[0].value).toBe("Noise — Chaos");
    expect(obs[0].evidence).toBe("too loud");
  });

  it("returns nothing for empty input", () => {
    expect(parseObservations("")).toEqual([]);
    expect(parseObservations(null)).toEqual([]);
    expect(parseObservations(undefined)).toEqual([]);
  });
});

describe("historyFor / latestFor", () => {
  const obs = parseObservations(FILE);

  it("collects every line for one field, oldest first", () => {
    const h = historyFor(obs, "identity", "primary_language");
    expect(h.map((o) => o.date)).toEqual(["2026-08-29", "2026-09-01"]);
  });

  it("returns the most recent line as the explanation", () => {
    expect(latestFor(obs, "identity", "primary_language")?.date).toBe("2026-09-01");
  });

  it("returns null for a field the trail never mentions", () => {
    expect(latestFor(obs, "identity", "pronouns")).toBeNull();
  });
});

describe("parseDoNotRecord", () => {
  it("reads the bullets out of the file rather than restating them", () => {
    expect(parseDoNotRecord(FILE)).toEqual([
      "Political or religious beliefs (echo-chamber risk)",
      "Health or mental-health diagnoses (GDPR Art. 9, breach of trust)",
      "Relationship conflicts as triggers for later quotes",
      "MBTI type or similar pseudo-scientific labels",
    ]);
  });

  it("stops at the next heading", () => {
    const withTail = `${FILE}\n## Something else\n\n- not a category\n`;
    expect(parseDoNotRecord(withTail)).toHaveLength(4);
  });

  it("returns nothing when the section is missing", () => {
    expect(parseDoNotRecord("# About the user\n")).toEqual([]);
    expect(parseDoNotRecord(null)).toEqual([]);
  });
});

describe("shortenCategory", () => {
  it("drops the bracketed rationale", () => {
    expect(shortenCategory("Political or religious beliefs (echo-chamber risk)")).toBe(
      "Political",
    );
  });

  it("keeps a single-clause category whole", () => {
    expect(shortenCategory("MBTI type")).toBe("MBTI type");
  });

  it("never returns an empty string", () => {
    expect(shortenCategory("(only a reason)")).toBe("(only a reason)");
  });
});
