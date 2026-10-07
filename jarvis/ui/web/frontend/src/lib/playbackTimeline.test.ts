import { describe, expect, it } from "vitest";
import { audibleContextTime, DevicePlaybackTimeline, TimedPcmQueue } from "./playbackTimeline";
import { TimedSpeechTracker, type TimedText } from "./timedSpeech";
import { TimedAudioFrame, TimedTextFrame } from "./timedSpeech";
import { readFileSync } from "node:fs";
import path from "node:path";
import { readTimedSpeechPlayback } from "./speechPlayback";

function cue(id: string, text: string, start: number, end: number, charStart = 0): TimedText {
  return { type: "speech_timing", epoch: 0, line_id: id, text,
    char_start: charStart, char_end: text.length, start_ms: start, end_ms: end };
}

describe("source audio to device to transcript", () => {
  it("accepts the Python wire fixture without changing values", () => {
    const [audio, text] = JSON.parse(readFileSync(path.resolve(
      process.cwd(), "../../../../tests/fixtures/voice_playback_contract.json",
    ), "utf8"));
    expect(TimedAudioFrame.parse(audio)).toEqual(audio);
    expect(TimedTextFrame.parse(text)).toEqual(text);
  });
  it.each([16_000, 24_000, 44_100, 48_000])("uses rendered frames at %i Hz and waits for hardware latency", rate => {
    const queue = new TimedPcmQueue(rate);
    const device = new DevicePlaybackTimeline();
    const text = new TimedSpeechTracker();
    const id = `rate-${rate}`;
    text.text(cue(id, "First second", 1000, 2000));
    queue.enqueue(new Int16Array(rate).fill(1000), { startMs: 1000, endMs: 2000 });
    expect(readTimedSpeechPlayback(id)?.chars).toBe(0); // All audio received, none played.
    device.rendered(queue.render(new Float32Array(rate / 2), 5));
    text.played(device.advance(4.99));
    expect(readTimedSpeechPlayback(id)?.chars).toBe(0); // Rendered ahead of the DAC.
    text.played(device.advance(5.05));
    expect(readTimedSpeechPlayback(id)?.chars).toBe(5);
    device.rendered(queue.render(new Float32Array(rate / 2), 5.5));
    text.played(device.advance(6));
    expect(readTimedSpeechPlayback(id)?.chars).toBe(12);
  });

  it.each([200, 600, 1800])("follows a %i ms spoken fragment without a fixed speech rate", duration => {
    const tracker = new TimedSpeechTracker();
    const id = `speed-${duration}`;
    tracker.text(cue(id, "One two", 0, duration));
    tracker.played([{ startMs: 0, endMs: duration / 4 }]);
    expect(readTimedSpeechPlayback(id)?.chars).toBe(3);
    tracker.played([{ startMs: duration / 4, endMs: duration }]);
    expect(readTimedSpeechPlayback(id)?.chars).toBe(7);
  });

  it("does not consume source time while priming, underrunning or waiting between sentences", () => {
    const queue = new TimedPcmQueue(1000);
    queue.enqueue(new Int16Array(50), { startMs: 1000, endMs: 1050 });
    expect(queue.render(new Float32Array(100), 0)).toEqual([]);
    expect(queue.render(new Float32Array(100), 0.1)).toEqual([]);
    expect(queue.render(new Float32Array(100), 0.2)).toEqual([]);
    const played = queue.render(new Float32Array(100), 0.3);
    expect(played[0]).toMatchObject({ startMs: 1000, endMs: 1050, contextEnd: 0.35 });
    expect(queue.render(new Float32Array(100), 30)).toEqual([]);
    queue.enqueue(new Int16Array(200), { startMs: 5000, endMs: 5200 });
    expect(queue.render(new Float32Array(100), 31)[0].startMs).toBe(5000);
  });

  it("freezes in an embedded pause and catches up when a caption arrives late", () => {
    const tracker = new TimedSpeechTracker();
    tracker.text(cue("pause", "One", 0, 200));
    tracker.text(cue("pause", "One two", 1200, 1400, 3));
    tracker.played([{ startMs: 0, endMs: 1000 }]);
    expect(readTimedSpeechPlayback("pause")?.chars).toBe(3);
    tracker.played([{ startMs: 1000, endMs: 1400 }]);
    expect(readTimedSpeechPlayback("pause")?.chars).toBe(7);
    tracker.text(cue("late", "Already heard", 100, 300));
    expect(readTimedSpeechPlayback("late")?.chars).toBe(13);
  });

  it("does not turn an interrupted or dropped tail white", () => {
    const tracker = new TimedSpeechTracker();
    const device = new DevicePlaybackTimeline();
    tracker.text(cue("cut", "First second", 0, 1000));
    device.rendered([{ startMs: 0, endMs: 1000, contextStart: 5, contextEnd: 6 }]);
    tracker.played(device.advance(5.1));
    tracker.clear();
    device.clear();
    tracker.played(device.advance(600));
    expect(readTimedSpeechPlayback("cut")?.chars).toBe(5);
    const queue = new TimedPcmQueue(1000);
    queue.enqueue(new Int16Array(12_000), { startMs: 0, endMs: 12_000 });
    expect(queue.render(new Float32Array(1000), 0)[0].startMs).toBe(2000);
    const dropped = new TimedSpeechTracker();
    dropped.text(cue("dropped", "Not heard", 0, 1000));
    dropped.played([{ startMs: 2000, endMs: 3000 }]);
    expect(readTimedSpeechPlayback("dropped")?.chars).toBe(0);
  });

  it("uses the device timestamp, and measured latency only on older engines", () => {
    const context = { currentTime: 5, baseLatency: 0.01, outputLatency: 0.2,
      getOutputTimestamp: () => ({ contextTime: 4.75, performanceTime: 100 }) };
    expect(audibleContextTime(context as AudioContext)).toBe(4.75);
    context.getOutputTimestamp = () => ({ contextTime: 0, performanceTime: 0 });
    expect(audibleContextTime(context as AudioContext)).toBe(0);
    expect(audibleContextTime({ ...context, getOutputTimestamp: undefined } as unknown as AudioContext)).toBe(4.79);
  });
});
