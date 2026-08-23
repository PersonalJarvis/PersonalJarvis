import { describe, expect, it } from "vitest";

import { monogramFor, resolveToolBrand, tokenizeToolName } from "@/lib/toolBrand";

describe("resolveToolBrand", () => {
  it("maps a prefixed tool name to the brand and humanises the action", () => {
    const b = resolveToolBrand("gmail_search");
    expect(b.brandId).toBe("gmail");
    expect(b.label).toBe("Gmail · search");
    // Vite inlines small SVGs as data URLs, so only "some svg" is stable.
    expect(b.logoUrl).toMatch(/svg/);
    expect(b.monogram).toBe("GM");
  });

  it("normalises dashes and drops the wrapper word", () => {
    const b = resolveToolBrand("plugin-gmail");
    expect(b.brandId).toBe("gmail");
    expect(b.label).toBe("Gmail");
  });

  it("prefers the most specific brand (google_calendar over anything shorter)", () => {
    const b = resolveToolBrand("google_calendar_list");
    expect(b.brandId).toBe("google_calendar");
    expect(b.label).toBe("Google Calendar · list");
    expect(b.logoUrl).toMatch(/svg/);
    expect(b.monogram).toBe("GC");
  });

  it("matches whole tokens only, never substrings", () => {
    expect(resolveToolBrand("linear_issues").brandId).toBe("linear");
    expect(resolveToolBrand("nonlinear_solver").brandId).toBeUndefined();
  });

  it("uses the display name for brands whose file stem reads wrong", () => {
    expect(resolveToolBrand("github_issues").label).toBe("GitHub · issues");
    expect(resolveToolBrand("cal_com_bookings").label).toBe("Cal.com · bookings");
    expect(resolveToolBrand("home_assistant_toggle").label).toBe("Home Assistant · toggle");
  });

  it("falls back to a humanised label and a monogram for unknown tools", () => {
    const shell = resolveToolBrand("run_shell");
    expect(shell.brandId).toBeUndefined();
    expect(shell.logoUrl).toBeUndefined();
    expect(shell.label).toBe("run shell");
    expect(shell.monogram).toBe("RS");

    const nav = resolveToolBrand("navigate");
    expect(nav.label).toBe("navigate");
    expect(nav.monogram).toBe("NA");
  });

  it("survives an empty name", () => {
    expect(resolveToolBrand("").label).toBe("?");
    expect(resolveToolBrand("").monogram).toBe("??");
  });
});

describe("helpers", () => {
  it("tokenizes on any non-alphanumeric run, lower-cased", () => {
    expect(tokenizeToolName("Plugin-Gmail.Search v2")).toEqual(["plugin", "gmail", "search", "v2"]);
  });

  it("builds two-letter monograms from initials or the first two letters", () => {
    expect(monogramFor("Google Drive")).toBe("GD");
    expect(monogramFor("Notion")).toBe("NO");
    expect(monogramFor("Gmail · search")).toBe("GS");
  });
});
