import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ChatMarkdown } from "./ChatMarkdown";
import { parseToolResult } from "./ToolResultCard";

afterEach(cleanup);

const receipt = { ok: true, final_result: "Found Example Domain", urls: ["https://example.com/", "https://example.com/"], errors: [], steps: 2, seconds: 21.92, usage: { input_tokens: 0 } };
const markdown = (value: unknown) => "```json\n" + JSON.stringify(value) + "\n```";

describe("human-readable tool receipts", () => {
  it("shows the outcome and a single link, keeping every raw field behind a disclosure", () => {
    render(<ChatMarkdown text={markdown(receipt)} />);
    const card = screen.getByTestId("tool-result-card");
    expect(screen.getByText("Found Example Domain")).toBeTruthy();
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(card.querySelector("details")?.open).toBe(false);
    expect(card.querySelector("pre")?.textContent).toBe(JSON.stringify(receipt));
  });

  it("shows failures even when the outer receipt claims success", () => {
    render(<ChatMarkdown text={markdown({ ...receipt, errors: ["Second page failed"], error: "Login required" })} />);
    expect(screen.getByText("Output · Failed")).toBeTruthy();
    expect(screen.getByText("Second page failed")).toBeTruthy();
    expect(screen.getByText("Login required")).toBeTruthy();
  });

  it("does not turn receipt text into HTML or unsafe links", () => {
    const { container } = render(<ChatMarkdown text={markdown({ ...receipt, final_result: '<img src=x onerror="alert(1)">', urls: ["javascript:alert(1)", "file:///private", "https://user:secret@example.com"] })} />);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText('<img src=x onerror="alert(1)">')).toBeTruthy();
  });

  it("retains incomplete streamed output until a complete receipt arrives", () => {
    const text = markdown(receipt);
    const { container, rerender } = render(<ChatMarkdown text={text.slice(0, 40)} />);
    expect(screen.queryByTestId("tool-result-card")).toBeNull();
    expect(container.querySelector("pre")).toBeTruthy();
    rerender(<ChatMarkdown text={text} />);
    expect(screen.getByTestId("tool-result-card")).toBeTruthy();
  });

  it.each(["null", "[]", '{"ok":true}', '{"ok":true,"final_result":"x","urls":[],"errors":[{}]}'])
  ("leaves unrelated or unsupported JSON unchanged: %s", code => {
    expect(parseToolResult(code)).toBeNull();
  });
});
