import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/openExternal", () => ({
  openExternalUrl: vi.fn(async () => undefined),
}));

import { openExternalUrl } from "@/lib/openExternal";
import {
  activateTerminalLink,
  TERMINAL_OSC_LINK_HANDLER,
} from "./terminalLinks";

const openExternal = vi.mocked(openExternalUrl);

function click(options: MouseEventInit = {}): MouseEvent {
  return new MouseEvent("mouseup", { button: 0, ...options });
}

describe("terminal links", () => {
  beforeEach(() => {
    openExternal.mockClear();
  });

  it("leaves ordinary clicks available for selecting linked terminal text", () => {
    activateTerminalLink(click(), "https://startups.microsoft.com/");

    expect(openExternal).not.toHaveBeenCalled();
  });

  it("opens an HTTP link on an explicit Ctrl-click", () => {
    activateTerminalLink(
      click({ ctrlKey: true }),
      "https://startups.microsoft.com/",
    );

    expect(openExternal).toHaveBeenCalledWith(
      "https://startups.microsoft.com/",
    );
  });

  it("supports the macOS Cmd-click convention for OSC-8 links", () => {
    TERMINAL_OSC_LINK_HANDLER.activate(
      click({ metaKey: true }),
      "https://example.com/docs",
    );

    expect(openExternal).toHaveBeenCalledWith("https://example.com/docs");
  });

  it("refuses non-web protocols even with a modifier", () => {
    activateTerminalLink(click({ ctrlKey: true }), "javascript:alert(1)");
    activateTerminalLink(click({ ctrlKey: true }), "file:///private/data");

    expect(openExternal).not.toHaveBeenCalled();
  });

  it("does not open from a modified non-primary click", () => {
    activateTerminalLink(
      click({ button: 2, ctrlKey: true }),
      "https://example.com",
    );

    expect(openExternal).not.toHaveBeenCalled();
  });
});
