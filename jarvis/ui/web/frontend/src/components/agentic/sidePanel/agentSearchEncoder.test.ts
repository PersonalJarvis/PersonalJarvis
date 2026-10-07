import { describe, expect, it } from "vitest";
import { meanPoolTokens } from "./agentSearchEncoder";

describe("search sentence pooling", () => {
  it("averages token positions without mixing embedding dimensions", () => {
    expect(meanPoolTokens(new Float32Array([2, 4, 4, 8, 6, 12]), 3, 2)).toEqual([4, 8]);
  });
  it("rejects malformed or empty encoder output", () => {
    expect(() => meanPoolTokens(new Float32Array(), 0, 2)).toThrow();
    expect(() => meanPoolTokens(new Float32Array([1, 2, 3]), 2, 2)).toThrow();
  });
});
