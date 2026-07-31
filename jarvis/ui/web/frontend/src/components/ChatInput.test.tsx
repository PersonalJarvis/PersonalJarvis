import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test } from "vitest";

import { ChatInput } from "@/components/ChatInput";
import { useEventStore } from "@/store/events";

describe("ChatInput offline/warming placeholder", () => {
  beforeEach(() => {
    useEventStore.setState({
      connected: false,
      wsWarming: true,
      chatThinking: false,
      dictating: false,
    });
  });
  afterEach(() => cleanup());

  test("shows the booting placeholder while warming", () => {
    render(<ChatInput />);
    const box = screen.getByPlaceholderText("Starting…") as HTMLTextAreaElement;
    expect(box.disabled).toBe(true);
  });

  test("shows the offline placeholder when truly offline", () => {
    useEventStore.setState({ connected: false, wsWarming: false });
    render(<ChatInput />);
    expect(screen.getByPlaceholderText("Offline")).toBeTruthy();
  });
});

/*
 * A dictation started by the keyboard shortcut never runs through this
 * component's own start handler, so the "text that was already in the box"
 * snapshot it keeps is a leftover from the last time the microphone BUTTON was
 * used — empty on a session that never used it. Appending onto that leftover
 * replaced whatever the user had typed.
 */
describe("ChatInput dictation commit", () => {
  beforeEach(() => {
    useEventStore.setState({
      connected: true,
      wsWarming: false,
      chatThinking: false,
      dictating: false,
      dictationText: "",
      dictationCommitText: "",
      dictationCommitSeq: 0,
    });
  });
  afterEach(() => cleanup());

  function commit(text: string): void {
    act(() => {
      useEventStore.getState().commitDictation(text);
    });
  }

  /* Found the way the delivery path finds it — the attribute is what tells it
   * whether the fallback sink is on screen at all (lib/dictationTarget.ts). */
  function composer(): HTMLTextAreaElement {
    const box = document.querySelector("textarea[data-jarvis-chat-input]");
    if (!box) throw new Error("the chat composer is not marked as one");
    return box as HTMLTextAreaElement;
  }

  test("appends to what the user already typed", () => {
    render(<ChatInput />);
    const box = composer();
    fireEvent.change(box, { target: { value: "already typed" } });

    commit("and dictated");

    expect(box.value).toBe("already typed and dictated");
  });

  test("does not glue a stale snapshot onto a later dictation", () => {
    render(<ChatInput />);
    const box = composer();

    fireEvent.change(box, { target: { value: "first" } });
    commit("one");
    expect(box.value).toBe("first one");

    // Send clears the box; the next dictation must start from what is there
    // now, not from the snapshot of the previous turn.
    fireEvent.change(box, { target: { value: "" } });
    commit("two");
    expect(box.value).toBe("two");
  });
});
