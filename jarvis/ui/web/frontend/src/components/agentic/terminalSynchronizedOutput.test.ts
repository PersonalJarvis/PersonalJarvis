import { afterEach, describe, expect, it, vi } from "vitest";
import { Terminal } from "@xterm/xterm";
import { installSynchronizedOutput, SYNCHRONIZED_OUTPUT_TIMEOUT_MS } from "./terminalSynchronizedOutput";

function harness() {
  const csi = new Map<string, (params: number[]) => boolean>();
  let reset = () => false;
  const render = vi.fn();
  const refresh = vi.fn();
  const service = { _renderRows: render };
  const term = {
    rows: 24, refresh, _core: { _renderService: service },
    parser: {
      registerCsiHandler(id: { final: string }, callback: (params: number[]) => boolean) {
        csi.set(id.final, callback);
        return { dispose: () => csi.delete(id.final) };
      },
      registerEscHandler(_id: unknown, callback: () => boolean) {
        reset = callback;
        return { dispose: () => { reset = () => false; } };
      },
    },
  } as unknown as Terminal;
  const bridge = installSynchronizedOutput(term);
  return { bridge, render, refresh, service, csi, reset: () => reset() };
}

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe("synchronized terminal output", () => {
  it("keeps the last frame while several output chunks build the next one", () => {
    const h = harness();
    h.service._renderRows(0, 23);
    h.render.mockClear();
    expect(h.csi.get("h")!([2026, 25])).toBe(false);
    h.service._renderRows(0, 12);
    h.service._renderRows(13, 23);
    expect(h.render).not.toHaveBeenCalled();
    expect(h.csi.get("l")!([2026, 25])).toBe(false);
    expect(h.refresh).toHaveBeenCalledExactlyOnceWith(0, 23);
    h.service._renderRows(0, 23);
    expect(h.render).toHaveBeenCalledExactlyOnceWith(0, 23);
    h.bridge.dispose();
  });

  it("leaves ordinary output and unrelated private modes unchanged", () => {
    const h = harness();
    h.csi.get("h")!([25]);
    h.service._renderRows(3, 7);
    h.csi.get("l")!([2026]);
    expect(h.render).toHaveBeenCalledExactlyOnceWith(3, 7);
    expect(h.refresh).not.toHaveBeenCalled();
    h.bridge.dispose();
  });

  it("bounds a missing end marker even when begin is repeated", () => {
    vi.useFakeTimers();
    const h = harness();
    h.csi.get("h")!([2026]);
    vi.advanceTimersByTime(SYNCHRONIZED_OUTPUT_TIMEOUT_MS - 1);
    h.csi.get("h")!([2026]);
    h.service._renderRows(0, 23);
    expect(h.render).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    h.service._renderRows(0, 23);
    expect(h.render).toHaveBeenCalledOnce();
    expect(h.refresh).toHaveBeenCalledOnce();
    h.bridge.dispose();
  });

  it("starts a fresh deadline for the next frame and releases on reset", () => {
    vi.useFakeTimers();
    const h = harness();
    h.csi.get("h")!([2026]);
    vi.advanceTimersByTime(900);
    h.csi.get("l")!([2026]);
    h.csi.get("h")!([2026]);
    vi.advanceTimersByTime(101);
    h.service._renderRows(0, 23);
    expect(h.render).not.toHaveBeenCalled();
    h.bridge.reset();
    h.service._renderRows(0, 23);
    expect(h.render).toHaveBeenCalledOnce();
    h.csi.get("h")!([2026]);
    expect(h.reset()).toBe(false);
    h.service._renderRows(0, 23);
    expect(h.render).toHaveBeenCalledTimes(2);
    h.bridge.dispose();
  });

  it("restores rendering and removes timers and parser handlers on disposal", () => {
    vi.useFakeTimers();
    const h = harness();
    h.csi.get("h")!([2026]);
    h.bridge.dispose();
    h.bridge.dispose();
    expect(h.service._renderRows).toBe(h.render);
    expect(h.csi.size).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
    expect(h.refresh).not.toHaveBeenCalled();
  });

  it("degrades to ordinary rendering when xterm's private seam is unavailable", () => {
    const bridge = installSynchronizedOutput({} as Terminal);
    expect(() => { bridge.reset(); bridge.dispose(); }).not.toThrow();
  });

  it("honors Codex frame markers split across writes through the real xterm parser", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    vi.stubGlobal("matchMedia", () => ({ matches: false, addListener() {}, removeListener() {} }));
    const host = document.createElement("div");
    document.body.append(host);
    const term = new Terminal({ cols: 80, rows: 24, allowProposedApi: true });
    term.open(host);
    const service = (term as unknown as { _core: { _renderService: { _renderRows(start: number, end: number): void } } })._core._renderService;
    const render = vi.spyOn(service, "_renderRows");
    let bridge: ReturnType<typeof installSynchronizedOutput> | undefined;
    const write = (text: string) => new Promise<void>((resolve) => term.write(text, resolve));
    try {
      // The installed xterm ignores the frame marker without the bridge:
      // the same split repaint can present its incomplete first half.
      await write("\x1b[?2026h\x1b[1;1H\x1b[Jbaseline partial");
      service._renderRows(0, 23);
      expect(render).toHaveBeenCalled();
      await write("\x1b[?2026l");
      render.mockClear();
      bridge = installSynchronizedOutput(term);
      await write("\x1b[?2026h\x1b[?25l\x1b[1;1H\x1b[Jpartial");
      // Discard a baseline repaint that ran before the async parser consumed
      // the new begin marker; every presentation from this point must wait.
      render.mockClear();
      service._renderRows(0, 23);
      expect(render).not.toHaveBeenCalled();
      await write(" frame\x1b[?20");
      service._renderRows(0, 23);
      expect(render).not.toHaveBeenCalled();
      await write("26l\x1b[?25h");
      service._renderRows(0, 23);
      expect(render).toHaveBeenCalled();
      expect(term.buffer.active.getLine(0)?.translateToString(true)).toBe("partial frame");
    } finally {
      bridge?.dispose();
      term.dispose();
      host.remove();
      vi.unstubAllGlobals();
    }
  });
});
