import { describe, expect, it } from "vitest";

import { resolveAgentsMode } from "./agentsMode";

describe("agent view preference migration", () => {
  it("opens the original world when no preference exists", () => {
    expect(resolveAgentsMode(null, null, true)).toBe("world");
  });

  it.each([null, "world", "ledger"])(
    "restores a saved city to world even when the old preference is %s",
    (legacy) => {
      expect(resolveAgentsMode("city", legacy, true)).toBe("world");
    },
  );

  it.each(["world", "ledger"] as const)(
    "preserves the current %s preference",
    (stored) => {
      expect(resolveAgentsMode(stored, stored === "world" ? "ledger" : "world", true)).toBe(stored);
    },
  );

  it("honors a legacy ledger choice when the current preference is unset or invalid", () => {
    expect(resolveAgentsMode(null, "ledger", true)).toBe("ledger");
    expect(resolveAgentsMode("unknown", "ledger", true)).toBe("ledger");
    expect(resolveAgentsMode("unknown", null, true)).toBe("world");
  });

  it.each([null, "city", "world", "ledger", "unknown"])(
    "uses the ledger without WebGL for stored preference %s",
    (stored) => {
      expect(resolveAgentsMode(stored, null, false)).toBe("ledger");
    },
  );
});
