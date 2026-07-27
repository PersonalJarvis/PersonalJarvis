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

/*
 * The dropped-file panel answers one question the prompt text cannot: was the
 * picture actually LOOKED at? On an install whose providers are all text-only
 * it was not, and the user has to be able to see that before the agent starts
 * rather than after it asks what the screenshot showed.
 */
describe("PromptPreview attachments", () => {
  const described = {
    name: "shot.png",
    reference: "@.jarvis/drops/shot.png",
    kind: "image" as const,
    detail: "A login dialog whose submit button overflows its container.",
    described_by: "vision" as const,
    note: "",
  };

  const undescribed = {
    name: "photo.png",
    reference: "@photo.png",
    kind: "image" as const,
    detail: "",
    described_by: "none" as const,
    note: "No provider that can see images is reachable.",
  };

  it("shows nothing at all when nothing was dropped", () => {
    render(<PromptPreview {...base} />);
    expect(screen.queryByTestId("prompt-preview-attachments")).toBeNull();
  });

  it("shows what was read out of a dropped screenshot", () => {
    render(<PromptPreview {...base} attachments={[described]} />);
    const panel = screen.getByTestId("prompt-preview-attachments");
    expect(panel.textContent).toContain("shot.png");
    expect(panel.textContent).toContain("submit button overflows");
    expect(panel.textContent).toContain("described");
  });

  it("says plainly when the image could NOT be described", () => {
    render(<PromptPreview {...base} attachments={[undescribed]} />);
    const panel = screen.getByTestId("prompt-preview-attachments");
    expect(panel.textContent).toContain("attached as a file only");
    // The reason travels with it — "it did not work" without a why is not an
    // answer the user can act on.
    expect(panel.textContent).toContain("No provider that can see images");
  });

  it("distinguishes an extracted document from a described image", () => {
    render(
      <PromptPreview
        {...base}
        attachments={[
          {
            name: "spec.md",
            reference: '"spec.md"',
            kind: "text" as const,
            detail: "The endpoint must return 202.",
            described_by: "extraction" as const,
            note: "",
          },
        ]}
      />,
    );
    const panel = screen.getByTestId("prompt-preview-attachments");
    expect(panel.textContent).toContain("text read");
    expect(panel.textContent).toContain("must return 202");
  });

  it("lists every dropped file rather than only the first", () => {
    render(<PromptPreview {...base} attachments={[described, undescribed]} />);
    const panel = screen.getByTestId("prompt-preview-attachments");
    expect(panel.textContent).toContain("shot.png");
    expect(panel.textContent).toContain("photo.png");
  });
});
