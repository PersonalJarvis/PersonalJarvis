/**
 * Tests for the Board ShareDialog.
 *
 * Behaviour anchors:
 *   1. Open dialog renders the card (with the repo URL + hero number) and the
 *      three actions.
 *   2. Copy Image puts a PNG on the clipboard and surfaces a status line.
 *   3. The X handle persists to localStorage and shows up on the card.
 *
 * html-to-image is mocked so no real canvas work happens in jsdom.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import { ShareDialog } from "@/components/board/ShareDialog";

vi.mock("html-to-image", () => ({
  toBlob: vi.fn(async () => new Blob(["png"], { type: "image/png" })),
}));

const STATS = {
  userWords: 10874,
  jarvisWords: 18712,
  conversationHours: 27.9,
  sessionCount: 888,
  longestStreak: 23,
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  try {
    localStorage.clear();
  } catch {
    /* ignore */
  }
});

describe("ShareDialog", () => {
  it("renders the card, the repo link and three actions when open", () => {
    render(<ShareDialog open onOpenChange={() => {}} stats={STATS} />);
    expect(screen.getByTestId("share-dialog")).toBeDefined();
    expect(screen.getByTestId("share-copy")).toBeDefined();
    expect(screen.getByTestId("share-save")).toBeDefined();
    expect(screen.getByTestId("share-x")).toBeDefined();
    // The repo URL is baked into the card (preview + capture copies).
    expect(
      screen.getAllByText(/github\.com\/PersonalJarvis\/PersonalJarvis/).length,
    ).toBeGreaterThan(0);
    // Hero number rendered (locale-agnostic — matches whatever separator
    // toLocaleString uses in the test environment).
    const hero = (10874).toLocaleString();
    expect(screen.getAllByText(hero).length).toBeGreaterThan(0);
  });

  it("Copy Image writes a PNG to the clipboard and shows a status", async () => {
    const write = vi.fn(async () => {});
    // jsdom lacks ClipboardItem — provide a minimal stand-in.
    (globalThis as unknown as { ClipboardItem: unknown }).ClipboardItem = class {
      constructor(public items: unknown) {}
    };
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { write },
    });

    render(<ShareDialog open onOpenChange={() => {}} stats={STATS} />);
    fireEvent.click(screen.getByTestId("share-copy"));

    await waitFor(() => expect(write).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.getByTestId("share-status")).toBeDefined(),
    );
  });

  it("persists the X handle to localStorage and renders it on the card", () => {
    render(<ShareDialog open onOpenChange={() => {}} stats={STATS} />);
    const input = screen.getByTestId("share-handle-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "@ruben" } });
    expect(localStorage.getItem("board.share.handle")).toBe("ruben");
    expect(screen.getAllByText(/@ruben/).length).toBeGreaterThan(0);
  });
});
