import { describe, expect, it } from "vitest";

import { wakeActivationLocalState } from "./wakeActivation";

describe("wakeActivationLocalState", () => {
  it("has nothing to explain for a grant, another OS, or an older backend", () => {
    expect(wakeActivationLocalState(undefined)).toBeNull();
    expect(wakeActivationLocalState({ outcome: "granted", reason: "", can_open_settings: false })).toBeNull();
    expect(wakeActivationLocalState({ outcome: "not_required", reason: "", can_open_settings: false })).toBeNull();
  });

  it("reads pending as macOS asking right now", () => {
    expect(
      wakeActivationLocalState({ outcome: "pending", reason: "not_determined", can_open_settings: true }),
    ).toEqual({
      permissions: ["microphone"],
      reason: "not_determined",
      phase: "os_dialog",
      can_open_settings: true,
    });
  });

  it("reads every other outcome as the person having to act", () => {
    const state = wakeActivationLocalState({ outcome: "needs_settings", reason: "needs_settings", can_open_settings: true });
    expect(state?.phase).toBe("blocked");
    expect(state?.reason).toBe("needs_settings");
    expect(wakeActivationLocalState({ outcome: "denied", reason: "denied", can_open_settings: true })?.reason).toBe("denied");
  });

  it("never trusts a reason outside the shared vocabulary: the outcome decides", () => {
    expect(wakeActivationLocalState({ outcome: "denied", reason: "invented" as never, can_open_settings: true })?.reason).toBe("denied");
    expect(wakeActivationLocalState({ outcome: "unavailable", reason: "", can_open_settings: false })?.reason).toBe("unavailable");
    expect(wakeActivationLocalState({ outcome: "mystery" as never, reason: "", can_open_settings: false })?.reason).toBe("unavailable");
  });
});
