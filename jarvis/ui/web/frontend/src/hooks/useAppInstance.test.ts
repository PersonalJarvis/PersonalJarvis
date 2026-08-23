import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchAppInstance, resetAppInstanceCache } from "./useAppInstance";

function healthResponding(body: unknown, ok = true) {
  return vi.fn(async () => ({ ok, json: async () => body })) as unknown as typeof fetch;
}

describe("fetchAppInstance", () => {
  beforeEach(() => resetAppInstanceCache());
  afterEach(() => vi.unstubAllGlobals());

  it("reads the instance from /api/health and flags dev", async () => {
    vi.stubGlobal("fetch", healthResponding({ ok: true, instance: "dev" }));
    expect(await fetchAppInstance()).toEqual({ name: "dev", isDev: true });
  });

  it("treats a missing field as the default app (older backend)", async () => {
    vi.stubGlobal("fetch", healthResponding({ ok: true }));
    expect(await fetchAppInstance()).toEqual({ name: "default", isDev: false });
  });

  it("caches the answer — the instance never changes while a process runs", async () => {
    const f = healthResponding({ ok: true, instance: "dev" });
    vi.stubGlobal("fetch", f);
    await fetchAppInstance();
    await fetchAppInstance();
    expect(f).toHaveBeenCalledTimes(1);
  });

  it("stays unknown (null) when the backend cannot be reached, never guesses", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new Error("offline");
    }) as unknown as typeof fetch);
    expect(await fetchAppInstance()).toBeNull();
  });
});
