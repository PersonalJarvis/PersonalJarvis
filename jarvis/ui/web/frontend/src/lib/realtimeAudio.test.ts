import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  BrowserSpeechFallback,
  browserRealtimeSupportIssue,
  buildAudioSocketUrl,
  ensureAudioSocketToken,
  StreamingPcm16Resampler,
} from "./realtimeAudio";

describe("realtime audio client", () => {
  beforeEach(() => {
    vi.stubGlobal("window", {
      location: { protocol: "https:", host: "app.example", hostname: "app.example" },
      __JARVIS_TOKEN: "tok",
    });
  });

  afterEach(() => vi.unstubAllGlobals());

  it("builds a wss /ws/audio url with the token", () => {
    expect(buildAudioSocketUrl()).toBe("wss://app.example/ws/audio?token=tok");
  });

  it("reuses an injected desktop token without fetching another", async () => {
    const fetchToken = vi.fn();
    vi.stubGlobal("fetch", fetchToken);

    await expect(ensureAudioSocketToken()).resolves.toBe("tok");
    expect(fetchToken).not.toHaveBeenCalled();
  });

  it("fetches and registers a canonical token for a remote browser", async () => {
    (window as Window & { __JARVIS_TOKEN?: string }).__JARVIS_TOKEN = undefined;
    const fetchToken = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue({ token: "remote-token" }),
    });
    vi.stubGlobal("fetch", fetchToken);

    await expect(ensureAudioSocketToken()).resolves.toBe("remote-token");

    expect(fetchToken).toHaveBeenCalledWith("/api/missions/auth/token", {
      cache: "no-store",
      credentials: "same-origin",
    });
    expect(buildAudioSocketUrl()).toBe(
      "wss://app.example/ws/audio?token=remote-token",
    );
  });

  it("fails closed when localhost cannot obtain a token", async () => {
    vi.stubGlobal("window", {
      location: {
        protocol: "http:",
        host: "localhost:47821",
        hostname: "localhost",
      },
    });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("backend warming")));

    await expect(ensureAudioSocketToken()).rejects.toThrow("backend warming");
    expect(buildAudioSocketUrl()).toBe("ws://localhost:47821/ws/audio");
  });

  it("fails closed when a remote browser cannot obtain a token", async () => {
    (window as Window & { __JARVIS_TOKEN?: string }).__JARVIS_TOKEN = undefined;
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("token unavailable")));

    await expect(ensureAudioSocketToken()).rejects.toThrow("token unavailable");
  });

  it("rejects browser microphone capture outside a secure context", () => {
    vi.stubGlobal("window", { isSecureContext: false });

    expect(browserRealtimeSupportIssue()).toBe("secure_context");
  });

  it("reports missing microphone and AudioWorklet capabilities separately", () => {
    vi.stubGlobal("window", { isSecureContext: true });
    vi.stubGlobal("navigator", {});
    expect(browserRealtimeSupportIssue()).toBe("microphone_unavailable");

    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: vi.fn() } });
    vi.stubGlobal("AudioContext", undefined);
    vi.stubGlobal("AudioWorkletNode", undefined);
    expect(browserRealtimeSupportIssue()).toBe("audio_worklet_unavailable");
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

  it("speaks a server-approved fallback with language and volume", () => {
    const utterances: SpeechSynthesisUtterance[] = [];
    const synthesis = {
      cancel: vi.fn(),
      speak: vi.fn((utterance: SpeechSynthesisUtterance) => utterances.push(utterance)),
    };
    const createUtterance = (text: string) =>
      ({ text, lang: "", volume: 1, onstart: null, onend: null, onerror: null }) as unknown as
      SpeechSynthesisUtterance;
    const controller = new BrowserSpeechFallback(synthesis, createUtterance);
    const started = vi.fn();
    const finished = vi.fn();

    expect(controller.speak("Hola", "es-ES", 0.4, { onStart: started, onFinish: finished })).toBe(
      true,
    );
    expect(utterances[0].lang).toBe("es-ES");
    expect(utterances[0].volume).toBe(0.4);
    utterances[0].onstart?.(new Event("start") as SpeechSynthesisEvent);
    utterances[0].onend?.(new Event("end") as SpeechSynthesisEvent);
    expect(started).toHaveBeenCalledOnce();
    expect(finished).toHaveBeenCalledWith("ended");
  });

  it("fails honestly when the browser has no speech service", () => {
    const finished = vi.fn();
    const controller = new BrowserSpeechFallback(null, null);

    expect(controller.speak("Answer", "en-US", 1, { onFinish: finished })).toBe(false);
    expect(finished).toHaveBeenCalledWith("unavailable");
  });

  it("ignores a stale completion after a newer fallback starts", () => {
    const utterances: SpeechSynthesisUtterance[] = [];
    const synthesis = {
      cancel: vi.fn(),
      speak: (utterance: SpeechSynthesisUtterance) => utterances.push(utterance),
    };
    const createUtterance = (text: string) =>
      ({ text, lang: "", volume: 1, onstart: null, onend: null, onerror: null }) as unknown as
      SpeechSynthesisUtterance;
    const controller = new BrowserSpeechFallback(synthesis, createUtterance);
    const first = vi.fn();
    const second = vi.fn();

    controller.speak("First", "en-US", 1, { onFinish: first });
    controller.speak("Second", "en-US", 1, { onFinish: second });
    utterances[0].onend?.(new Event("end") as SpeechSynthesisEvent);
    utterances[1].onend?.(new Event("end") as SpeechSynthesisEvent);

    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledWith("ended");
  });
});
