import { describe, expect, it } from "vitest";
import { isSettingsReason, promptActions } from "./promptActions";

const base = { can_prompt: false, can_open_settings: true, outside_app: false };
const idle = { returnedFromSettings: false, stillOff: false, canReset: false };

describe("promptActions", () => {
  it("not_determined with a prompt: Continue, then Not now", () => {
    expect(promptActions({ ...base, reason: "not_determined", can_prompt: true }, idle)).toEqual([
      "continue",
      "not_now",
    ]);
  });

  it("denied and needs_settings: ONE primary (Open System Settings) and a quiet Not now", () => {
    for (const reason of ["denied", "needs_settings"] as const) {
      expect(promptActions({ ...base, reason }, idle)).toEqual(["open_settings", "not_now"]);
    }
  });

  it("Check again appears only after the person came back from Settings and it still reads off", () => {
    const denied = { ...base, reason: "denied" as const };

    expect(promptActions(denied, { ...idle, returnedFromSettings: true, stillOff: true })).toEqual([
      "open_settings",
      "check_again",
      "not_now",
    ]);
    // Came back and it reads on (the card is about to resolve): nothing to check.
    expect(promptActions(denied, { ...idle, returnedFromSettings: true, stillOff: false })).not.toContain("check_again");
    // Never opened Settings from here: the watcher and the focus refetch notice a grant by themselves.
    expect(promptActions(denied, { ...idle, returnedFromSettings: false, stillOff: true })).not.toContain("check_again");
  });

  it("offers Reset and ask again only after returning from Settings while it is still off", () => {
    const denied = { ...base, reason: "denied" as const };

    expect(promptActions(denied, { returnedFromSettings: true, stillOff: true, canReset: true })).toContain("reset");
    expect(promptActions(denied, { returnedFromSettings: false, stillOff: true, canReset: true })).not.toContain("reset");
    expect(promptActions(denied, { returnedFromSettings: true, stillOff: false, canReset: true })).not.toContain("reset");
    expect(promptActions(denied, { returnedFromSettings: true, stillOff: true, canReset: false })).not.toContain("reset");
  });

  it("Screen Recording and Input Monitoring offer Quit and reopen BEFORE a reset once back from Settings", () => {
    const denied = { ...base, reason: "denied" as const };
    const back = { returnedFromSettings: true, stillOff: true, canReset: true };

    expect(promptActions(denied, { ...back, restartMayHelp: true })).toEqual([
      "open_settings",
      "check_again",
      "restart",
      "reset",
      "not_now",
    ]);
    // Any other permission keeps the plain reset.
    expect(promptActions(denied, back)).not.toContain("restart");
    // Not back from Settings yet: no restart suggestion (nothing says the grant exists).
    expect(promptActions(denied, { ...back, returnedFromSettings: false, restartMayHelp: true })).not.toContain(
      "restart",
    );
  });

  it("restart_hint: Quit and reopen only", () => {
    expect(promptActions({ ...base, reason: "restart_hint" }, idle)).toEqual(["restart", "not_now"]);
  });

  it("restricted and unavailable: an explanation only, nothing to click but Not now", () => {
    for (const reason of ["restricted", "unavailable"] as const) {
      expect(promptActions({ ...base, reason, can_prompt: true }, idle)).toEqual(["not_now"]);
    }
  });

  it("outside the installed app the confirmation replaces Continue", () => {
    // ONE primary: the confirmation is the way forward, so System Settings is not offered next to it.
    expect(
      promptActions({ ...base, reason: "needs_settings", can_prompt: true, outside_app: true }, idle),
    ).toEqual(["allow_outside", "not_now"]);
    expect(
      promptActions({ ...base, reason: "not_determined", can_prompt: true, outside_app: true }, idle),
    ).toEqual(["allow_outside", "not_now"]);
  });

  it("never offers a Settings button the backend says is not available", () => {
    expect(promptActions({ ...base, reason: "denied", can_open_settings: false }, idle)).toEqual(["not_now"]);
  });
});

describe("isSettingsReason", () => {
  it("is true only where a switch in System Settings is the way forward", () => {
    expect(isSettingsReason("denied")).toBe(true);
    expect(isSettingsReason("needs_settings")).toBe(true);
    for (const reason of ["not_determined", "restricted", "restart_hint", "unavailable"]) {
      expect(isSettingsReason(reason), reason).toBe(false);
    }
  });
});
