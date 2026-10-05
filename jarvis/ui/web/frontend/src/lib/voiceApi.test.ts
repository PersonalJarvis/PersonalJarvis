import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchVoiceRuntimeState, requestVoiceCall } from "./voiceApi";

function answer(body: unknown, status = 200) {
  return vi.fn(async () => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  })) as unknown as typeof fetch;
}

describe("voiceApi", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reads whether this browser should hold the call", async () => {
    vi.stubGlobal(
      "fetch",
      answer({ available: false, state: "unavailable", voice_state: "idle", browser_call: true }),
    );

    expect(await fetchVoiceRuntimeState()).toEqual({
      available: false,
      voiceState: "idle",
      browserCall: true,
    });
  });

  it("never offers a browser call when an older backend does not say so", async () => {
    vi.stubGlobal("fetch", answer({ available: true, state: "idle" }));

    expect(await fetchVoiceRuntimeState()).toEqual({
      available: true,
      voiceState: "idle",
      browserCall: false,
    });
  });

  it("tells a host without a speech pipeline apart by its status", async () => {
    vi.stubGlobal("fetch", answer({ detail: "voice-pipeline-unavailable" }, 503));

    const failure = await requestVoiceCall().catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(Error);
    expect((failure as Error).message).toBe("Voice is not running on this computer.");
    expect((failure as { status?: number }).status).toBe(503);
  });
});
