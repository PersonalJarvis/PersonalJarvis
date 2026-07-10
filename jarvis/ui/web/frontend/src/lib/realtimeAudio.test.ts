import { describe, it, expect, beforeEach } from "vitest";
import { buildAudioSocketUrl, StreamingPcm16Resampler } from "./realtimeAudio";

describe("realtime audio client", () => {
  beforeEach(() => {
    // @ts-expect-error test shim
    global.window = { location: { protocol: "https:", host: "app.example" }, __JARVIS_TOKEN: "tok" };
  });

  it("builds a wss /ws/audio url with the token", () => {
    expect(buildAudioSocketUrl()).toBe("wss://app.example/ws/audio?token=tok");
  });

  it("resamples provider PCM from 24 kHz to a 48 kHz AudioContext", () => {
    const input = Int16Array.from({ length: 2_400 }, (_, i) => i - 1_200);
    const output = new Int16Array(
      new StreamingPcm16Resampler(24_000, 48_000).process(input.buffer),
    );

    expect(output.length).toBeGreaterThanOrEqual(4_798);
    expect(output.length).toBeLessThanOrEqual(4_800);
  });

  it("keeps interpolation continuous across WebSocket frame boundaries", () => {
    const input = Int16Array.from({ length: 2_400 }, (_, i) => i * 4 - 4_800);
    const whole = new Int16Array(
      new StreamingPcm16Resampler(24_000, 48_000).process(input.buffer),
    );
    const streamed = new StreamingPcm16Resampler(24_000, 48_000);
    const first = new Int16Array(streamed.process(input.slice(0, 1_200).buffer));
    const second = new Int16Array(streamed.process(input.slice(1_200).buffer));

    expect([...first, ...second]).toEqual([...whole]);
  });
});
