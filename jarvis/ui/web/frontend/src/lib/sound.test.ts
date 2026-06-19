import { describe, it, expect, vi, beforeEach } from "vitest";

/** Minimal fake WebAudio graph that records what `playDropConfirm` builds. */
function installFakeAudio() {
  const started: number[] = [];
  const stopped: number[] = [];
  const oscillators: unknown[] = [];
  class FakeParam {
    value = 0;
    setValueAtTime = vi.fn();
    exponentialRampToValueAtTime = vi.fn();
    linearRampToValueAtTime = vi.fn();
  }
  class FakeGain {
    gain = new FakeParam();
    connect = vi.fn();
  }
  class FakeOsc {
    type = "sine";
    frequency = new FakeParam();
    connect = vi.fn();
    start = vi.fn((t?: number) => started.push(t ?? 0));
    stop = vi.fn((t?: number) => stopped.push(t ?? 0));
    constructor() {
      oscillators.push(this);
    }
  }
  class FakeCtx {
    state = "running";
    currentTime = 0;
    destination = {};
    resume = vi.fn(() => Promise.resolve());
    createGain = vi.fn(() => new FakeGain());
    createOscillator = vi.fn(() => new FakeOsc());
  }
  const ctorSpy = vi.fn(() => new FakeCtx());
  (window as unknown as { AudioContext: unknown }).AudioContext = ctorSpy;
  return { ctorSpy, oscillators, started, stopped };
}

describe("playDropConfirm", () => {
  beforeEach(() => {
    vi.resetModules();
    delete (window as unknown as { AudioContext?: unknown }).AudioContext;
    delete (window as unknown as { webkitAudioContext?: unknown })
      .webkitAudioContext;
    try {
      localStorage.removeItem("jarvis.ui.sound");
    } catch {
      /* ignore */
    }
  });

  it("is a no-op and never throws when WebAudio is unavailable", async () => {
    const { playDropConfirm } = await import("./sound");
    expect(() => playDropConfirm()).not.toThrow();
  });

  it("builds and starts soft oscillator voices when WebAudio is present", async () => {
    const fake = installFakeAudio();
    const { playDropConfirm } = await import("./sound");
    playDropConfirm();
    expect(fake.ctorSpy).toHaveBeenCalledTimes(1);
    // At least two voices for a warm, non-beepy timbre.
    expect(fake.oscillators.length).toBeGreaterThanOrEqual(2);
    expect(fake.started.length).toBeGreaterThanOrEqual(2);
    expect(fake.stopped.length).toBeGreaterThanOrEqual(2);
  });

  it("reuses a single AudioContext across calls", async () => {
    const fake = installFakeAudio();
    const { playDropConfirm } = await import("./sound");
    playDropConfirm();
    playDropConfirm();
    expect(fake.ctorSpy).toHaveBeenCalledTimes(1);
  });

  it("stays silent when the user muted UI sound", async () => {
    const fake = installFakeAudio();
    localStorage.setItem("jarvis.ui.sound", "off");
    const { playDropConfirm } = await import("./sound");
    playDropConfirm();
    expect(fake.ctorSpy).not.toHaveBeenCalled();
  });
});
