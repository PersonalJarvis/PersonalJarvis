import { afterEach, describe, expect, it, vi } from "vitest";

import { BROWSER_VOICE_FEATURE, askHostMicrophone } from "./hostMicrophone";

function answerWith(body: Record<string, unknown>, status = 200) {
  const fetchMock = vi.fn(async () => ({ ok: status < 400, status, json: async () => body }) as Response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe("askHostMicrophone", () => {
  it("asks from the gesture with the browser_voice feature attached", async () => {
    const fetchMock = answerWith({ outcome: "granted", granted: true, reason: "" });

    await askHostMicrophone();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/permissions/microphone/request?dry_run=false");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ feature: BROWSER_VOICE_FEATURE });
    expect(BROWSER_VOICE_FEATURE).toBe("browser_voice");
  });

  it("lets the stream open when macOS needs nothing or allows it", async () => {
    answerWith({ outcome: "granted", granted: true, reason: "" });
    expect(await askHostMicrophone()).toEqual({ kind: "granted" });
    answerWith({ outcome: "not_required", granted: true, reason: "" });
    expect(await askHostMicrophone()).toEqual({ kind: "granted" });
  });

  it("does not open the stream while macOS is asking", async () => {
    answerWith({ outcome: "pending", granted: false, reason: "not_determined" });
    expect(await askHostMicrophone()).toEqual({ kind: "pending" });
  });

  it("blocks with the shared reason vocabulary, and maps an unknown word by the outcome", async () => {
    answerWith({ outcome: "denied", granted: false, reason: "denied" });
    expect(await askHostMicrophone()).toEqual({ kind: "blocked", reason: "denied" });
    answerWith({ outcome: "needs_settings", granted: false, reason: "something-new" });
    expect(await askHostMicrophone()).toEqual({ kind: "blocked", reason: "needs_settings" });
    answerWith({ outcome: "unavailable", granted: false, reason: "" });
    expect(await askHostMicrophone()).toEqual({ kind: "blocked", reason: "unavailable" });
  });

  it("goes on (and never throws) when the route fails, so voice is not made impossible by it", async () => {
    answerWith({ error: "rate_limited", retry_after_s: 3 }, 429);
    expect(await askHostMicrophone()).toEqual({ kind: "unknown" });
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new Error("offline"))));
    expect(await askHostMicrophone()).toEqual({ kind: "unknown" });
  });
});
