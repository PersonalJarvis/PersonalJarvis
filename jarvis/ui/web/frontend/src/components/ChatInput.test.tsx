import { cleanup, render, screen } from "@testing-library/react";
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
