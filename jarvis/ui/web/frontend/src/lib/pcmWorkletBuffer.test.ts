import { describe, expect, it } from "vitest";

import {
  BoundedPcm16Queue,
  Pcm16Packetizer,
  capturePacketSampleCount,
  playbackBufferSampleCount,
} from "./pcmWorkletBuffer";

function pcm(values: number[]): Int16Array {
  return Int16Array.from(values);
}

function decoded(values: number[]): number[] {
  return values.map((value) => value / 0x8000);
}

describe("Pcm16Packetizer", () => {
  it.each([
    [48_000, 960],
    [44_100, 882],
    [16_000, 320],
  ])("emits exactly 50 twenty-millisecond packets at %i Hz", (rate, packetSize) => {
    const packetizer = new Pcm16Packetizer(rate);
    const packets: ArrayBuffer[] = [];
    let remaining = rate;

    while (remaining > 0) {
      const frameSamples = Math.min(128, remaining);
      packetizer.push(new Float32Array(frameSamples).fill(0.5), (packet) => {
        packets.push(packet);
      });
      remaining -= frameSamples;
    }

    expect(capturePacketSampleCount(rate)).toBe(packetSize);
    expect(packets).toHaveLength(50);
    expect(packets.every((packet) => packet.byteLength === packetSize * 2)).toBe(true);
  });

  it("does not exceed 50 packets per second at an unusual context rate", () => {
    const rate = 48_001;
    const packetizer = new Pcm16Packetizer(rate);
    let packetCount = 0;
    let remaining = rate;

    while (remaining > 0) {
      const frameSamples = Math.min(128, remaining);
      packetizer.push(new Float32Array(frameSamples), () => {
        packetCount += 1;
      });
      remaining -= frameSamples;
    }

    expect(packetCount).toBeLessThanOrEqual(50);
    expect(capturePacketSampleCount(rate)).toBe(961);
  });
});

describe("BoundedPcm16Queue", () => {
  it("preserves sample order across ring wraparound", () => {
    const queue = new BoundedPcm16Queue(5);
    queue.enqueue(pcm([1_000, 2_000, 3_000]));

    const prefix = new Float32Array(2);
    expect(queue.dequeueInto(prefix)).toBe(2);
    expect(Array.from(prefix)).toEqual(decoded([1_000, 2_000]));

    expect(queue.enqueue(pcm([4_000, 5_000, 6_000, 7_000]))).toBe(0);
    const rest = new Float32Array(5);
    expect(queue.dequeueInto(rest)).toBe(5);
    expect(Array.from(rest)).toEqual(decoded([3_000, 4_000, 5_000, 6_000, 7_000]));
  });

  it("drops the oldest samples on overrun and reports the drop", () => {
    const queue = new BoundedPcm16Queue(4);
    queue.enqueue(pcm([1_000, 2_000, 3_000]));

    expect(queue.enqueue(pcm([4_000, 5_000, 6_000]))).toBe(2);
    const output = new Float32Array(4);
    queue.dequeueInto(output);
    expect(Array.from(output)).toEqual(decoded([3_000, 4_000, 5_000, 6_000]));
  });

  it("retains only the newest tail of an oversized chunk", () => {
    const queue = new BoundedPcm16Queue(4);
    queue.enqueue(pcm([8_000, 9_000]));

    expect(queue.enqueue(pcm([1_000, 2_000, 3_000, 4_000, 5_000, 6_000]))).toBe(4);
    const output = new Float32Array(4);
    queue.dequeueInto(output);
    expect(Array.from(output)).toEqual(decoded([3_000, 4_000, 5_000, 6_000]));
  });

  it("flushes queued audio and writes silence on underrun", () => {
    const queue = new BoundedPcm16Queue(4);
    queue.enqueue(pcm([10_000, 20_000]));
    queue.clear();

    const output = new Float32Array([1, 1, 1]);
    expect(queue.dequeueInto(output)).toBe(0);
    expect(Array.from(output)).toEqual([0, 0, 0]);
    expect(queue.length).toBe(0);
  });

  it("bounds a 48 kHz queue to ten seconds of PCM16", () => {
    expect(playbackBufferSampleCount(48_000)).toBe(480_000);
  });
});
