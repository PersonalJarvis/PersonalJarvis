import { afterEach, expect, it, vi } from "vitest";

afterEach(() => vi.unstubAllGlobals());

it("measures remote audio on the render thread without animation frames or audible duplication", async () => {
  const messages: { type: string; rms: number }[] = [];
  const processors = new Map<string, new () => { process(inputs: Float32Array[][], outputs: Float32Array[][]): boolean }>();
  vi.stubGlobal("sampleRate", 48_000);
  vi.stubGlobal("AudioWorkletProcessor", class {
    port = { postMessage: (message: { type: string; rms: number }) => messages.push(message) };
  });
  vi.stubGlobal("registerProcessor", (name: string, processor: never) => processors.set(name, processor));
  // A hidden WebView does not deliver animation callbacks. Audio still does.
  const animate = vi.fn();
  vi.stubGlobal("requestAnimationFrame", animate);
  await import("./pcm-worklet");
  const Meter = processors.get("pcm-level")!;
  const meter = new Meter();
  const output = new Float32Array(128);
  for (let frame = 0; frame < 13; frame++) {
    expect(meter.process([[new Float32Array(128).fill(0.125)]], [[output]])).toBe(true);
  }
  expect(messages).toEqual([{ type: "level", rms: 0.125 }]);
  expect(output.every(sample => sample === 0)).toBe(true);
  for (let frame = 0; frame < 13; frame++) meter.process([], [[output]]);
  expect(messages.at(-1)).toEqual({ type: "level", rms: 0 });
  expect(animate).not.toHaveBeenCalled();
});
