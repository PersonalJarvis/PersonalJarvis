import { describe, it, expect } from "vitest";
import { SLO_STATUSES, RUN_DECISION_KINDS, TRANSCRIPT_ROLES, RUN_OUTCOMES } from "../types";

describe("run enum parity", () => {
  it("SLO statuses match the Python SSOT order", () => {
    expect(SLO_STATUSES).toEqual(["ok", "warn", "breach"]);
  });
  it("decision kinds match the Python SSOT set", () => {
    expect([...RUN_DECISION_KINDS].sort()).toEqual(
      ["brain", "fallback", "mission", "risk", "route", "tier"],
    );
  });
  it("transcript roles match the Python SSOT order", () => {
    expect(TRANSCRIPT_ROLES).toEqual(["user", "jarvis", "system", "tool", "error"]);
  });
  it("run outcomes match the Python SSOT order", () => {
    expect(RUN_OUTCOMES).toEqual(["success", "partial", "failed"]);
  });
});
