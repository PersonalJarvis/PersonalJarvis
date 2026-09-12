import { afterEach, expect, it, vi } from "vitest";
import { setMapFullscreen } from "./mapFullscreen";
import { inDesktopShell } from "./nativeDrop";
vi.mock("./nativeDrop", () => ({ inDesktopShell: vi.fn(() => false) }));
afterEach(() => { vi.unstubAllGlobals(); vi.mocked(inDesktopShell).mockReturnValue(false); });

it("restores the browser when exit was clicked before fullscreen entry completed", async () => {
  let finish!: () => void;
  const host = { fullscreenElement: null as object | null, documentElement: {
    requestFullscreen: () => new Promise<void>((resolve) => { finish = resolve; }),
  }, exitFullscreen: vi.fn(async () => { host.fullscreenElement = null; }) };
  vi.stubGlobal("document", host);
  const entry = setMapFullscreen(true);
  await setMapFullscreen(false);
  host.fullscreenElement = {};
  finish();
  await entry;
  expect(host.exitFullscreen).toHaveBeenCalledOnce();
});

it("serializes native entry and exit", async () => {
  vi.mocked(inDesktopShell).mockReturnValue(true);
  const requests: boolean[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url, init) => {
    requests.push(JSON.parse(init.body).enabled);
    return { ok: true, json: async () => ({ ok: true }) };
  }));
  await Promise.all([setMapFullscreen(true), setMapFullscreen(false)]);
  expect(requests).toEqual([true, false]);
});

it("reports browser fullscreen rejection", async () => {
  vi.stubGlobal("document", { fullscreenElement: null, documentElement: {} });
  await expect(setMapFullscreen(true)).rejects.toThrow("Fullscreen unavailable");
});
