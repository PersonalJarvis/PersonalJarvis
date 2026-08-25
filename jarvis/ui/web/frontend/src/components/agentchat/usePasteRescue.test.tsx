import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { usePasteRescue } from "@/components/agentchat/usePasteRescue";

/**
 * Ctrl+V where the embedded browser never sends a `paste` event.
 *
 * The behaviour that matters is the NEGATIVE one: in a browser that pastes
 * normally this hook must do nothing at all, or every paste lands twice. So
 * both halves are pinned — the rescue fires when the event is missing, and
 * stays out of the way when it is not.
 */

function Field({ onRescued }: { onRescued?: () => void }) {
  const rescue = usePasteRescue();
  return (
    <textarea
      data-testid="field"
      defaultValue=""
      onKeyDown={(e) => rescue.onKeyDown(e)}
      onPaste={() => {
        rescue.onPaste();
        onRescued?.();
      }}
    />
  );
}

describe("usePasteRescue", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url).includes("/api/clipboard/text") && (init?.method ?? "GET") === "GET") {
        return { ok: true, status: 200, json: async () => ({ text: "from the clipboard" }) } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("puts the clipboard text in when no paste event follows the keystroke", async () => {
    const { getByTestId } = render(<Field />);
    const field = getByTestId("field") as HTMLTextAreaElement;
    field.focus();

    fireEvent.keyDown(field, { key: "v", ctrlKey: true });
    // No `paste` event — this is the embedded browser that swallows it.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });

    expect(fetchMock).toHaveBeenCalled();
    expect(field.value).toBe("from the clipboard");
  });

  it("does nothing at all when the browser delivers the paste itself", async () => {
    const { getByTestId } = render(<Field />);
    const field = getByTestId("field") as HTMLTextAreaElement;
    field.focus();

    fireEvent.keyDown(field, { key: "v", ctrlKey: true });
    fireEvent.paste(field, { clipboardData: { items: [], files: [], types: [] } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });

    // Never read the clipboard, never inserted: a second insertion here is
    // exactly the double paste this timing exists to avoid.
    expect(fetchMock).not.toHaveBeenCalled();
    expect(field.value).toBe("");
  });

  it("ignores every keystroke that is not Ctrl/Cmd+V", async () => {
    const { getByTestId } = render(<Field />);
    const field = getByTestId("field") as HTMLTextAreaElement;

    fireEvent.keyDown(field, { key: "v" });
    fireEvent.keyDown(field, { key: "c", ctrlKey: true });
    fireEvent.keyDown(field, { key: "v", ctrlKey: true, altKey: true });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("leaves an empty clipboard alone rather than reporting a failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ text: "" }) }) as Response),
    );
    const { getByTestId } = render(<Field />);
    const field = getByTestId("field") as HTMLTextAreaElement;
    field.focus();

    fireEvent.keyDown(field, { key: "v", ctrlKey: true });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    expect(field.value).toBe("");
  });
});
