/**
 * Component tests for the brief-writer picker.
 *
 * The cases that matter are the ones where a wrong pixel means the wrong thing
 * gets billed, or a user cannot find out why their subscription sits idle:
 * that the CLI is named as the one THIS install connected rather than a vendor
 * this file guessed, that an option which cannot write is visible but
 * unselectable, and that a rejected save shows the server's reason instead of
 * a generic one — the server's reason is the one that names the fix.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { PromptWriterCard } from "@/components/PromptWriterCard";
import { fetchPromptWriter, savePromptWriter } from "@/lib/agenticIdeApi";

vi.mock("@/lib/agenticIdeApi", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/agenticIdeApi")>("@/lib/agenticIdeApi");
  return { ...actual, fetchPromptWriter: vi.fn(), savePromptWriter: vi.fn() };
});

const mockFetch = vi.mocked(fetchPromptWriter);
const mockSave = vi.mocked(savePromptWriter);

const STATE = {
  prompt_writer: "auto",
  options: [
    { id: "auto", label: "Automatic (a connected subscription, else the API model)", connected: true },
    { id: "tool_model", label: "Tool Model (Google Gemini - gemini-3.6-flash)", connected: true },
    { id: "subscription", label: "Any connected coding CLI (never an API key)", connected: true },
    { id: "api", label: "API model (billed per token)", connected: true },
    { id: "antigravity", label: "Antigravity (Google subscription)", connected: true },
  ],
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("PromptWriterCard", () => {
  it("names the Tool Model and the connected CLI as the server reports them", async () => {
    mockFetch.mockResolvedValue(STATE);
    render(<PromptWriterCard />);

    // The label carries the actual model, so the choice is informed rather
    // than a bare "Tool Model" the user has to go and look up.
    expect(
      await screen.findByText(/Tool Model \(Google Gemini - gemini-3\.6-flash\)/),
    ).toBeTruthy();
    // And the CLI is the one this install connected, under its own name.
    expect(screen.getByText("Antigravity (Google subscription)")).toBeTruthy();
  });

  it("persists the choice the user clicks", async () => {
    mockFetch.mockResolvedValue(STATE);
    mockSave.mockResolvedValue({ ...STATE, prompt_writer: "tool_model" });
    render(<PromptWriterCard />);

    fireEvent.click(await screen.findByText(/Tool Model \(Google Gemini/));

    await waitFor(() => expect(mockSave).toHaveBeenCalledWith("tool_model"));
  });

  it("shows an unusable option, but does not let it be chosen", async () => {
    // Hiding the row is what leaves "why is my subscription not an option?"
    // unanswerable from this screen.
    mockFetch.mockResolvedValue({
      prompt_writer: "auto",
      options: [
        { id: "auto", label: "Automatic", connected: true },
        { id: "tool_model", label: "Tool Model (none selected yet)", connected: false },
      ],
    });
    render(<PromptWriterCard />);

    const row = (await screen.findByText(/Tool Model \(none selected yet\)/)).closest(
      "button",
    );
    expect(row).toBeTruthy();
    expect((row as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(row as HTMLButtonElement);
    expect(mockSave).not.toHaveBeenCalled();
  });

  it("surfaces the server's own reason when a choice is refused", async () => {
    mockFetch.mockResolvedValue(STATE);
    mockSave.mockRejectedValue(
      new Error("No usable Tool Model is configured. Pick one in the Tool Model settings"),
    );
    render(<PromptWriterCard />);

    fireEvent.click(await screen.findByText(/Tool Model \(Google Gemini/));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Pick one in the Tool Model settings");
  });

  it("says so when it cannot read the current setting", async () => {
    // An empty picker would read as "there are no options", which is a
    // different and wrong story.
    mockFetch.mockRejectedValue(new Error("offline"));
    render(<PromptWriterCard />);

    expect(await screen.findByRole("alert")).toBeTruthy();
  });
});
