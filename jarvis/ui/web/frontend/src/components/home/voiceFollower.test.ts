import { describe, expect, it } from "vitest";

import { createVoiceFollower } from "@/components/home/voiceFollower";

/** Feed `seconds` of a constant level at `hz` and return the last signals. */
function run(level: number, seconds: number, hz: number, follower = createVoiceFollower()) {
  let out = { level: 0, pulse: 0 };
  for (let i = 0; i < Math.round(seconds * hz); i++) out = follower.step(1 / hz, level);
  return out;
}

describe("createVoiceFollower", () => {
  it("rises quickly when someone starts speaking", () => {
    const out = run(0.5, 0.4, 60);
    expect(out.level).toBeGreaterThan(0.5);
    expect(out.pulse).toBeGreaterThan(0.6);
  });

  it("falls slower than it rises, so pauses between words do not drop the light", () => {
    const follower = createVoiceFollower();
    run(0.6, 1, 60, follower);
    const after = run(0, 0.15, 60, follower);
    expect(after.level).toBeGreaterThan(0.4);
    // The pulse tracks syllables and has already let go.
    expect(after.pulse).toBeLessThan(after.level);
  });

  it("moves the same at 60 Hz and 144 Hz", () => {
    const slow = run(0.4, 0.5, 60);
    const fast = run(0.4, 0.5, 144);
    expect(Math.abs(slow.level - fast.level)).toBeLessThan(0.01);
    expect(Math.abs(slow.pulse - fast.pulse)).toBeLessThan(0.01);
  });

  it("never steps by more than a sliver per frame on choppy input", () => {
    const follower = createVoiceFollower();
    let prev = follower.step(0, 0).level;
    let worst = 0;
    for (let i = 0; i < 600; i++) {
      const raw = i % 4 < 2 ? 0.8 : 0; // a level flipping every two frames
      const { level } = follower.step(1 / 60, raw);
      worst = Math.max(worst, Math.abs(level - prev));
      prev = level;
    }
    expect(worst).toBeLessThan(0.12);
  });

  it("treats a broken sample as silence", () => {
    const out = run(Number.NaN, 0.5, 60);
    expect(out).toEqual({ level: 0, pulse: 0 });
  });
});
