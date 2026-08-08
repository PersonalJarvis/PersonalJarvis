import { describe, expect, it } from "vitest";

import {
  JitterBufferedPcm16Queue,
  PLAYBACK_PREBUFFER_SECONDS,
  PLAYBACK_PREBUFFER_TIMEOUT_SECONDS,
  playbackPrebufferSampleCount,
} from "./pcmWorkletBuffer";

const RATE = 48_000;
const QUANTUM = 128;

/** One render quantum, as the AudioWorklet hands it to the processor. */
function quantum(): Float32Array {
  return new Float32Array(QUANTUM);
}

/** Constant-amplitude PCM16, so a rendered sample is trivially recognisable. */
function tone(count: number, value = 0x4000): Int16Array {
  return new Int16Array(count).fill(value);
}

function renderQuanta(
  queue: JitterBufferedPcm16Queue,
  count: number,
): Float32Array[] {
  const frames: Float32Array[] = [];
  for (let i = 0; i < count; i++) {
    const out = quantum();
    queue.render(out);
    frames.push(out);
  }
  return frames;
}

describe("playback jitter buffer", () => {
  it("holds the opening burst silent until the reserve is banked", () => {
    const queue = new JitterBufferedPcm16Queue(RATE);
    const reserve = playbackPrebufferSampleCount(RATE);
    expect(reserve).toBe(Math.round(RATE * PLAYBACK_PREBUFFER_SECONDS));

    queue.enqueue(tone(reserve - QUANTUM));
    const held = renderQuanta(queue, 3);
    for (const frame of held) expect(frame.every((s) => s === 0)).toBe(true);
    expect(queue.isPriming).toBe(true);
    // Nothing was consumed while priming — the reserve is delayed, not dropped.
    expect(queue.length).toBe(reserve - QUANTUM);

    queue.enqueue(tone(QUANTUM));
    const out = quantum();
    expect(queue.render(out)).toBe(QUANTUM);
    expect(queue.isPriming).toBe(false);
    expect(out[QUANTUM - 1]).toBeGreaterThan(0);
  });

  it("starts a short reply anyway once the prebuffer timeout elapses", () => {
    const queue = new JitterBufferedPcm16Queue(RATE);
    // A whole burst far below the reserve: waiting for a reserve that will
    // never arrive would swallow the reply entirely.
    queue.enqueue(tone(QUANTUM * 2));
    const timeoutQuanta = Math.ceil(
      (RATE * PLAYBACK_PREBUFFER_TIMEOUT_SECONDS) / QUANTUM,
    );

    let rendered = 0;
    for (let i = 0; i < timeoutQuanta + 2 && rendered === 0; i++) {
      rendered = queue.render(quantum());
    }
    expect(rendered).toBeGreaterThan(0);
  });

  it("streams without added latency once playing", () => {
    const queue = new JitterBufferedPcm16Queue(RATE);
    queue.enqueue(tone(playbackPrebufferSampleCount(RATE) + QUANTUM * 4));
    // Leave priming.
    expect(queue.render(quantum())).toBe(QUANTUM);

    const before = queue.length;
    expect(queue.render(quantum())).toBe(QUANTUM);
    expect(queue.length).toBe(before - QUANTUM);
    expect(queue.isPriming).toBe(false);
  });

  it("re-banks the reserve after a true underrun instead of chopping on", () => {
    const queue = new JitterBufferedPcm16Queue(RATE);
    queue.enqueue(tone(playbackPrebufferSampleCount(RATE)));
    while (queue.length >= QUANTUM) queue.render(quantum());

    // The starved quantum: partial data, then the buffer returns to priming.
    const starved = quantum();
    const rendered = queue.render(starved);
    expect(rendered).toBeLessThan(QUANTUM);
    expect(queue.isPriming).toBe(true);

    // A trickle that stays below the reserve must not be dribbled out.
    queue.enqueue(tone(QUANTUM));
    expect(queue.render(quantum())).toBe(0);
  });

  it("ramps both edges so a gap is not also a click", () => {
    const queue = new JitterBufferedPcm16Queue(RATE);
    const reserve = playbackPrebufferSampleCount(RATE);
    queue.enqueue(tone(reserve));

    const first = quantum();
    queue.render(first);
    // Resume edge: the ramp lifts the first samples out of silence.
    expect(first[0]).toBeGreaterThan(0);
    expect(first[0]).toBeLessThan(first[QUANTUM - 1]);

    while (queue.length >= QUANTUM) queue.render(quantum());
    const starved = quantum();
    const rendered = queue.render(starved);
    if (rendered > 0 && rendered < QUANTUM) {
      // Underrun edge: the tail decays instead of stepping to zero.
      expect(starved[rendered]).toBeGreaterThan(0);
      expect(starved[rendered]).toBeLessThan(starved[rendered - 1]);
    }
  });

  it("returns to priming on flush", () => {
    const queue = new JitterBufferedPcm16Queue(RATE);
    queue.enqueue(tone(playbackPrebufferSampleCount(RATE)));
    expect(queue.render(quantum())).toBe(QUANTUM);

    queue.clear();
    expect(queue.length).toBe(0);
    expect(queue.isPriming).toBe(true);
    queue.enqueue(tone(QUANTUM));
    expect(queue.render(quantum())).toBe(0);
  });
});
