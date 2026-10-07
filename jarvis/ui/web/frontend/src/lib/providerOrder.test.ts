import { describe, expect, it } from "vitest";

import { moveId, orderBy } from "./providerOrder";

describe("provider order", () => {
  it("puts placed providers first and keeps the rest in catalog order", () => {
    expect(orderBy(["a", "b", "c", "d"], (id) => id, ["c", "a"])).toEqual(["c", "a", "b", "d"]);
  });

  it("moves a provider onto another's place", () => {
    expect(moveId(["a", "b", "c"], "c", "a")).toEqual(["c", "a", "b"]);
    expect(moveId(["a", "b", "c"], "a", "b")).toEqual(["b", "a", "c"]);
    expect(moveId(["a", "b"], "x", "a")).toEqual(["a", "b"]);
  });
});
