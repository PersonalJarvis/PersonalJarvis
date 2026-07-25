import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PromptPreview } from "./PromptPreview";

const base = {
  terminal: "Mika",
  composed:
    "## Task\nReview the ranking pipeline.\n\n## Key files\n- `@jarvis/rank.py` - ranking",
  files: ["jarvis/rank.py"],
  composedBy: "llm" as const,
  onSend: () => {},
  onSendVerbatim: () => {},
  onCancel: () => {},
};

describe("PromptPreview", () => {
  it("shows the composed prompt with its line structure intact", () => {
    render(<PromptPreview {...base} />);
    const body = screen.getByTestId("prompt-preview-body");
    expect(body.textContent).toContain("## Task");
    expect(body.textContent).toContain("Review the ranking pipeline.");
    expect(body.textContent).toContain("## Key files");
  });

  it("names the terminal it would go to", () => {
    render(<PromptPreview {...base} />);
    expect(screen.getByText(/Mika/)).toBeTruthy();
  });

  it("sends the composed prompt", () => {
    const onSend = vi.fn();
    render(<PromptPreview {...base} onSend={onSend} />);
    fireEvent.click(screen.getByTestId("prompt-preview-send"));
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("offers sending the original wording unchanged", () => {
    const onSendVerbatim = vi.fn();
    render(<PromptPreview {...base} onSendVerbatim={onSendVerbatim} />);
    fireEvent.click(screen.getByTestId("prompt-preview-verbatim"));
    expect(onSendVerbatim).toHaveBeenCalledTimes(1);
  });

  it("cancels on Escape so the typed text is never trapped", () => {
    const onCancel = vi.fn();
    render(<PromptPreview {...base} onCancel={onCancel} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("says so when only the deterministic prompt could be built", () => {
    render(<PromptPreview {...base} composedBy="fallback" />);
    expect(screen.getByTestId("prompt-preview-note")).toBeTruthy();
  });

  it("stays quiet about provenance on the happy path", () => {
    render(<PromptPreview {...base} />);
    expect(screen.queryByTestId("prompt-preview-note")).toBeNull();
  });

  it("lists the files the prompt pulled in", () => {
    render(<PromptPreview {...base} />);
    expect(screen.getByText("jarvis/rank.py")).toBeTruthy();
  });
});
