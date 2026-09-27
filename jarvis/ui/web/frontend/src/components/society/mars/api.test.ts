import { afterEach, describe, expect, it, vi } from "vitest";
import { clearDraftAttempt, MarsApiError, readDraftAttempt, saveDraftAttempt, submitMarsDraft } from "./api";
import { containsCredential } from "./credentialInput";

afterEach(() => { sessionStorage.clear(); vi.unstubAllGlobals(); });
const attempt = { request_id: "52f36e94-8701-4b46-b0c1-628bc775fdf0", agent_id: "comms", draft: "Draft a meeting invitation." };

describe("station command recovery", () => {
  it("preserves a newer attempt when an unmounted request completes late", () => {
    saveDraftAttempt(attempt);
    expect(readDraftAttempt()).toEqual(attempt);
    const next = { ...attempt, request_id: "52f36e94-8701-4b46-b0c1-628bc775fdf1" };
    saveDraftAttempt(next);
    clearDraftAttempt(attempt.request_id);
    expect(readDraftAttempt()).toEqual(next);
    clearDraftAttempt(next.request_id);
    expect(readDraftAttempt()).toBeNull();
  });
  it("does not persist credential-shaped drafts", () => {
    const synthetic = "sk-" + "example".repeat(6);
    expect(containsCredential(synthetic)).toBe(true);
    saveDraftAttempt({ ...attempt, draft: synthetic });
    expect(readDraftAttempt()).toBeNull();
    expect(containsCredential(attempt.draft)).toBe(false);
    expect(containsCredential("commit " + "a".repeat(40))).toBe(false);
  });
  it("retains exact request identity on repeated uncertain submissions", async () => {
    const bodies: string[] = [];
    vi.stubGlobal("fetch", async (_url: string, init: RequestInit) => {
      bodies.push(String(init.body)); throw new Error("offline");
    });
    await expect(submitMarsDraft(attempt)).rejects.toThrow("offline");
    await expect(submitMarsDraft(attempt)).rejects.toThrow("offline");
    expect(bodies[0]).toBe(bodies[1]);
  });
  it("exposes only a safe credential-rejection classification", async () => {
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({
      detail: { reason: "credential_input_use_api_key_settings" },
    }), { status: 422 }));
    await expect(submitMarsDraft(attempt)).rejects.toMatchObject({
      status: 422, credentialInput: true, message: "mars_request_failed",
    } satisfies Partial<MarsApiError>);
  });
});
