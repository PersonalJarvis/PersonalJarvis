import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  isAutopilotToastsEnabled,
  onAutopilotToastsChange,
  setAutopilotToastsEnabled,
} from "@/lib/autopilotToasts";

describe("autopilotToasts preference", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("defaults to on and persists off across reads", () => {
    expect(isAutopilotToastsEnabled()).toBe(true);
    setAutopilotToastsEnabled(false);
    expect(isAutopilotToastsEnabled()).toBe(false);
    setAutopilotToastsEnabled(true);
    expect(isAutopilotToastsEnabled()).toBe(true);
  });

  it("notifies listeners in this window when the preference changes", () => {
    const listener = vi.fn();
    const off = onAutopilotToastsChange(listener);
    setAutopilotToastsEnabled(false);
    expect(listener).toHaveBeenCalledTimes(1);
    off();
    setAutopilotToastsEnabled(true);
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("falls back to the default when storage is blocked", () => {
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(isAutopilotToastsEnabled()).toBe(true);
    spy.mockRestore();
  });
});
