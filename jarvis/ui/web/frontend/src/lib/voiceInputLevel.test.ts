import { beforeEach, describe, expect, it } from "vitest";

import {
  readVoiceInputLevel,
  setBrowserVoiceInputOwnership,
  setVoiceInputLevel,
  voiceInputLevelRef,
} from "./voiceInputLevel";

describe("voice input level", () => {
  beforeEach(() => setBrowserVoiceInputOwnership(false));

  it("clamps measured samples to the normalized range", () => {
    setVoiceInputLevel(1.4);
    expect(voiceInputLevelRef.current).toBe(1);

    setVoiceInputLevel(-0.2);
    expect(voiceInputLevelRef.current).toBe(0);

    setVoiceInputLevel(Number.NaN);
    expect(voiceInputLevelRef.current).toBe(0);
  });

  it("gives an active browser microphone exclusive ownership", () => {
    setVoiceInputLevel(0.8, "native", 10);
    setBrowserVoiceInputOwnership(true);
    setVoiceInputLevel(0.64, "browser", 20);
    setVoiceInputLevel(1, "native", 30);

    expect(readVoiceInputLevel(30)).toBe(0.64);

    setBrowserVoiceInputOwnership(false);
    setVoiceInputLevel(0.3, "native", 40);
    expect(readVoiceInputLevel(40)).toBe(0.3);
  });

  it("expires a stalled producer sample", () => {
    setVoiceInputLevel(0.9, "native", 100);

    expect(readVoiceInputLevel(320)).toBe(0.9);
    expect(readVoiceInputLevel(321)).toBe(0);
  });
});
