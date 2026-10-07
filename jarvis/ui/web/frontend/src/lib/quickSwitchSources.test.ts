import { describe, expect, it } from "vitest";
import { folderName, matchScore, rankItems } from "./quickSwitchSources";

describe("matchScore", () => {
  it("needs every word of the query somewhere", () => {
    expect(matchScore("browser accept", ["Browser acceptance"])).toBeGreaterThan(0);
    expect(matchScore("browser zebra", ["Browser acceptance"])).toBe(0);
  });

  it("keeps short words to word starts", () => {
    expect(matchScore("ide", ["provide the numbers"])).toBe(0);
    expect(matchScore("ide", ["Agentic IDE"])).toBeGreaterThan(0);
  });
});

describe("rankItems", () => {
  it("ranks better matches first, caps the list and keeps input order on ties", () => {
    const items = ["blog speed", "speed", "speedy blog", "other"];
    expect(rankItems("speed", items, (s) => [s], 2)).toEqual(["speed", "speedy blog"]);
    expect(rankItems("blog", items, (s) => [s], 5)).toEqual(["blog speed", "speedy blog"]);
  });
});

describe("folderName", () => {
  it("takes the last segment on either separator", () => {
    expect(folderName("C:\\work\\blog")).toBe("blog");
    expect(folderName("/home/me/site/")).toBe("site");
  });
});
