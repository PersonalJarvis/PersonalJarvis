import { afterEach, expect, it, vi } from "vitest";

afterEach(() => vi.unstubAllGlobals());

it("flushes queued PCM on mute and never replays samples received while muted", async () => {
  const processors = new Map<string, new () => {
    port: { onmessage: (e: { data: unknown }) => void };
    process: (inputs: Float32Array[][], outputs: Float32Array[][]) => void;
  }>();
  vi.stubGlobal("sampleRate", 24000);
  vi.stubGlobal("registerProcessor", (name: string, ctor: unknown) => processors.set(name, ctor as never));
  vi.stubGlobal("AudioWorkletProcessor", class {
    port = { onmessage: null, postMessage: vi.fn() };
  });
  await import("./pcm-worklet");
  const playback = new (processors.get("pcm-playback")!)();
  const send = (data: unknown) => playback.port.onmessage({ data });
  const pcm = (sample: number) => ({ type: "pcm", data: new Int16Array(24000).fill(sample).buffer });
  const render = () => {
    const block = new Float32Array(128);
    playback.process([], [[block]]);
    return block;
  };
  send({ type: "output_state", muted: false, volume: 0.5 });
  send(pcm(10000));
  expect(render().some(x => x > 0)).toBe(true);
  send({ type: "output_state", muted: true, volume: 0.5 });
  send(pcm(20000));
  expect(render().every(x => x === 0)).toBe(true);
  send({ type: "output_state", muted: false, volume: 0.5 });
  expect(render().every(x => x === 0)).toBe(true);
  send(pcm(3000));
  expect(Math.max(...render())).toBeCloseTo(3000 / 32768 * 0.5);
});
