import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { companionEyeColors, companionSchema, defaultCompanion, resolveCompanion } from "./appearance";

const cases = JSON.parse(readFileSync(resolve(process.cwd(), "../../../../tests/contract/fixtures/agent-companion.json"), "utf8")) as { valid: Record<string, unknown>[]; invalid: Record<string, unknown>[] };
describe("the shared Python/JSON/TypeScript companion contract", () => {
  it.each(cases.valid)("accepts stored values %#", value => { expect(companionSchema.safeParse(value).success).toBe(true); });
  it.each(cases.invalid)("rejects malformed values %#", value => { expect(companionSchema.safeParse(value).success).toBe(false); });
  it("keeps existing identities and recovers corrupt legacy metadata", () => {
    expect(resolveCompanion("research", undefined)).toEqual(defaultCompanion("research"));
    expect(resolveCompanion("research", { shape: "bad" })).toEqual(defaultCompanion("research"));
    expect(resolveCompanion("new-name", cases.valid[1]).shape).toBe("cloud");
  });
  it("keeps eyes readable on custom dark colours", () => {
    expect(companionEyeColors("#000000").eye).toBe("#f5ecd7");
    expect(companionEyeColors("#ffffff").eye).toBe("#19171d");
  });
  it("standardizes earlier saved size and following-distance choices", () => {
    expect(resolveCompanion("research", cases.valid[1])).toMatchObject({ shape: "cloud", sizeM: 0.5, followDistanceM: 1 });
    expect(resolveCompanion("research", cases.valid[2])).toMatchObject({ shape: "drop", sizeM: 0.5, followDistanceM: 1 });
  });
});
