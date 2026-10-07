import { afterEach, describe, expect, it, vi } from "vitest";

import { createHistoryRequests, reuseHistoryRows } from "./historyRequests";
import { createAgentChatSession, fetchAgentChatSessions, invalidateAgentChatSessions, type AgentChatSession } from "./agentChatApi";
import { deleteTextConversation, fetchConversations } from "./chatsApi";
import { createAgentChatStore } from "@/store/agentChat";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
const response = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("history request ownership", () => {
  it("shares the in-flight request, isolates the complete query, and never caches a later explicit refresh", async () => {
    const pending = deferred<Response>();
    const fetcher = vi.fn(() => pending.promise);
    vi.stubGlobal("fetch", fetcher);
    const first = fetchAgentChatSessions(200, "jarvis");
    const joined = fetchAgentChatSessions(200, "jarvis");
    const otherSurface = fetchAgentChatSessions(200, "society");
    const otherLimit = fetchAgentChatSessions(10, "jarvis");
    expect(first).toBe(joined);
    expect(otherSurface).not.toBe(first);
    expect(fetcher).toHaveBeenCalledTimes(3);
    // Each distinct response owns its body; this stand-in is deliberately reusable.
    pending.resolve({ ok: true, json: async () => ({ sessions: [] }) } as Response);
    await Promise.all([first, joined, otherSurface, otherLimit]);
    await fetchAgentChatSessions(200, "jarvis");
    expect(fetcher).toHaveBeenCalledTimes(4);
  });

  it("does not publish a pre-creation list when a new chat arrives during the request", async () => {
    const old = deferred<Response>();
    const fresh = [{ session_id: "new", surface: "jarvis", account_id: "seat-b" }];
    let reads = 0;
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") return response(fresh[0]);
      reads += 1;
      return reads === 1 ? old.promise : response({ sessions: fresh });
    }));
    const first = fetchAgentChatSessions(200, "jarvis");
    await createAgentChatSession({ provider: "fake", surface: "jarvis" });
    const joined = fetchAgentChatSessions(200, "jarvis");
    expect(joined).toBe(first);
    expect(reads).toBe(1);
    old.resolve(response({ sessions: [] }));
    expect(await first).toEqual(fresh);
    expect(await joined).toEqual(fresh);
    expect(reads).toBe(2);
  });

  it("does not resurrect a deleted conversation from a pending history response", async () => {
    const old = deferred<Response>();
    let reads = 0;
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "DELETE") return response({ ok: true });
      return ++reads === 1 ? old.promise : response([]);
    }));
    const first = fetchConversations();
    expect(fetchConversations()).toBe(first);
    await deleteTextConversation("removed");
    old.resolve(response([{ kind: "text", id: "removed" }]));
    expect(await first).toEqual([]);
    expect(reads).toBe(2);
  });

  it("releases a failed read for retry, including an aborted slow request", async () => {
    vi.useFakeTimers();
    const requests = createHistoryRequests<string>();
    const read = requests.read("one", (signal) => new Promise((_resolve, reject) => {
      signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }));
    const failure = expect(read).rejects.toMatchObject({ name: "TimeoutError" });
    expect(requests.read("one", async () => "unused")).toBe(read);
    await vi.advanceTimersByTimeAsync(10_000);
    await failure;
    await expect(requests.read("one", async () => "recovered")).resolves.toBe("recovered");
  });

  it("bounds continuous invalidation to two calls, settles all waiters and permits a later fresh read", async () => {
    const requests = createHistoryRequests<string>();
    let calls = 0;
    const churningRead = async () => {
      calls += 1;
      // This guard makes the regression fail rather than hanging an older
      // unbounded implementation in an endless microtask loop.
      if (calls > 2) throw new Error("fixture safety limit exceeded");
      await Promise.resolve();
      requests.invalidate();
      return "known stale";
    };
    const first = requests.read("history", churningRead);
    const second = requests.read("history", churningRead);
    expect(second).toBe(first);
    const settled = await Promise.allSettled([first, second]);
    expect(settled.map((result) => result.status)).toEqual(["rejected", "rejected"]);
    expect(calls).toBe(2);
    await expect(requests.read("history", async () => "fresh")).resolves.toBe("fresh");
  });

  it("retains the current store list when every response is invalidated and recovers on the next refresh", async () => {
    const local: AgentChatSession = {
      session_id: "just-created", title: "Local chat", provider: "fake", model: "", effort: "",
      cwd: "", permission_mode: "ask", surface: "jarvis", account_id: "seat-b", vendor_session: null,
      created_ms: 1, updated_ms: 2, message_count: 1, preview: "New message",
    };
    const current = [local];
    const store = createAgentChatStore("jarvis");
    store.setState({ sessions: current });
    let churning = true;
    let calls = 0;
    vi.stubGlobal("fetch", vi.fn(async () => {
      calls += 1;
      if (churning && calls > 2) throw new Error("fixture safety limit exceeded");
      await Promise.resolve();
      if (churning) invalidateAgentChatSessions();
      return response({ sessions: churning ? [] : [{ ...local, title: "Saved chat" }] });
    }));
    await store.getState().loadSessions();
    expect(calls).toBe(2);
    expect(store.getState().sessions).toBe(current);
    churning = false;
    await store.getState().loadSessions();
    expect(calls).toBe(3);
    expect(store.getState().sessions[0]).toMatchObject({ session_id: "just-created", title: "Saved chat" });
  });

  it("settles on its deadline even if a loader ignores abort, and ignores that loader's late result", async () => {
    vi.useFakeTimers();
    const requests = createHistoryRequests<string>();
    const late = deferred<string>();
    let signal!: AbortSignal;
    const read = requests.read("history", (givenSignal) => { signal = givenSignal; return late.promise; });
    const failure = expect(read).rejects.toMatchObject({ name: "TimeoutError" });
    await vi.advanceTimersByTimeAsync(10_000);
    await failure;
    expect(signal.aborted).toBe(true);
    await expect(requests.read("history", async () => "fresh")).resolves.toBe("fresh");
    late.resolve("stale late reply");
    await Promise.resolve();
    await expect(requests.read("history", async () => "newer")).resolves.toBe("newer");
  });
});

it("reuses unchanged lists/rows but detects ordering, accounts, approvals and metadata changes", () => {
  const rows = [
    { id: "one", title: "First", account_id: "seat-a", approvals: ["a"] },
    { id: "two", title: "Second", account_id: "seat-b", approvals: [] },
  ];
  const same = rows.map((row) => ({ ...row, approvals: [...row.approvals] }));
  expect(reuseHistoryRows(rows, same, (row) => row.id)).toBe(rows);
  const reordered = reuseHistoryRows(rows, [...same].reverse(), (row) => row.id);
  expect(reordered).not.toBe(rows);
  expect(reordered[0]).toBe(rows[1]);
  for (const patch of [{ title: "Renamed" }, { account_id: "seat-c" }, { approvals: ["b"] }]) {
    const changed = reuseHistoryRows(rows, [{ ...same[0], ...patch }, same[1]], (row) => row.id);
    expect(changed).not.toBe(rows);
    expect(changed[0]).not.toBe(rows[0]);
    expect(changed[1]).toBe(rows[1]);
  }
});
