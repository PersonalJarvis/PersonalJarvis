import { act, cleanup, fireEvent, render as rtlRender, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NEAR_END_PX, VoiceStage, hintFor, isNearEnd, waveformPhase } from "@/components/home/VoiceStage";
import { stateKey } from "@/components/home/JarvisBar";
import { greetingKey } from "@/components/home/Greeting";
import { useHomeStore } from "@/store/home";
import { useEventStore } from "@/store/events";
import type { TranscriptLine } from "@/lib/homeTranscript";

const t = (key: string) => key;

// The bar owns a canvas waveform and two hooks of its own; the stage's
// scrolling is what these tests are about, so it stands in as a marker.
vi.mock("@/components/home/JarvisBar", async () => {
  const actual = await vi.importActual<typeof import("@/components/home/JarvisBar")>(
    "@/components/home/JarvisBar",
  );
  return { ...actual, JarvisBar: () => <div data-testid="jarvis-bar" /> };
});
vi.mock("@/components/agentic/useVoiceCall", () => ({
  useVoiceCall: () => ({ active: false, busy: false, connecting: false, toggleCall: () => {} }),
}));
vi.mock("@/hooks/useVoiceReadiness", () => ({
  useVoiceReadiness: () => ({
    connected: true,
    warming: false,
    ready: true,
    voiceWarming: false,
    bootWarming: false,
  }),
}));
vi.mock("@/hooks/useWakeWord", () => ({
  useWakeWord: () => ({ config: { phrase: "Hey George" }, loading: false, error: null }),
}));

class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = NoopResizeObserver as unknown as typeof ResizeObserver;
}

/** The greeting reads the profile name (a query), so the stage mounts like the app. */
function render() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  return rtlRender(
    <QueryClientProvider client={client}>
      <VoiceStage />
    </QueryClientProvider>,
  );
}

function said(who: "user" | "assistant", text: string, i: number): TranscriptLine {
  return { id: `m${i}`, who, text, ts: i };
}

/** jsdom lays nothing out, so the viewport is told how tall it "is". */
function fakeMetrics(viewport: HTMLElement, scrollTop: number, scrollHeight: number, clientHeight: number) {
  Object.defineProperty(viewport, "scrollHeight", { value: scrollHeight, configurable: true });
  Object.defineProperty(viewport, "clientHeight", { value: clientHeight, configurable: true });
  viewport.scrollTop = scrollTop;
}

function viewport(): HTMLElement {
  const el = document.querySelector("[data-radix-scroll-area-viewport]");
  if (!el) throw new Error("no scroll viewport");
  return el as HTMLElement;
}

afterEach(() => {
  cleanup();
  useHomeStore.getState().resetTranscript();
});

describe("VoiceStage transcript", () => {
  it("renders the WHOLE conversation, not a window onto its last lines", () => {
    // The regression this guards: the lane used to cut to the last 8 entries
    // before rendering, so older turns were absent from the DOM and no amount
    // of scrolling could bring them back.
    const lines = Array.from({ length: 30 }, (_, i) =>
      said(i % 2 === 0 ? "user" : "assistant", `line ${i}`, i),
    );
    act(() => useHomeStore.getState().seedTranscript(lines));
    render();

    expect(screen.getAllByTestId("transcript-line")).toHaveLength(30);
    expect(screen.getByText("line 0")).toBeTruthy();
    expect(screen.getByText("line 29")).toBeTruthy();
    // And it lives in a scrolling viewport, not a clipped lane.
    expect(document.querySelector("[data-radix-scroll-area-viewport]")).toBeTruthy();
  });

  it("offers the way back only once the view has been scrolled off the end", () => {
    act(() =>
      useHomeStore
        .getState()
        .seedTranscript(Array.from({ length: 20 }, (_, i) => said("user", `line ${i}`, i))),
    );
    render();

    expect(screen.queryByTestId("voice-scroll-end")).toBeNull();

    const vp = viewport();
    fakeMetrics(vp, 0, 4000, 500);
    act(() => {
      fireEvent.scroll(vp);
    });
    expect(screen.getByTestId("voice-scroll-end")).toBeTruthy();

    // Back at the end, the button goes away and following resumes.
    fakeMetrics(vp, 3500, 4000, 500);
    act(() => {
      fireEvent.scroll(vp);
    });
    expect(screen.queryByTestId("voice-scroll-end")).toBeNull();
  });

  it("keeps the reader's place when new lines arrive after scrolling up", () => {
    act(() =>
      useHomeStore
        .getState()
        .seedTranscript(Array.from({ length: 20 }, (_, i) => said("user", `line ${i}`, i))),
    );
    render();

    const vp = viewport();
    fakeMetrics(vp, 120, 4000, 500);
    act(() => {
      fireEvent.scroll(vp);
    });

    act(() =>
      useHomeStore.getState().seedTranscript([
        ...Array.from({ length: 20 }, (_, i) => said("user", `line ${i}`, i)),
        said("assistant", "a fresh answer", 99),
      ]),
    );

    expect(screen.getByText("a fresh answer")).toBeTruthy();
    expect(vp.scrollTop).toBe(120);
  });

  it("is one centred column with no lane before anything is said", () => {
    render();
    expect(screen.getByTestId("voice-stage").dataset.empty).toBe("true");
    expect(document.querySelector("[data-radix-scroll-area-viewport]")).toBeNull();
    expect(screen.getByTestId("jarvis-bar")).toBeTruthy();
  });

  it("shows the words still being said under the finished ones", () => {
    act(() => useHomeStore.getState().seedTranscript([said("assistant", "settled", 1)]));
    act(() => useEventStore.setState({ transcription: "still talking", transcriptionFinal: false }));
    render();

    expect(screen.getByTestId("transcript-live").textContent).toContain("still talking");
    act(() => useEventStore.setState({ transcription: "", transcriptionFinal: true }));
  });
});

describe("VoiceStage helpers", () => {
  it("follows new output only while the view is at the end", () => {
    // Exactly at the bottom, and a hair short of it: still following.
    expect(isNearEnd(900, 1000, 100)).toBe(true);
    expect(isNearEnd(900 - NEAR_END_PX, 1000, 100)).toBe(true);
    // Scrolled up to read something: the view stays put.
    expect(isNearEnd(900 - NEAR_END_PX - 1, 1000, 100)).toBe(false);
    expect(isNearEnd(0, 1000, 100)).toBe(false);
    // A conversation shorter than the viewport is always at its end.
    expect(isNearEnd(0, 100, 400)).toBe(true);
  });

  it("maps the voice state onto the waveform phases, idle when offline", () => {
    expect(waveformPhase("listening", false)).toBe("idle");
    expect(waveformPhase("listening", true)).toBe("listening");
    expect(waveformPhase("thinking", true)).toBe("working");
    expect(waveformPhase("speaking", true)).toBe("speaking");
    expect(waveformPhase("error", true)).toBe("error");
    expect(waveformPhase("paused", true)).toBe("idle");
  });

  it("names the wake phrase in the idle hint and the state otherwise", () => {
    const base = { connected: true, warming: false, connecting: false, t };
    expect(hintFor({ ...base, voiceState: "idle", wakePhrase: "Hey Nova" })).toBe("home.hint_idle");
    expect(hintFor({ ...base, voiceState: "idle", wakePhrase: "" })).toBe("home.hint_idle_nowake");
    expect(hintFor({ ...base, voiceState: "listening", wakePhrase: "" })).toBe("home.hint_listening");
    expect(hintFor({ ...base, voiceState: "speaking", wakePhrase: "" })).toBe("home.hint_speaking");
    expect(hintFor({ ...base, connecting: true, voiceState: "idle", wakePhrase: "" })).toBe(
      "home.hint_connecting",
    );
    expect(hintFor({ ...base, connected: false, voiceState: "idle", wakePhrase: "" })).toBe(
      "home.hint_offline",
    );
    expect(
      hintFor({ ...base, connected: false, warming: true, voiceState: "idle", wakePhrase: "" }),
    ).toBe("home.hint_warming");
  });

  it("greets by the hour", () => {
    expect(greetingKey(8)).toBe("home.greeting_morning");
    expect(greetingKey(13)).toBe("home.greeting_afternoon");
    expect(greetingKey(21)).toBe("home.greeting_evening");
  });
});

describe("JarvisBar state word", () => {
  it("says offline before anything else, connecting over a stale state", () => {
    expect(stateKey("speaking", false, false)).toBe("offline");
    expect(stateKey("idle", true, true)).toBe("connecting");
    expect(stateKey("listening", false, true)).toBe("listening");
    expect(stateKey("speaking", false, true)).toBe("speaking");
  });
});
