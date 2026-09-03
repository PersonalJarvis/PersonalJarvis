/**
 * Unit tests for the Ledger logic behind ProfileView.
 *
 * Pure functions only — acquaintance staging, open-question prioritization,
 * and the generative sigil geometry. The visual component consumes these;
 * keeping the math here makes the view itself thin and the behavior pinned.
 */
import { describe, expect, it } from "vitest";

import {
  BOOL_FIELD_KEYS,
  CLUSTER_FIELD_KEYS,
  CLUSTER_ORDER,
  LIST_FIELD_KEYS,
  TOTAL_FIELDS,
  countFilled,
  displayAddress,
  fieldKind,
  isBoolField,
  isEmptyValue,
  isListField,
} from "@/views/profile/ledger";

// ----------------------------------------------------------------------
// Fixtures
// ----------------------------------------------------------------------

const EMPTY_META: Record<string, unknown> = {};

const PARTIAL_META: Record<string, unknown> = {
  identity: {
    name: "Ruben",
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

});

// ----------------------------------------------------------------------
// Field kinds — drives the inline editor (text vs. toggle vs. chips)
// ----------------------------------------------------------------------

describe("field kinds", () => {
  const allFields = CLUSTER_ORDER.flatMap((cid) => CLUSTER_FIELD_KEYS[cid]);

  it("every list/bool field is part of the known vocabulary", () => {
    for (const key of LIST_FIELD_KEYS) expect(allFields).toContain(key);
    for (const key of BOOL_FIELD_KEYS) expect(allFields).toContain(key);
  });

  it("list and bool field sets do not overlap", () => {
    for (const key of LIST_FIELD_KEYS) expect(BOOL_FIELD_KEYS.has(key)).toBe(false);
  });

  it("classifies the six list fields", () => {
    for (const key of [
      "languages",
      "devices",
      "humor_types",
      "top_values",
      "pet_peeves",
      "motivations",
    ]) {
      expect(isListField(key)).toBe(true);
      expect(fieldKind(key)).toBe("list");
    }
  });

  it("classifies emoji_ok as a boolean", () => {
    expect(isBoolField("emoji_ok")).toBe(true);
    expect(fieldKind("emoji_ok")).toBe("bool");
  });

  it("classifies everything else as scalar", () => {
    for (const key of ["name", "preferred_address", "timezone", "feedback_pref"]) {
      expect(isListField(key)).toBe(false);
      expect(isBoolField(key)).toBe(false);
      expect(fieldKind(key)).toBe("scalar");
    }
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

// ----------------------------------------------------------------------
// collectOpenQuestions — what the butler asks next
// ----------------------------------------------------------------------

// ----------------------------------------------------------------------
// displayAddress — how the headline addresses the user
// ----------------------------------------------------------------------

describe("displayAddress", () => {
  it("prefers the preferred_address over the name", () => {
    const meta = { identity: { preferred_address: "Chef" } };
    expect(displayAddress(meta, "Jürgen Müller")).toBe("Chef"); // i18n-allow: intentional German address fixture
  });

  it("falls back to the first name", () => {
    // Neutral umlaut fixture: a real name here gets rewritten by the release
    // PII scrub and desynchronizes input and expectation (v1.0.6 forensic:
    // the public frontend CI failed on exactly this test).
    expect(displayAddress(EMPTY_META, "Jürgen Müller")).toBe("Jürgen"); // i18n-allow: umlaut name fixture
    expect(displayAddress(EMPTY_META, "  Jürgen  ")).toBe("Jürgen"); // i18n-allow: umlaut name fixture
  });

  it("returns null when nothing is known", () => {
    expect(displayAddress(EMPTY_META, null)).toBeNull();
    expect(displayAddress(EMPTY_META, "   ")).toBeNull();
  });
});

