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

describe("playDockTick", () => {
  beforeEach(() => {
    vi.resetModules();
    delete (window as unknown as { AudioContext?: unknown }).AudioContext;
    delete (window as unknown as { webkitAudioContext?: unknown })
      .webkitAudioContext;
    delete (navigator as unknown as { userActivation?: unknown }).userActivation;
    try {
      localStorage.removeItem("jarvis.ui.sound");
    } catch {
      /* ignore */
    }
  });

  it("is a no-op and never throws when WebAudio is unavailable", async () => {
    const { playDockTick } = await import("./sound");
    expect(() => playDockTick()).not.toThrow();
    expect(() => playDockTick("select")).not.toThrow();
  });

  it("plays one short body voice per tick, even on an engine without buffers", async () => {
    const fake = installFakeAudio();
    const { playDockTick } = await import("./sound");
    playDockTick("hover");
    expect(fake.ctorSpy).toHaveBeenCalledTimes(1);
    expect(fake.oscillators.length).toBe(1);
    expect(fake.started.length).toBe(1);
    // Sub-40 ms: a detent, not a note.
    expect(fake.stopped[0]).toBeLessThan(0.05);
  });

  it("rate-limits: two ticks on the same audio clock play once", async () => {
    const fake = installFakeAudio();
    const { playDockTick } = await import("./sound");
    playDockTick("hover");
    playDockTick("hover");
    expect(fake.oscillators.length).toBe(1);
  });

  it("gives the pick a lower voice than the pass", async () => {
    const fake = installFakeAudio();
    const { playDockTick } = await import("./sound");
    playDockTick("select");
    const osc = fake.oscillators[0] as { frequency: { setValueAtTime: ReturnType<typeof vi.fn> } };
    const start = osc.frequency.setValueAtTime.mock.calls[0][0] as number;
    // 1300 Hz ± 4 % jitter — well under the 2100 Hz hover voice.
    expect(start).toBeGreaterThan(1200);
    expect(start).toBeLessThan(1400);
  });

  it("does not even create a context before the page has seen a gesture", async () => {
    const fake = installFakeAudio();
    (navigator as unknown as { userActivation: unknown }).userActivation = {
      hasBeenActive: false,
    };
    const { playDockTick } = await import("./sound");
    playDockTick();
    expect(fake.ctorSpy).not.toHaveBeenCalled();
  });

  it("stays silent when the user muted UI sound", async () => {
    const fake = installFakeAudio();
    localStorage.setItem("jarvis.ui.sound", "off");
    const { playDockTick } = await import("./sound");
    playDockTick();
    expect(fake.ctorSpy).not.toHaveBeenCalled();
  });
});
