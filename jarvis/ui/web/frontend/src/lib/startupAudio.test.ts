import { describe, expect, it } from "vitest";
import { StartupAudioQueue } from "./startupAudio";

it("discards muted audio and resumes retention without releasing it early", () => {
  const queue = new StartupAudioQueue(16000);
  const output = new Float32Array(128);
  queue.prepend(new Float32Array(128).fill(0.5));
  queue.process(new Float32Array(128).fill(0.2), output);
  queue.suspend();
  queue.process(new Float32Array(128).fill(0.8), output);
  expect(queue.pendingMs).toBe(0);
  queue.resume();
  queue.process(new Float32Array(128).fill(0.3), output);
  expect(output.every(v => v === 0)).toBe(true);
  queue.start();
  queue.process(new Float32Array(), output);
  expect(output.every(v => Math.abs(v - 0.3) < 0.001)).toBe(true);
});

describe("startup RTP audio", () => {
  it.each([16000, 24000, 44100, 48000])("bounds a four-second silent opening to 100 ms and catches up within 300 ms at %s Hz", rate => {
    const queue = new StartupAudioQueue(rate);
    const block = 128;
    const silence = new Float32Array(block), output = new Float32Array(block);
    for (let n = 0; n < rate * 4; n += block) queue.process(silence, output);
    expect(queue.pendingMs).toBeGreaterThanOrEqual(4000);
    queue.start();
    expect(queue.pendingMs).toBeCloseTo(100, 3);
    queue.start(); // Repeated readiness must not alter the retained opening.
    expect(queue.pendingMs).toBeCloseTo(100, 3);
    for (let n = 0; n < rate * 0.3; n += block) queue.process(silence, output);
    expect(queue.pendingMs).toBeLessThan(10);
    const fresh = Float32Array.from({ length: block }, (_, i) => Math.sin(i / 7) * 0.0005);
    queue.process(fresh, output);
    expect(output).toEqual(fresh);
  });

  it("compacts across the native/browser boundary without removing the quiet onset or spoken pauses", () => {
    const rate = 16000, queue = new StartupAudioQueue(rate);
    const quiet = Float32Array.from([1e-30, -1e-30, 0.0005, -0.0005]);
    const pause = new Float32Array(rate);
    queue.prepend(new Float32Array(rate));
    queue.process(new Float32Array(rate), new Float32Array(128));
    queue.process(quiet, new Float32Array(128));
    queue.process(pause, new Float32Array(128));
    queue.process(quiet, new Float32Array(128));
    queue.start();
    const expected = new Float32Array(rate / 10 + quiet.length * 2 + pause.length);
    expected.set(quiet, rate / 10);
    expected.set(quiet, rate / 10 + quiet.length + pause.length);
    expect(queue.pendingMs).toBeCloseTo(expected.length / rate * 1000, 3);
    // Drain in one block to inspect compaction independently of the existing
    // pitch-preserving replay. Every remaining sample must be bit-exact.
    const output = new Float32Array(expected.length);
    queue.process(new Float32Array(), output);
    expect(output).toEqual(expected);
  });

  it("retains browser-leading silence when the native prefix already contains speech", () => {
    const rate = 16000, queue = new StartupAudioQueue(rate);
    const quiet = Float32Array.from([1e-30, -1e-30]);
    queue.prepend(quiet);
    queue.process(new Float32Array(rate * 4), new Float32Array(128));
    queue.start();
    const expected = new Float32Array(quiet.length + rate * 4);
    expected.set(quiet);
    expect(queue.pendingMs).toBeCloseTo(expected.length / rate * 1000, 3);
    const output = new Float32Array(expected.length);
    queue.process(new Float32Array(), output);
    expect(output).toEqual(expected);
  });

  it("leaves a short quiet opening unchanged and forgets compaction state after mute or cancellation", () => {
    const rate = 16000, queue = new StartupAudioQueue(rate);
    const shortOpening = new Float32Array(rate / 20).fill(0.0005);
    queue.process(shortOpening, new Float32Array(128));
    queue.start();
    expect(queue.pendingMs).toBe(50);
    const output = new Float32Array(shortOpening.length);
    queue.process(new Float32Array(), output);
    expect(output).toEqual(shortOpening);
    queue.suspend();
    queue.process(new Float32Array(rate).fill(0.7), output);
    queue.resume();
    queue.process(new Float32Array(rate * 4), output);
    queue.start();
    expect(queue.pendingMs).toBe(100);
    queue.suspend();
    queue.resume();
    queue.process(shortOpening, output);
    queue.start();
    queue.process(new Float32Array(), output);
    expect(output).toEqual(shortOpening);
  });

  it.each([16000, 24000, 44100, 48000])("pays down a two-second backlog despite constant room noise at %s Hz", rate => {
    const queue = new StartupAudioQueue(rate);
    const block = 128;
    const output = new Float32Array(block);
    for (let n = 0; n < rate * 2; n += block) queue.process(new Float32Array(block).fill(0.02), output);
    queue.start();
    for (let n = 0; n < rate * 5; n += block) queue.process(new Float32Array(block).fill(0.0005), output);
    expect(queue.pendingMs).toBeLessThan(10);
    const fresh = Float32Array.from({ length: block }, (_, i) => Math.sin(i / 7) * 0.15);
    queue.process(fresh, output);
    expect(output).toEqual(fresh); // Bit-exact live capture once the debt is paid.
  });

  it("preserves the pitch and quiet amplitude of buffered speech instead of gating it away", () => {
    const rate = 48000, pitch = 200, block = 128;
    const queue = new StartupAudioQueue(rate);
    const output = new Float32Array(block);
    const tone = (n: number) => Float32Array.from({ length: block }, (_, i) => Math.sin(2 * Math.PI * pitch * (n + i) / rate) * 0.0005);
    for (let n = 0; n < rate; n += block) queue.process(tone(n), output);
    queue.start();
    const heard: number[] = [];
    for (let n = rate; n < rate * 1.5; n += block) {
      queue.process(tone(n), output);
      heard.push(...output);
    }
    const crossings = heard.slice(1).filter((sample, i) => sample >= 0 && heard[i] < 0).length;
    expect(crossings / (heard.length / rate)).toBeCloseTo(pitch, -1);
    expect(Math.max(...heard.map(Math.abs))).toBeGreaterThan(0.00045);
    expect(queue.pendingMs).toBeLessThan(850);
  });

  it.each([0, 50, 60])("also catches up with random noise and mains hum (%s Hz)", frequency => {
    const rate = 48000, block = 128;
    const queue = new StartupAudioQueue(rate), output = new Float32Array(block);
    let random = 12345;
    const frame = (n: number) => Float32Array.from({ length: block }, (_, i) => {
      random = (1664525 * random + 1013904223) >>> 0;
      return frequency ? Math.sin((n + i) * 2 * Math.PI * frequency / rate) * 0.0005 : (random / 4294967296 - 0.5) * 0.001;
    });
    for (let n = 0; n < rate * 2; n += block) queue.process(frame(n), output);
    queue.start();
    for (let n = 0; n < rate * 10; n += block) queue.process(frame(n), output);
    expect(queue.pendingMs).toBe(0);
  });

  it("clears a partly rendered catch-up grain when a connection is replaced", () => {
    const queue = new StartupAudioQueue(48000);
    const out = new Float32Array(128);
    queue.process(new Float32Array(48000).fill(0.1), out);
    queue.start();
    queue.process(new Float32Array(128).fill(0.1), out);
    queue.suspend();
    queue.process(new Float32Array(128).fill(0.8), out);
    queue.start();
    const fresh = new Float32Array(128).fill(0.2);
    queue.process(fresh, out);
    expect(out).toEqual(fresh);
    expect(queue.pendingMs).toBe(0);
  });

  it("keeps gain bounded at waveform joins", () => {
    const queue = new StartupAudioQueue(48000);
    const output = new Float32Array(128);
    for (let i = 0; i < 600; i++) queue.process(new Float32Array(128).fill(i % 2 ? 0.7 : -0.7), output);
    queue.start();
    for (let i = 0; i < 1800; i++) {
      queue.process(new Float32Array(128).fill(i % 2 ? 0.4 : -0.4), output);
      expect(output.every(value => Number.isFinite(value) && Math.abs(value) <= 0.701)).toBe(true);
    }
  });

  it("retains the first sentence during negotiation and prepends the wake mic exactly once", () => {
    const queue = new StartupAudioQueue(100);
    const output = new Float32Array(2);
    queue.process(new Float32Array([0.3, 0.4]), output);
    expect([...output]).toEqual([0, 0]);
    queue.process(new Float32Array([0.5, 0.6]), output);
    queue.prepend(new Float32Array([0.1, 0.2]));
    queue.start();
    queue.start();
    const heard: number[] = [];
    for (let i = 0; i < 3; i++) {
      queue.process(new Float32Array(2), output);
      heard.push(...output);
    }
    expect(heard.map(v => Math.round(v * 10))).toEqual([1, 2, 3, 4, 5, 6]);
    expect(() => queue.prepend(new Float32Array([1]))).toThrow();
  });

  it("catches up during digital silence without discarding quiet speech", () => {
    const queue = new StartupAudioQueue(100);
    const output = new Float32Array(10);
    for (let i = 0; i < 10; i++) queue.process(new Float32Array(10).fill(0.0005), output);
    queue.start();
    for (let i = 0; i < 20; i++) queue.process(new Float32Array(10), output);
    queue.process(new Float32Array(10).fill(0.7), output);
    expect(output[0]).toBeCloseTo(0.7); // Live again, no permanent startup delay.
  });

  it("never carries microphone frames over reconnect or cancellation", () => {
    const queue = new StartupAudioQueue(100);
    const output = new Float32Array(2);
    queue.process(new Float32Array([0.1, 0.2]), output);
    queue.suspend();
    queue.process(new Float32Array([0.3, 0.4]), output);
    queue.start();
    queue.process(new Float32Array([0.5, 0.6]), output);
    expect([...output].map(v => Math.round(v * 10))).toEqual([5, 6]);
  });

  it("refuses overflow instead of silently truncating an opening command", () => {
    const queue = new StartupAudioQueue(1);
    const output = new Float32Array(31);
    expect(() => queue.process(new Float32Array(31).fill(0.1), output)).toThrow(/buffer exceeded/);
  });

  it("includes the native prefix in the startup memory bound before media starts", () => {
    const queue = new StartupAudioQueue(100);
    queue.prepend(new Float32Array(2000));
    expect(queue.pendingMs).toBe(20000);
    expect(() => queue.process(new Float32Array(1500), new Float32Array(2))).toThrow(/buffer exceeded/);
  });
});
