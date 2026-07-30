import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PromptHistoryButton } from "./PromptHistoryButton";

vi.mock("@/lib/agenticIdeApi", () => ({
  fetchPromptHistory: vi.fn(),
}));
vi.mock("@/lib/clipboard", () => ({
  robustCopy: vi.fn(),
}));

const { fetchPromptHistory } = await import("@/lib/agenticIdeApi");
const { robustCopy } = await import("@/lib/clipboard");
const fetchMock = fetchPromptHistory as unknown as ReturnType<typeof vi.fn>;
const copyMock = robustCopy as unknown as ReturnType<typeof vi.fn>;

const olderText = "## First task\nReview the terminal transport.";
const newerText = "## Second task\nAdd regression coverage and report the result.";

beforeEach(() => {
  fetchMock.mockReset();
  copyMock.mockReset();
  copyMock.mockResolvedValue(true);
  fetchMock.mockResolvedValue({
    name: "Nova",
    total: 2,
    available: 2,
    complete: true,
    items: [
      {
        id: "newer",
        sequence: 2,
        text: newerText,
        chars: newerText.length,
        at: 1_800_000_100,
        submitted: true,
      },
      {
        id: "older",
        sequence: 1,
        text: olderText,
        chars: olderText.length,
        at: 1_800_000_000,
        submitted: false,
      },
    ],
  });
});

describe("PromptHistoryButton", () => {
  it("puts a small, counted history control in the pane header", () => {
    render(
      <PromptHistoryButton
        terminal="Nova"
        workspaceId="ide_alpha"
        count={7}
        light={false}
      />,
    );

    const button = screen.getByTestId("pane-prompt-history-Nova");
    expect(button.textContent).toBe("7");
    expect(button.getAttribute("aria-label")).toContain("Nova");
  });

  it("opens with every prompt listed and the newest prompt in full", async () => {
    render(
      <PromptHistoryButton
        terminal="Nova"
        workspaceId="ide_alpha"
        count={2}
        light={false}
      />,
    );

    fireEvent.click(screen.getByTestId("pane-prompt-history-Nova"));

    await waitFor(() => expect(screen.getByTestId("prompt-history-dialog")).toBeTruthy());
    await waitFor(() => {
      expect(screen.getByTestId("prompt-history-detail").textContent).toContain(
        "Add regression coverage",
      );
    });
    expect(fetchMock).toHaveBeenCalledWith("Nova", "ide_alpha");
    expect(screen.getByTestId("prompt-history-row-older")).toBeTruthy();
  });

  it("copies the exact prompt selected from the history", async () => {
    render(
      <PromptHistoryButton
        terminal="Nova"
        workspaceId="ide_alpha"
        count={2}
        light={false}
      />,
    );
    fireEvent.click(screen.getByTestId("pane-prompt-history-Nova"));
    await waitFor(() => expect(screen.getByTestId("prompt-history-row-older")).toBeTruthy());

    fireEvent.click(screen.getByTestId("prompt-history-row-older"));
    fireEvent.click(screen.getByTestId("prompt-history-copy"));

    await waitFor(() => expect(copyMock).toHaveBeenCalledWith(olderText));
    expect(screen.getByTestId("prompt-history-copy").textContent).toContain("Copied");
  });

  it("explains the empty history instead of presenting a blank panel", async () => {
    fetchMock.mockResolvedValue({
      name: "Nova",
      total: 0,
      available: 0,
      complete: true,
      items: [],
    });
    render(
      <PromptHistoryButton
        terminal="Nova"
        workspaceId="ide_alpha"
        count={0}
        light
      />,
    );

    fireEvent.click(screen.getByTestId("pane-prompt-history-Nova"));

    await waitFor(() => expect(screen.getByText("No prompts yet")).toBeTruthy());
    expect(screen.getByText(/voice, chat, or the prompt bar/i)).toBeTruthy();
  });
});
