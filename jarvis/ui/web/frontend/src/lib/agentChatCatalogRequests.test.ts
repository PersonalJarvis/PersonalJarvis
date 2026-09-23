import { afterEach, expect, test, vi } from "vitest";
import { fetchAgentChatCatalog, fetchAgentConnections } from "./agentChatApi";

afterEach(() => vi.unstubAllGlobals());

test("concurrent catalog readers share a request, but surfaces and later refreshes remain separate", async () => {
  let release!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => { release = resolve; });
  const request = vi.fn(() => pending);
  vi.stubGlobal("fetch", request);
  const first = fetchAgentChatCatalog("society");
  const second = fetchAgentChatCatalog("society");
  const other = fetchAgentChatCatalog("jarvis");
  expect(first).toBe(second);
  expect(request).toHaveBeenCalledTimes(2);
  // The response stub permits each distinct surface to read its own body.
  release({ ok: true, json: async () => ({ providers: [] }) } as Response);
  await Promise.all([first, second, other]);
  await fetchAgentChatCatalog("society");
  expect(request).toHaveBeenCalledTimes(3);
});

test("concurrent credential-status readers share the in-flight request", async () => {
  const request = vi.fn(async () => ({ ok: true, json: async () => ({ mapping: [] }) }) as Response);
  vi.stubGlobal("fetch", request);
  const first = fetchAgentConnections();
  const second = fetchAgentConnections();
  expect(first).toBe(second);
  await Promise.all([first, second]);
  expect(request).toHaveBeenCalledTimes(1);
  await fetchAgentConnections();
  expect(request).toHaveBeenCalledTimes(2);
});

test("a rejected discovery request is released so a later retry can succeed", async () => {
  const request = vi.fn().mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValue({ ok: true, json: async () => ({ providers: [] }) });
  vi.stubGlobal("fetch", request);
  const first = fetchAgentChatCatalog("society");
  const second = fetchAgentChatCatalog("society");
  const results = await Promise.allSettled([first, second]);
  expect(results.map((result) => result.status)).toEqual(["rejected", "rejected"]);
  await expect(fetchAgentChatCatalog("society")).resolves.toEqual({ providers: [] });
  expect(request).toHaveBeenCalledTimes(2);
});


test("catalog requests are isolated by account, session and working folder", async () => {
  const request = vi.fn(async (_url: string) => ({ ok: true, json: async () => ({ providers: [] }) }) as Response);
  vi.stubGlobal("fetch", request);
  const first = fetchAgentChatCatalog("society", { accountId: "one" });
  const same = fetchAgentChatCatalog("society", { accountId: "one" });
  const second = fetchAgentChatCatalog("society", { accountId: "two" });
  const session = fetchAgentChatCatalog("society", { sessionId: "chat-two", cwd: "/project" });
  expect(first).toBe(same);
  expect(first).not.toBe(second);
  await Promise.all([first, second, session]);
  expect(request.mock.calls.map(([url]) => url)).toEqual([
    "/api/agent-chat/catalog?surface=society&account_id=one",
    "/api/agent-chat/catalog?surface=society&account_id=two",
    "/api/agent-chat/catalog?surface=society&session_id=chat-two&cwd=%2Fproject",
  ]);
});
