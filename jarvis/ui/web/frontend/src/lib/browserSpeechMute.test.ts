import { expect, it, vi } from "vitest";
import { BrowserSpeechFallback } from "./realtimeAudio";

it("acknowledges muted browser speech once and never resumes the old utterance", () => {
  const utterances: SpeechSynthesisUtterance[] = [];
  const synthesis = { speak: vi.fn(), cancel: vi.fn() };
  const speech = new BrowserSpeechFallback(synthesis, text => {
    const utterance = { text } as SpeechSynthesisUtterance;
    utterances.push(utterance);
    return utterance;
  });
  const oldFinished = vi.fn();
  speech.speak("Old speech", "en", 0.4, { onFinish: oldFinished });
  speech.cancel(true);
  expect(synthesis.cancel).toHaveBeenCalledOnce();
  expect(oldFinished).toHaveBeenCalledExactlyOnceWith("cancelled");
  utterances[0].onend?.({} as SpeechSynthesisEvent);
  expect(oldFinished).toHaveBeenCalledOnce();
  const newFinished = vi.fn();
  speech.speak("Fresh speech", "en", 0.4, { onFinish: newFinished });
  expect(synthesis.speak).toHaveBeenCalledTimes(2);
  utterances[1].onend?.({} as SpeechSynthesisEvent);
  expect(newFinished).toHaveBeenCalledExactlyOnceWith("ended");
});
