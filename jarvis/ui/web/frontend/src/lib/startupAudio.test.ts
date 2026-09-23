import { describe, expect, it } from "vitest";
import { StartupAudioQueue } from "./startupAudio";

describe("startup RTP audio", () => {
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
});
