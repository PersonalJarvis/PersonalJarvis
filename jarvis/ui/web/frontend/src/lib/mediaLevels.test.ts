import { afterEach, expect, it } from "vitest";
import { MediaActivity } from "./mediaLevels";
import { setBrowserPlaybackActive, setBrowserVoiceOutputOwnership } from "./voiceOutputLevel";
import { useEventStore } from "@/store/events";

afterEach(() => {
  setBrowserVoiceOutputOwnership(false);
  useEventStore.getState().setVoice("idle");
});

it("keeps audible playback above delayed thinking and listening messages", () => {
  setBrowserVoiceOutputOwnership(true);
  setBrowserPlaybackActive(true);
  useEventStore.getState().setVoice("thinking");
  expect(useEventStore.getState().voiceState).toBe("speaking");
  useEventStore.getState().setVoice("listening");
  expect(useEventStore.getState().voiceState).toBe("speaking");
  setBrowserPlaybackActive(false);
  useEventStore.getState().setVoice("thinking");
  expect(useEventStore.getState().voiceState).toBe("thinking");
});

it("never hides errors, hangups or connection loss behind playback", () => {
  setBrowserVoiceOutputOwnership(true);
  setBrowserPlaybackActive(true);
  for (const state of ["error", "idle", "connecting"] as const) {
    useEventStore.getState().setVoice(state);
    expect(useEventStore.getState().voiceState).toBe(state);
  }
  setBrowserVoiceOutputOwnership(false);
  useEventStore.getState().setVoice("listening");
  expect(useEventStore.getState().voiceState).toBe("listening");
});

it("does not turn word gaps into repeated speaking/thinking changes", () => {
  const voice = new MediaActivity(0.004, 21);
  expect(voice.update(0.1)).toBe(true);
  for (let i = 0; i < 20; i++) expect(voice.update(0)).toBe(false);
  expect(voice.active).toBe(true);
  expect(voice.update(0.1)).toBe(false);
  for (let i = 0; i < 20; i++) voice.update(0);
  expect(voice.update(0)).toBe(true);
  expect(voice.active).toBe(false);
});
