import { describe, expect, it } from "vitest";
import { promptActions } from "./promptActions";

const base = { can_prompt: false, can_open_settings: true, outside_app: false };
const idle = { returnedFromSettings: false, stillOff: false, canReset: false };

describe("promptActions", () => {
  it("not_determined with a prompt: Continue, then Not now", () => {
    expect(promptActions({ ...base, reason: "not_determined", can_prompt: true }, idle)).toEqual([
      "continue",
      "not_now",
    ]);
  });

  it("denied and needs_settings: Open System Settings and Check again", () => {
    for (const reason of ["denied", "needs_settings"] as const) {
      expect(promptActions({ ...base, reason }, idle)).toEqual(["open_settings", "check_again", "not_now"]);
    }
  });

  it("offers Reset and ask again only after returning from Settings while it is still off", () => {
    const denied = { ...base, reason: "denied" as const };

    expect(promptActions(denied, { returnedFromSettings: true, stillOff: true, canReset: true })).toContain("reset");
    expect(promptActions(denied, { returnedFromSettings: false, stillOff: true, canReset: true })).not.toContain("reset");
    expect(promptActions(denied, { returnedFromSettings: true, stillOff: false, canReset: true })).not.toContain("reset");
    expect(promptActions(denied, { returnedFromSettings: true, stillOff: true, canReset: false })).not.toContain("reset");
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
    expect(
      promptActions({ ...base, reason: "needs_settings", can_prompt: true, outside_app: true }, idle),
    ).toEqual(["allow_outside", "open_settings", "check_again", "not_now"]);
    expect(
      promptActions({ ...base, reason: "not_determined", can_prompt: true, outside_app: true }, idle),
    ).toEqual(["allow_outside", "not_now"]);
  });

  it("never offers a Settings button the backend says is not available", () => {
    expect(promptActions({ ...base, reason: "denied", can_open_settings: false }, idle)).toEqual(["not_now"]);
  });
});
