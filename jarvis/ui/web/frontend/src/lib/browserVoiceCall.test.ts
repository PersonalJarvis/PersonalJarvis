import { afterEach, describe, expect, it, vi } from "vitest";

import {
  browserVoiceCallLive,
  registerBrowserVoiceCallOwner,
  setBrowserVoiceCallLive,
  startBrowserVoiceCall,
  stopBrowserVoiceCall,
} from "./browserVoiceCall";

describe("browserVoiceCall", () => {
  let unregister: (() => void) | null = null;

  afterEach(() => {
    unregister?.();
    unregister = null;
    setBrowserVoiceCallLive(false);
  });

  it("reports that nobody can hold a call before an owner registers", () => {
    expect(startBrowserVoiceCall()).toBe(false);
    expect(stopBrowserVoiceCall()).toBe(false);
  });

  it("hands Start to the owner and Stop only while a call is live", () => {
    const owner = { start: vi.fn(), stop: vi.fn() };
    unregister = registerBrowserVoiceCallOwner(owner);

    expect(startBrowserVoiceCall()).toBe(true);
    expect(owner.start).toHaveBeenCalledTimes(1);
    // Nothing is open yet, so a Stop is not this browser's to handle.
    expect(stopBrowserVoiceCall()).toBe(false);

    setBrowserVoiceCallLive(true);
    expect(browserVoiceCallLive()).toBe(true);
    expect(stopBrowserVoiceCall()).toBe(true);
    expect(owner.stop).toHaveBeenCalledTimes(1);
  });

  it("forgets the call when its owner unmounts", () => {
    const owner = { start: vi.fn(), stop: vi.fn() };
    const release = registerBrowserVoiceCallOwner(owner);
    setBrowserVoiceCallLive(true);

    release();

    expect(browserVoiceCallLive()).toBe(false);
    expect(startBrowserVoiceCall()).toBe(false);
  });

  it("ignores a stale unregister after a newer owner took over", () => {
    const first = registerBrowserVoiceCallOwner({ start: vi.fn(), stop: vi.fn() });
    const second = { start: vi.fn(), stop: vi.fn() };
    unregister = registerBrowserVoiceCallOwner(second);

    first();

    expect(startBrowserVoiceCall()).toBe(true);
    expect(second.start).toHaveBeenCalledTimes(1);
  });
});
