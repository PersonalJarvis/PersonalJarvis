/**
 * Unit tests for the Ledger logic behind ProfileView.
 *
 * Pure functions only — acquaintance staging, open-question prioritization,
 * and the generative sigil geometry. The visual component consumes these;
 * keeping the math here makes the view itself thin and the behavior pinned.
 */
import { describe, expect, it } from "vitest";

import {
  ASK_PRIORITY,
  CLUSTER_FIELD_KEYS,
  CLUSTER_ORDER,
  TOTAL_FIELDS,
  acquaintanceStage,
  collectOpenQuestions,
  countFilled,
  displayAddress,
  isEmptyValue,
} from "@/views/profile/ledger";

// ----------------------------------------------------------------------
// Fixtures
// ----------------------------------------------------------------------

const EMPTY_META: Record<string, unknown> = {};

const PARTIAL_META: Record<string, unknown> = {
  identity: {
    name: "Alex",
    primary_language: "Deutsch",
    languages: ["Deutsch", "English"],
  },
  communication: { formality: "informal" },
};

/** meta with every single field filled. */
function fullMeta(): Record<string, unknown> {
  const meta: Record<string, Record<string, unknown>> = {};
  for (const cid of CLUSTER_ORDER) {
    meta[cid] = {};
    for (const key of CLUSTER_FIELD_KEYS[cid]) {
      meta[cid][key] = "x";
    }
  }
  return meta;
}

// ----------------------------------------------------------------------
// Field vocabulary invariants
// ----------------------------------------------------------------------

describe("ledger field vocabulary", () => {
  it("counts 18 fields across the five clusters", () => {
    const sum = CLUSTER_ORDER.reduce(
      (acc, cid) => acc + CLUSTER_FIELD_KEYS[cid].length,
      0,
    );
    expect(sum).toBe(18);
    expect(TOTAL_FIELDS).toBe(18);
  });

  it("ASK_PRIORITY covers every field exactly once", () => {
    const all = CLUSTER_ORDER.flatMap((cid) => CLUSTER_FIELD_KEYS[cid]);
    expect([...ASK_PRIORITY].sort()).toEqual([...all].sort());
    expect(new Set(ASK_PRIORITY).size).toBe(ASK_PRIORITY.length);
  });
});

// ----------------------------------------------------------------------
// isEmptyValue / countFilled
// ----------------------------------------------------------------------

describe("isEmptyValue", () => {
  it("treats null, undefined, empty string and empty array as empty", () => {
    expect(isEmptyValue(null)).toBe(true);
    expect(isEmptyValue(undefined)).toBe(true);
    expect(isEmptyValue("")).toBe(true);
    expect(isEmptyValue([])).toBe(true);
  });

  it("treats false, 0 and non-empty values as filled", () => {
    expect(isEmptyValue(false)).toBe(false);
    expect(isEmptyValue(0)).toBe(false);
    expect(isEmptyValue("x")).toBe(false);
    expect(isEmptyValue(["a"])).toBe(false);
  });
});

describe("countFilled", () => {
  it("is 0 for an empty meta and 18 for a full meta", () => {
    expect(countFilled(EMPTY_META)).toBe(0);
    expect(countFilled(fullMeta())).toBe(TOTAL_FIELDS);
  });

  it("counts only known vocabulary fields", () => {
    expect(countFilled(PARTIAL_META)).toBe(4);
    // Unknown stray keys never inflate the count.
    expect(countFilled({ identity: { hobby: "golf" } })).toBe(0);
  });
});

// ----------------------------------------------------------------------
// acquaintanceStage — the named relationship depth
// ----------------------------------------------------------------------

describe("acquaintanceStage", () => {
  it("maps 0 filled to the blank-page stage", () => {
    expect(acquaintanceStage(0, 18)).toMatchObject({ index: 0, key: "blank_page" });
  });

  it("maps the low band to first impressions", () => {
    expect(acquaintanceStage(1, 18).key).toBe("first_impressions");
    expect(acquaintanceStage(4, 18).key).toBe("first_impressions"); // 22%
  });

  it("maps the quarter band to getting acquainted", () => {
    expect(acquaintanceStage(5, 18).key).toBe("getting_acquainted"); // 27.8%
    expect(acquaintanceStage(8, 18).key).toBe("getting_acquainted"); // 44.4%
  });

  it("maps the half band to well acquainted", () => {
    expect(acquaintanceStage(9, 18).key).toBe("well_acquainted"); // 50%
    expect(acquaintanceStage(13, 18).key).toBe("well_acquainted"); // 72.2%
  });

  it("maps the upper band to trusted company", () => {
    expect(acquaintanceStage(14, 18).key).toBe("trusted_company"); // 77.8%
    expect(acquaintanceStage(17, 18).key).toBe("trusted_company"); // 94.4%
  });

  it("reserves the final stage for a complete ledger", () => {
    expect(acquaintanceStage(18, 18)).toMatchObject({ index: 5, key: "inner_circle" });
  });

  it("never crashes on a zero total", () => {
    expect(acquaintanceStage(0, 0).key).toBe("blank_page");
  });
});

// ----------------------------------------------------------------------
// collectOpenQuestions — what the butler asks next
// ----------------------------------------------------------------------

describe("collectOpenQuestions", () => {
  it("returns the top priorities for a blank ledger", () => {
    const top = collectOpenQuestions(EMPTY_META, 3);
    expect(top.map((q) => q.field)).toEqual(ASK_PRIORITY.slice(0, 3));
    // Every entry knows its cluster so the UI can label it.
    for (const q of top) {
      expect(CLUSTER_FIELD_KEYS[q.cluster]).toContain(q.field);
    }
  });

  it("skips fields that are already inked", () => {
    const fields = collectOpenQuestions(PARTIAL_META, 5).map((q) => q.field);
    expect(fields).not.toContain("name");
    expect(fields).not.toContain("primary_language");
    expect(fields).not.toContain("languages");
    expect(fields).not.toContain("formality");
  });

  it("respects the limit and returns nothing for a full ledger", () => {
    expect(collectOpenQuestions(EMPTY_META, 2)).toHaveLength(2);
    expect(collectOpenQuestions(fullMeta(), 5)).toHaveLength(0);
  });

  it("returns all 18 open questions for a blank ledger when uncapped", () => {
    expect(collectOpenQuestions(EMPTY_META, 99)).toHaveLength(18);
  });
});

// ----------------------------------------------------------------------
// displayAddress — how the headline addresses the user
// ----------------------------------------------------------------------

describe("displayAddress", () => {
  it("prefers the preferred_address over the name", () => {
    const meta = { identity: { preferred_address: "Chef" } };
    expect(displayAddress(meta, "Personal Jarvis Maintainer")).toBe("Chef"); // i18n-allow: intentional German address fixture
  });

  it("falls back to the first name", () => {
    expect(displayAddress(EMPTY_META, "Personal Jarvis Maintainer")).toBe("Alex"); // i18n-allow: German umlaut name fixture
    expect(displayAddress(EMPTY_META, "  Alex  ")).toBe("Alex");
  });

  it("returns null when nothing is known", () => {
    expect(displayAddress(EMPTY_META, null)).toBeNull();
    expect(displayAddress(EMPTY_META, "   ")).toBeNull();
  });
});

