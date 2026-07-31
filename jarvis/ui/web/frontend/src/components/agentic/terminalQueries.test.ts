import { describe, expect, it, vi } from "vitest";
import {
  installQuerySuppression,
  isColourQuery,
  type TerminalQueryParser,
} from "./terminalQueries";

/** Records what was registered, so a test can run the handlers xterm would. */
function fakeParser() {
  const osc = new Map<number, (data: string) => boolean | Promise<boolean>>();
  const csi: { id: { prefix?: string; final: string }; handled: boolean }[] = [];
  const disposed: string[] = [];
  const parser: TerminalQueryParser = {
    registerOscHandler(ident, callback) {
      osc.set(ident, callback);
      return { dispose: () => disposed.push(`osc:${ident}`) };
    },
    registerCsiHandler(id, callback) {
      csi.push({ id, handled: callback([0]) as boolean });
      return { dispose: () => disposed.push(`csi:${id.final}`) };
    },
  };
  return { parser, osc, csi, disposed };
}

describe("installQuerySuppression", () => {
  it("stops this browser from answering the screen-colour queries", () => {
    const { parser, osc } = fakeParser();
    installQuerySuppression(parser);

    // OSC 10 is the foreground, OSC 11 the background — the two a coding CLI
    // asks for on startup. Answered here they arrive a round trip late, in the
    // agent's prompt.
    expect(osc.get(10)?.("?")).toBe(true);
    expect(osc.get(11)?.("?")).toBe(true);
  });

  it("leaves a program that RECOLOURS the terminal working", () => {
    const { parser, osc } = fakeParser();
    installQuerySuppression(parser);

    // Same sequence, no question mark: this sets the background rather than
    // asking for it, and must fall through to xterm's own handler.
    expect(osc.get(11)?.("#ff0000")).toBe(false);
  });

  it("stops this browser from answering the device-attributes query", () => {
    const { parser, csi } = fakeParser();
    installQuerySuppression(parser);

    expect(csi).toHaveLength(1);
    expect(csi[0].id.final).toBe("c");
    // No prefix: the secondary form (ESC [ > c) keeps xterm's own answer, so
    // nothing beyond the one query that misbehaves changes.
    expect(csi[0].id.prefix).toBeUndefined();
    expect(csi[0].handled).toBe(true);
  });

  it("restores xterm's own handling when the pane goes away", () => {
    const { parser, disposed } = fakeParser();

    installQuerySuppression(parser)();

    expect(disposed).toEqual(["osc:10", "osc:11", "csi:c"]);
  });

  it("survives a terminal that was disposed first", () => {
    const parser: TerminalQueryParser = {
      registerOscHandler: () => ({
        dispose: vi.fn(() => {
          throw new Error("terminal is gone");
        }),
      }),
      registerCsiHandler: () => ({ dispose: vi.fn() }),
    };

    expect(() => installQuerySuppression(parser)()).not.toThrow();
  });
});

describe("isColourQuery", () => {
  it("tells a question apart from an instruction", () => {
    expect(isColourQuery("?")).toBe(true);
    expect(isColourQuery("#12141a")).toBe(false);
    expect(isColourQuery("rgb:1212/1414/1a1a")).toBe(false);
  });
});
