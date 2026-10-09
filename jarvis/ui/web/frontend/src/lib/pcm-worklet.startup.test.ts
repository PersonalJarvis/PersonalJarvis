import { afterEach, expect, it, vi } from "vitest";

afterEach(() => vi.unstubAllGlobals());

it("acknowledges the actual render quantum and catchup without sending pre-activation audio", async () => {
  type Processor = {
    port: { onmessage: (e: { data: unknown }) => void; postMessage: ReturnType<typeof vi.fn> };
    process: (inputs: Float32Array[][], outputs: Float32Array[][]) => void;
  };
  const processors = new Map<string, new () => Processor>();
  vi.stubGlobal("sampleRate", 48000);
  vi.stubGlobal("currentTime", 0);
  vi.stubGlobal("registerProcessor", (name: string, ctor: unknown) => processors.set(name, ctor as never));
  vi.stubGlobal("AudioWorkletProcessor", class { port = { onmessage: null, postMessage: vi.fn() }; });
  await import("./pcm-worklet");
  const capture = new (processors.get("pcm-startup")!)();
  const block = new Float32Array(128).fill(0.02), output = new Float32Array(128);
  for (let i = 0; i < 750; i++) {
    capture.process([[block]], [[output]]);
    expect(output.every(value => value === 0)).toBe(true);
  }
  expect(capture.port.postMessage).not.toHaveBeenCalled();
  capture.port.onmessage({ data: { type: "start" } });
  expect(capture.port.postMessage).not.toHaveBeenCalled();
  capture.process([[block]], [[output]]);
  expect(capture.port.postMessage).toHaveBeenCalledWith({ type: "input_started", pending_ms: expect.any(Number) });
  expect(output.some(value => value > 0)).toBe(true);
  expect(capture.port.postMessage).not.toHaveBeenCalledWith({ type: "input_caught_up" });
  for (let i = 0; i < 2_000; i++) capture.process([[block]], [[output]]);
  expect(capture.port.postMessage).toHaveBeenCalledWith({ type: "input_caught_up" });
  expect(capture.port.postMessage).toHaveBeenCalledTimes(2);
  capture.port.onmessage({ data: { type: "suspend" } });
  capture.process([[block]], [[output]]);
  expect(output.every(value => value === 0)).toBe(true);
});
