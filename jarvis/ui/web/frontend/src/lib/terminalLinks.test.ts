import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ILink, ILinkProvider, Terminal } from "@xterm/xterm";

vi.mock("@/lib/openExternal", () => ({
  openExternalUrl: vi.fn(async () => undefined),
}));
vi.mock("@/lib/agenticIdeApi", () => ({
  openTerminalTarget: vi.fn(async () => ({
    opened: true,
    kind: "file",
    path: "src/main.ts",
  })),
}));

import { openTerminalTarget } from "@/lib/agenticIdeApi";
import { openExternalUrl } from "@/lib/openExternal";
import {
  activateTerminalLink,
  createTerminalLinkActivator,
  createTerminalOscLinkHandler,
  findTerminalPathMatches,
  TERMINAL_OSC_LINK_HANDLER,
  TerminalPathLinksAddon,
} from "./terminalLinks";

const openExternal = vi.mocked(openExternalUrl);
const openTarget = vi.mocked(openTerminalTarget);

function click(options: MouseEventInit = {}): MouseEvent {
  return new MouseEvent("mouseup", { button: 0, ...options });
}

describe("terminal links", () => {
  beforeEach(() => {
    openExternal.mockClear();
    openTarget.mockClear();
    openTarget.mockResolvedValue({
      opened: true,
      kind: "file",
      path: "src/main.ts",
    });
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
      {
        start: { x: 1, y: 1 },
        end: { x: 4, y: 1 },
      },
    );

    expect(openExternal).toHaveBeenCalledWith("https://example.com/docs");
  });

  it("refuses non-web protocols and paths without workspace context", () => {
    activateTerminalLink(click({ ctrlKey: true }), "javascript:alert(1)");
    activateTerminalLink(click({ ctrlKey: true }), "file:///private/data");

    expect(openExternal).not.toHaveBeenCalled();
    expect(openTarget).not.toHaveBeenCalled();
  });

  it("opens a detected workspace path on Ctrl-click", () => {
    activateTerminalLink(click({ ctrlKey: true }), "src/main.ts:42:7", {
      workspaceId: "workspace-1",
    });

    expect(openTarget).toHaveBeenCalledWith(
      "workspace-1",
      "src/main.ts:42:7",
    );
  });

  it("allows file OSC-8 links only when a workspace boundary is available", () => {
    const handler = createTerminalOscLinkHandler({
      workspaceId: "workspace-1",
    });
    handler.activate(
      click({ metaKey: true }),
      "file:///workspace/docs",
      {
        start: { x: 1, y: 1 },
        end: { x: 4, y: 1 },
      },
    );

    expect(handler.allowNonHttpProtocols).toBe(true);
    expect(TERMINAL_OSC_LINK_HANDLER.allowNonHttpProtocols).toBe(false);
    expect(openTarget).toHaveBeenCalledWith(
      "workspace-1",
      "file:///workspace/docs",
    );
  });

  it("reports a native path-open failure instead of doing nothing", async () => {
    const onError = vi.fn();
    openTarget.mockRejectedValueOnce(new Error("That workspace path is unavailable."));

    activateTerminalLink(click({ ctrlKey: true }), "docs/missing.md", {
      workspaceId: "workspace-1",
      onError,
    });
    await vi.waitFor(() => expect(onError).toHaveBeenCalledTimes(1));

    expect(onError).toHaveBeenCalledWith(
      "That workspace path is unavailable.",
    );
  });

  it("does not open from a modified non-primary click", () => {
    activateTerminalLink(
      click({ button: 2, ctrlKey: true }),
      "https://example.com",
    );

    expect(openExternal).not.toHaveBeenCalled();
  });

  it("detects common file and folder forms without treating URLs as paths", () => {
    const line =
      'Changed src/components/App.tsx:42:7, "C:\\Program Files\\Project\\notes.md" and /work/project/docs; see https://example.com/docs';

    expect(findTerminalPathMatches(line).map((match) => match.text)).toEqual([
      "src/components/App.tsx:42:7",
      "C:\\Program Files\\Project\\notes.md",
      "/work/project/docs",
    ]);
  });

  it("recognises bare developer filenames and relative folders", () => {
    expect(
      findTerminalPathMatches("README.md ./scripts ../shared assets/icons/").map(
        (match) => match.text,
      ),
    ).toEqual(["README.md", "./scripts", "../shared", "assets/icons/"]);
  });

  it("registers detected paths as xterm links with clickable cell ranges", () => {
    const text = "Open src/main.ts:42";
    const cell = {
      value: "",
      getChars() {
        return this.value;
      },
      getWidth() {
        return 1;
      },
    };
    const line = {
      isWrapped: false,
      length: 80,
      translateToString: () => text,
      getCell(index: number, target: typeof cell) {
        target.value = index < text.length ? text[index] : "";
        return target;
      },
    };
    let provider: ILinkProvider | undefined;
    const terminal = {
      buffer: {
        active: {
          getLine: (row: number) => (row === 0 ? line : undefined),
          getNullCell: () => cell,
        },
      },
      registerLinkProvider(value: ILinkProvider) {
        provider = value;
        return { dispose: vi.fn() };
      },
    } as unknown as Terminal;
    const addon = new TerminalPathLinksAddon(
      createTerminalLinkActivator({ workspaceId: "workspace-1" }),
    );
    addon.activate(terminal);

    let links: ILink[] | undefined;
    provider?.provideLinks(1, (value) => {
      links = value;
    });

    expect(links).toHaveLength(1);
    expect(links?.[0]).toMatchObject({
      text: "src/main.ts:42",
      range: {
        start: { x: 6, y: 1 },
        end: { x: 19, y: 1 },
      },
    });
    links?.[0].activate(click({ ctrlKey: true }), links[0].text);
    expect(openTarget).toHaveBeenCalledWith(
      "workspace-1",
      "src/main.ts:42",
    );
  });
});
