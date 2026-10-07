import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TranscriptLine } from "./VoiceStage";
import { BrowserSpeechFallback } from "@/lib/realtimeAudio";
import { clearSpeechPlayback, readSpeechPlayback } from "@/lib/speechPlayback";

afterEach(() => {
  cleanup();
  clearSpeechPlayback();
  vi.useRealTimers();
});

function player() {
  const utterances: SpeechSynthesisUtterance[] = [];
  const synthesis = {
    cancel: () => undefined,
    speak: (utterance: SpeechSynthesisUtterance) => { utterances.push(utterance); },
  };
  const create = (text: string) => ({ text, volume: 1 }) as SpeechSynthesisUtterance;
  return { controller: new BrowserSpeechFallback(synthesis, create), utterances };
}

function boundary(utterance: SpeechSynthesisUtterance, charIndex: number, name = "word") {
  utterance.onboundary?.({ charIndex, name } as SpeechSynthesisEvent);
}

const event = new Event("speech") as SpeechSynthesisEvent;
const text = "Ready when you are.";

describe("speech engine to visible transcript", () => {
  it("uses playback boundaries, independently of elapsed time and speech rate", () => {
    vi.useFakeTimers();
    const { controller, utterances } = player();
    controller.speak(text, "en", 1, { onFinish: () => undefined });
    render(<TranscriptLine who="Assistant" text={text} user={false} playbackEligible />);
    const utterance = utterances[0];
    expect(screen.getByTestId("spoken-part").textContent).toBe("");
    act(() => vi.advanceTimersByTime(20_000));
    expect(screen.getByTestId("spoken-part").textContent).toBe("");
    act(() => utterance.onstart?.(event));
    act(() => boundary(utterance, 0));
    expect(screen.getByTestId("spoken-part").textContent).toBe("Ready");
    act(() => boundary(utterance, 6));
    expect(screen.getByTestId("spoken-part").textContent).toBe("Ready when");
    act(() => utterance.onpause?.(event));
    act(() => vi.advanceTimersByTime(90_000));
    act(() => boundary(utterance, 11));
    expect(screen.getByTestId("spoken-part").textContent).toBe("Ready when");
    act(() => utterance.onresume?.(event));
    act(() => boundary(utterance, 11));
    expect(screen.getByTestId("spoken-part").textContent).toBe("Ready when you");
    act(() => utterance.onend?.(event));
    expect(screen.getByTestId("spoken-part").textContent).toBe(text);
    expect(screen.getByTestId("unspoken-part").textContent).toBe("");
  });

  it.each(["cancel", "error"])("keeps the unheard tail grey after %s, including stale callbacks", (reason) => {
    const { controller, utterances } = player();
    controller.speak(text, "en", 1, { onFinish: () => undefined });
    const view = render(<TranscriptLine who="Assistant" text={text} user={false} playbackEligible />);
    const utterance = utterances[0];
    act(() => utterance.onstart?.(event));
    act(() => boundary(utterance, 0));
    act(() => {
      if (reason === "cancel") controller.cancel();
      else utterance.onerror?.(event as SpeechSynthesisErrorEvent);
    });
    view.rerender(<TranscriptLine who="Assistant" text={text} user={false} />);
    act(() => {
      boundary(utterance, 15);
      utterance.onend?.(event);
      utterance.onstart?.(event);
    });
    expect(screen.getByTestId("spoken-part").textContent).toBe("Ready");
    expect(screen.getByTestId("unspoken-part").textContent).toBe(" when you are.");
  });

  it("never claims silent output was heard", () => {
    const { controller, utterances } = player();
    controller.speak(text, "en", 0, { onFinish: () => undefined });
    render(<TranscriptLine who="Assistant" text={text} user={false} playbackEligible />);
    act(() => {
      utterances[0].onstart?.(event);
      boundary(utterances[0], 15);
      utterances[0].onend?.(event);
    });
    expect(screen.getByTestId("spoken-part").textContent).toBe("");
    expect(readSpeechPlayback()?.state).toBe("cancelled");
  });

  it("ignores non-word, invalid and out-of-order boundaries", () => {
    const { controller, utterances } = player();
    controller.speak(text, "en", 1, { onFinish: () => undefined });
    render(<TranscriptLine who="Assistant" text={text} user={false} playbackEligible />);
    act(() => {
      boundary(utterances[0], 6); // Not playing yet.
      utterances[0].onstart?.(event);
      boundary(utterances[0], 11, "sentence");
      boundary(utterances[0], NaN);
    });
    expect(screen.getByTestId("spoken-part").textContent).toBe("");
    act(() => {
      boundary(utterances[0], 6);
      boundary(utterances[0], 0);
    });
    expect(screen.getByTestId("spoken-part").textContent).toBe("Ready when");
  });
});
