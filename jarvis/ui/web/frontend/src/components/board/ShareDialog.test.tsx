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
import { shareToX } from "@/lib/shareImage";
import { toBlob } from "html-to-image";

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

function installImageClipboard(): ReturnType<typeof vi.fn> {
  const write = vi.fn(async () => {});
  (globalThis as unknown as { ClipboardItem: unknown }).ClipboardItem = class {
    constructor(public items: unknown) {}
  };
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { write },
  });
  return write;
}

function installComposerWindow() {
  const handle = {
    opener: globalThis,
    location: { replace: vi.fn() },
    close: vi.fn(),
  } as unknown as Window;
  const replace = vi.mocked(handle.location.replace);
  replace.mockImplementation(() => {
    expect(handle.opener).toBeNull();
  });
  const open = vi.spyOn(globalThis, "open").mockReturnValue(handle);
  return { handle, open, replace };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  (globalThis as unknown as { ClipboardItem: unknown }).ClipboardItem = undefined;
  for (const property of ["clipboard", "canShare", "share"] as const) {
    Object.defineProperty(navigator, property, {
      configurable: true,
      value: undefined,
    });
  }
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
    const write = installImageClipboard();

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

  it("opens the X composer before a pending image render resolves", async () => {
    installImageClipboard();
    let finishRender: ((blob: Blob) => void) | undefined;
    vi.mocked(toBlob).mockReturnValueOnce(
      new Promise<Blob>((resolve) => {
        finishRender = resolve;
      }),
    );
    const { handle, open, replace } = installComposerWindow();

    render(<ShareDialog open onOpenChange={() => {}} stats={STATS} />);
    fireEvent.click(screen.getByTestId("share-x"));

    expect(open).toHaveBeenCalledWith("", "_blank");
    expect(handle.opener).toBeNull();
    expect(String(replace.mock.calls[0][0])).toContain("twitter.com/intent/tweet");
    expect(screen.getByTestId("share-status").textContent).toContain("Generating");

    finishRender?.(new Blob(["png"], { type: "image/png" }));
    await waitFor(() => {
      expect(screen.getByTestId("share-status").textContent).toContain(
        "Image copied",
      );
    });
  });

  it("reports when X opens but the clipboard image is unavailable", async () => {
    const { open } = installComposerWindow();

    render(<ShareDialog open onOpenChange={() => {}} stats={STATS} />);
    fireEvent.click(screen.getByTestId("share-x"));

    await waitFor(() => {
      expect(screen.getByTestId("share-status").textContent).toContain(
        "image could not be copied",
      );
    });
    expect(open).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("share-status").textContent).not.toContain(
      "Image copied",
    );
  });

  it("starts native Web Share synchronously for an existing Blob", async () => {
    let finishShare: (() => void) | undefined;
    const share = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finishShare = resolve;
        }),
    );
    Object.defineProperty(navigator, "canShare", {
      configurable: true,
      value: vi.fn(() => true),
    });
    Object.defineProperty(navigator, "share", {
      configurable: true,
      value: share,
    });
    const open = vi.spyOn(globalThis, "open");

    const result = shareToX(new Blob(["png"], { type: "image/png" }), "stats");

    expect(share).toHaveBeenCalledTimes(1);
    expect(open).not.toHaveBeenCalled();
    finishShare?.();
    await expect(result).resolves.toBe("shared");
  });

  it("reports blocked only when the synchronous blank-window handle is null", async () => {
    installImageClipboard();
    const open = vi.spyOn(globalThis, "open").mockReturnValue(null);

    const result = shareToX(
      Promise.resolve(new Blob(["png"], { type: "image/png" })),
      "stats",
    );

    expect(open).toHaveBeenCalledWith("", "_blank");
    await expect(result).resolves.toBe("blocked");
  });
});
