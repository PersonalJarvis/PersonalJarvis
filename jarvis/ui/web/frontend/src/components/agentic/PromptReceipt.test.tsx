import { act, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  PromptReceipt,
  agoLabel,
  RECEIPT_LEAVE_GRACE_MS,
  RECEIPT_VISIBLE_MS,
} from "./PromptReceipt";

/*
 * The receipt exists because a delivered prompt is routinely invisible: the
 * pane had parked its output, or its emulator had not painted, or its socket
 * was reconnecting, or the CLI redrew its input box out of view. In every one
 * of those the agent really was briefed and the user had no way to know it.
 *
 * So these tests pin the properties that make it PROOF rather than another
 * claim: it shows real text from the prompt (not just the word "sent"), it can
 * produce the whole brief on demand, it does not overstate a prompt that never
 * started, and it does not disappear on its own.
 */

vi.mock("@/lib/agenticIdeApi", () => ({
  fetchLastPrompt: vi.fn(),
}));

const { fetchLastPrompt } = await import("@/lib/agenticIdeApi");
const fetchMock = fetchLastPrompt as unknown as ReturnType<typeof vi.fn>;

const DELIVERED_AT = 1_700_000_000;

const base = {
  terminal: "Mika",
  workspaceId: "ide_abc",
  at: DELIVERED_AT,
  preview: "## Task\nReview the ranking pipeline and report what you find.",
  chars: 2_400,
  submitted: true as boolean | null,
  onDismiss: () => {},
};

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(DELIVERED_AT * 1000 + 2_000);
  fetchMock.mockReset();
  fetchMock.mockResolvedValue({
    name: "Mika",
    text: "## Task\nReview the ranking pipeline and report what you find.\n\n## Key files\n- `@jarvis/rank.py`",
    chars: 2_400,
    at: DELIVERED_AT,
    submitted: true,
    prompts_sent: 1,
  });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("PromptReceipt", () => {
  it("shows actual text from the prompt, not merely that something was sent", () => {
    // The whole complaint this answers is "it said it sent something and I
    // could not see it". A receipt that only says "sent" repeats that.
    render(<PromptReceipt {...base} />);

    expect(screen.getByTestId("prompt-receipt").textContent).toContain(
      "Review the ranking pipeline",
    );
  });

  it("says when it happened, in clock time as well as relative", () => {
    render(<PromptReceipt {...base} />);

    const text = screen.getByTestId("prompt-receipt").textContent ?? "";
    expect(text).toContain("just now");
    // A clock reading is what can be checked against "I asked it right after
    // the call" — a relative label alone cannot be.
    expect(text).toMatch(/\d{1,2}:\d{2}/);
  });

  it("reads back the complete brief when it is opened", async () => {
    render(<PromptReceipt {...base} />);

    fireEvent.click(screen.getByTestId("prompt-receipt-toggle"));

    await waitFor(() => {
      expect(screen.getByTestId("prompt-receipt-body").textContent).toContain(
        "jarvis/rank.py",
      );
    });
    expect(fetchMock).toHaveBeenCalledWith("Mika", "ide_abc");
  });

  it("keeps showing the excerpt when the full prompt cannot be fetched", async () => {
    // Degrades to "you can see the opening" rather than to an empty box: the
    // delivery still happened and the excerpt is still evidence of it.
    fetchMock.mockRejectedValue(new Error("backend restarting"));
    render(<PromptReceipt {...base} />);

    fireEvent.click(screen.getByTestId("prompt-receipt-toggle"));

    await waitFor(() => {
      const body = screen.getByTestId("prompt-receipt-body").textContent ?? "";
      expect(body).toContain("Review the ranking pipeline");
      expect(body).toContain("backend restarting");
    });
  });

  it("does not claim a prompt started when it is still in the input box", () => {
    // The case that most needs saying: this pane looks exactly like a working
    // one, and only the user can push it over the line.
    render(<PromptReceipt {...base} submitted={false} />);

    const text = screen.getByTestId("prompt-receipt").textContent ?? "";
    expect(text).toContain("not started");
    expect(text).toContain("press Enter");
  });

  it("stays honest about an unconfirmed start rather than rounding up", () => {
    render(<PromptReceipt {...base} submitted={null} />);

    expect(screen.getByTestId("prompt-receipt").textContent).toContain(
      "could not be confirmed",
    );
  });

  it("withdraws after five seconds so it stops covering the terminal", () => {
    // The pane is a terminal and its bottom rows are where a CLI answers. A
    // receipt that lingers buys certainty by covering the thing it certifies
    // (maintainer decision 2026-07-28, after seeing it live).
    render(<PromptReceipt {...base} />);
    expect(screen.getByTestId("prompt-receipt")).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(RECEIPT_VISIBLE_MS + 200);
    });

    expect(screen.queryByTestId("prompt-receipt")).toBeNull();
  });

  it("keeps a prompt that never STARTED on screen", () => {
    // Not a confirmation but a warning: that pane looks exactly like a working
    // one, and only the user can push it over the line. Hiding it after five
    // seconds would rebuild the original bug somewhere new.
    render(<PromptReceipt {...base} submitted={false} />);

    act(() => {
      vi.advanceTimersByTime(RECEIPT_VISIBLE_MS * 4);
    });

    const receipt = screen.getByTestId("prompt-receipt");
    expect(receipt).toBeTruthy();
    expect(receipt.dataset.transient).toBe("false");
  });

  it("does not disappear while it is being read", () => {
    render(<PromptReceipt {...base} />);
    fireEvent.click(screen.getByTestId("prompt-receipt-toggle"));

    act(() => {
      vi.advanceTimersByTime(RECEIPT_VISIBLE_MS * 5);
    });

    // A panel that closes under the cursor is worse than one that never opened.
    expect(screen.getByTestId("prompt-receipt-body")).toBeTruthy();
  });

  it("does not disappear under the pointer, and leaves shortly after it goes", () => {
    render(<PromptReceipt {...base} />);
    const receipt = screen.getByTestId("prompt-receipt");
    fireEvent.mouseEnter(receipt);

    act(() => {
      vi.advanceTimersByTime(RECEIPT_VISIBLE_MS * 3);
    });
    expect(screen.getByTestId("prompt-receipt")).toBeTruthy();

    fireEvent.mouseLeave(screen.getByTestId("prompt-receipt"));
    act(() => {
      vi.advanceTimersByTime(RECEIPT_LEAVE_GRACE_MS + 200);
    });

    expect(screen.queryByTestId("prompt-receipt")).toBeNull();
  });

  it("goes away only when the user says so", () => {
    const onDismiss = vi.fn();
    render(<PromptReceipt {...base} onDismiss={onDismiss} />);

    fireEvent.click(screen.getByTestId("prompt-receipt-dismiss"));

    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("is announced to a screen reader, not only drawn", () => {
    render(<PromptReceipt {...base} />);

    const receipt = screen.getByTestId("prompt-receipt");
    expect(receipt.getAttribute("role")).toBe("status");
    expect(receipt.getAttribute("aria-live")).toBe("polite");
  });

  it("names the full length so an excerpt is never read as the whole brief", async () => {
    render(<PromptReceipt {...base} />);

    fireEvent.click(screen.getByTestId("prompt-receipt-toggle"));

    await waitFor(() => {
      expect(screen.getByTestId("prompt-receipt-body").textContent).toMatch(
        /2[,.]400 characters/,
      );
    });
  });
});

describe("agoLabel", () => {
  it("reads as 'just now' only while it really is", () => {
    expect(agoLabel(1_000, 1_000_000 + 3_000)).toBe("just now");
  });

  it("keeps up rather than freezing at 'just now'", () => {
    const at = 1_000;
    expect(agoLabel(at, at * 1000 + 30_000)).toBe("30s ago");
    expect(agoLabel(at, at * 1000 + 4 * 60_000)).toBe("4 min ago");
    expect(agoLabel(at, at * 1000 + 2 * 3_600_000)).toBe("2 hours ago");
  });

  it("never reports a delivery as being in the future", () => {
    // Clock skew between the backend's stamp and the browser is ordinary; a
    // receipt reading "-3s ago" would look like a bug in the proof itself.
    expect(agoLabel(2_000, 1_000_000)).toBe("just now");
  });
});
