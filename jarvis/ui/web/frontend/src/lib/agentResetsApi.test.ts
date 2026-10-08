import { afterEach, expect, it, vi } from "vitest";
import { consumeAccountReset } from "./agentResetsApi";

afterEach(() => vi.unstubAllGlobals());
const attempt = { idempotency_key: "one-attempt", credit_id: "one-credit", account_key: "a".repeat(64) };

it("never accepts an unknown redemption outcome as success", async () => {
  const fetcher = vi.fn(async () => Response.json({ outcome: "futureState", resets: {} }));
  vi.stubGlobal("fetch", fetcher);
  await expect(consumeAccountReset("seat", attempt)).rejects.toThrow("uncertain");
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("sends one confirmed request and retains a safe failure classification", async () => {
  const fetcher = vi.fn(async () => Response.json({ detail: { code: "account_changed" } }, { status: 409 }));
  vi.stubGlobal("fetch", fetcher);
  await expect(consumeAccountReset("codex:one", attempt)).rejects.toThrow("account_changed");
  expect(fetcher).toHaveBeenCalledWith("/api/agent-accounts/codex%3Aone/resets/consume", expect.objectContaining({
    method: "POST", body: JSON.stringify({ ...attempt, confirmed: true }),
  }));
});
