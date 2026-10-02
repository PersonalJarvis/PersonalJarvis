import { act, cleanup, fireEvent, render as rtlRender, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import { VoiceStage, hintFor } from "@/components/home/VoiceStage";
import { NEAR_END_PX, isNearEnd } from "@/hooks/useStickToBottom";
import { greetingKey } from "@/components/home/Greeting";
import { useHomeStore } from "@/store/home";
import { useEventStore } from "@/store/events";
import type { TranscriptLine } from "@/lib/homeTranscript";

const t = (key: string) => key;

// The composer owns hooks of its own (engine, Prompt Mode); the stage's
// scrolling is what these tests are about, so it stands in as a marker.
vi.mock("@/components/home/VoiceComposer", () => ({
  VoiceComposer: ({ hint }: { hint: string }) => <div data-testid="voice-composer">{hint}</div>,
}));
const call = vi.hoisted(() => ({ active: false }));
vi.mock("@/components/agentic/useVoiceCall", () => ({
  useVoiceCall: () => ({ active: call.active, busy: false, connecting: false, toggleCall: () => {} }),
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
  call.active = false;
  useHomeStore.getState().resetTranscript();
  useEventStore.setState({ voiceState: "idle" });
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
    expect(screen.getByTestId("voice-composer")).toBeTruthy();
  });

  it("shows the words still being said under the finished ones", () => {
    act(() => useHomeStore.getState().seedTranscript([said("assistant", "settled", 1)]));
    act(() => useEventStore.setState({ transcription: "still talking", transcriptionFinal: false }));
    render();

    expect(screen.getByTestId("transcript-live").textContent).toContain("still talking");
    act(() => useEventStore.setState({ transcription: "", transcriptionFinal: true }));
  });
});

describe("VoiceStage turn pet", () => {
  function liveSteps(i: number): TranscriptLine {
    return {
      id: `s${i}`,
      who: "steps",
      text: "",
      ts: i,
      live: true,
      startedTs: i,
      lastTs: i,
      steps: [{ id: "t1", kind: "tool", labelKey: "thinking.tool", detail: "web_search", status: "active", startedTs: i }],
    };
  }

  it("draws the pet under the lane while the turn has no live trace", () => {
    call.active = true;
    act(() => useHomeStore.getState().seedTranscript([said("user", "hello", 1)]));
    act(() => useEventStore.setState({ voiceState: "thinking" }));
    render();

    expect(screen.getByTestId("voice-turn-indicator")).toBeTruthy();
  });

  it("draws no second pet while a live trace sits above the answer", () => {
    // The live trace already carries the pet on its status line; the answer
    // streaming in under it must not add another one below.
    call.active = true;
    act(() =>
      useHomeStore.getState().seedTranscript([said("user", "hello", 1), liveSteps(2), said("assistant", "Alles klar", 3)]),
    );
    act(() => useEventStore.setState({ voiceState: "speaking" }));
    render();

    expect(screen.queryByTestId("voice-turn-indicator")).toBeNull();
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
