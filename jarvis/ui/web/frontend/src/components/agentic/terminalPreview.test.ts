import { describe, expect, it, vi } from "vitest";
import {
  clearTerminalPreview,
  publishTerminalPreview,
  readTerminalPreview,
  registerTerminalPreviewSource,
  subscribeTerminalPreview,
  terminalPreviewRequested,
} from "./terminalPreview";

describe("terminal preview", () => {
  it("reads the live xterm screen rather than raw terminal output", () => {
    const lines = ["old scrollback", "  prompt", "", "tests passed", "", ""];
    const preview = readTerminalPreview(
      {
        rows: 5,
        buffer: {
          active: {
            type: "normal",
            baseY: 1,
            getLine: (line) => ({ translateToString: () => lines[line] ?? "" }),
          },
        },
      },
      3,
    );

    expect(preview).toEqual(["prompt", "", "tests passed"]);
  });

  it("rejoins xterm wraps and removes visible escaped control debris", () => {
    const lines = [
      { text: "npm run bu", isWrapped: false },
      { text: "ild", isWrapped: true },
      { text: String.raw`Befud\ndling...`, isWrapped: false },
      { text: String.raw`\x1b[32mtests passed\x1b[0m`, isWrapped: false },
    ];
    const preview = readTerminalPreview(
      {
        rows: lines.length,
        buffer: {
          active: {
            type: "alternate",
            baseY: 0,
            getLine: (line) => ({
              isWrapped: lines[line]?.isWrapped,
              translateToString: () => lines[line]?.text ?? "",
            }),
          },
        },
      },
      4,
    );

    expect(preview).toEqual(["npm run build", "Befuddling...", "tests passed"]);
  });

  it("asks the real terminal for a snapshot when a card starts watching", () => {
    const request = vi.fn();
    const notify = vi.fn();
    const stopSource = registerTerminalPreviewSource("T1", request);
    expect(terminalPreviewRequested("T1")).toBe(false);
    expect(request).not.toHaveBeenCalled();

    const unsubscribe = subscribeTerminalPreview("T1", notify);
    expect(terminalPreviewRequested("T1")).toBe(true);
    expect(request).toHaveBeenCalledOnce();

    publishTerminalPreview("T1", ["ready"]);
    expect(notify).toHaveBeenCalledOnce();

    unsubscribe();
    expect(terminalPreviewRequested("T1")).toBe(false);
    stopSource();
    clearTerminalPreview("T1");
  });
});
