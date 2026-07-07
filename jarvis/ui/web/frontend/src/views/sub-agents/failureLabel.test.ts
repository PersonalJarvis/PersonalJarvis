import { describe, expect, it } from "vitest";

import { failureLabel } from "./failureLabel";

const t = (key: string) => `[${key}]`; // identity-ish stub: proves the key used

describe("failureLabel", () => {
  it("maps a known error_class to its i18n message and appends the detail", () => {
    expect(
      failureLabel(
        { error: "Failed to authenticate. API Error: 401", error_class: "provider_auth" },
        t,
      ),
    ).toBe("[subagents_view.error_class.provider_auth] (Failed to authenticate. API Error: 401)");
  });

  it("falls back to the raw error when the class is unknown/legacy", () => {
    expect(failureLabel({ error: "task_error", error_class: "OrchestratorCrash" }, t)).toBe(
      "task_error",
    );
    expect(failureLabel({ error: "task_error", error_class: null }, t)).toBe("task_error");
  });

  it("returns null when there is no error at all", () => {
    expect(failureLabel({ error: null, error_class: null }, t)).toBeNull();
  });

  it("uses the mapped message alone when no detail text exists", () => {
    expect(failureLabel({ error: null, error_class: "worker_timeout" }, t)).toBe(
      "[subagents_view.error_class.worker_timeout]",
    );
  });
});
