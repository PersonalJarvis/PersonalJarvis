import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentChatSession } from "@/lib/agentChatApi";
import { createAgentChatStore, draftKey } from "@/store/agentChat";

/**
 * The store knows its surface: the front page's store speaks for the typed
 * Jarvis chat (`jarvis`), an IDE's for its coding sessions (`agent`). Each
 * asks the backend for its own list and catalog, stamps the surface on the
 * sessions it creates, and keeps its draft under its own key.
 */

function session(id: string, surface?: AgentChatSession["surface"]): AgentChatSession {
  return {
    session_id: id,
    title: id,
    provider: "claude-api",
    model: "",
    effort: "high",
    cwd: "C:\\work",
    permission_mode: "ask",
    ...(surface === undefined ? {} : { surface }),
    vendor_session: null,
    created_ms: 1,
    updated_ms: 2,
    message_count: 1,
    preview: id,
  };
}

interface Call {
  url: string;
  method: string;
  body: Record<string, unknown> | null;
}

/** A fetch that answers every agent-chat route with something plausible and records what was asked. */
function stubFetch(sessions: AgentChatSession[]): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const body = typeof init?.body === "string" ? (JSON.parse(init.body) as Record<string, unknown>) : null;
      calls.push({ url, method, body });
      const reply = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
      if (url.startsWith("/api/agent-chat/catalog")) return reply({ providers: [], default_cwd: "C:\\work", shell: "pwsh" });
      if (url.startsWith("/api/jarvis-agent/status")) return reply({ mapping: [] });
      if (url.startsWith("/api/agent-chat/sessions?")) return reply({ sessions });
      if (url === "/api/agent-chat/sessions" && method === "POST") {
        return reply({ ...session("new"), ...(body ?? {}) });
      }
      if (url.endsWith("/messages")) return reply({ turn_id: "t1" });
      return new Response(null, { status: 404 });
    }),
  );
  return calls;
}

/** jsdom's WebSocket would try to reach a real host; a silent stand-in is enough here. */
class FakeSocket {
  onopen: (() => void) | null = null;
  onmessage: ((msg: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  close() {}
}

describe("agent-chat store surfaces", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal("WebSocket", FakeSocket);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("names the front page's draft key as it always was, and gives the IDE its own", () => {
    expect(draftKey("jarvis")).toBe("jarvis.agentChat.draft.v1");
    expect(draftKey("agent")).toBe("jarvis.agentChat.draft.agent.v1");
  });

  it("creates a front-page session stamped with its surface", async () => {
    const calls = stubFetch([]);
    const store = createAgentChatStore("jarvis");
    store.setState({
      draft: { provider: "claude-api", model: "", effort: "high", permissionMode: "ask", buildMode: "ask", cwd: "C:\\work" },
    });
    await store.getState().send("hello");
    const create = calls.find((c) => c.url === "/api/agent-chat/sessions" && c.method === "POST");
    expect(create?.body).toMatchObject({ provider: "claude-api", permission_mode: "ask", surface: "jarvis" });
    expect(store.getState().activeSessionId).toBe("new");
    expect(store.getState().sessions[0]?.surface).toBe("jarvis");
  });

  it("asks the list for its own surface and drops rows of the other", async () => {
    const calls = stubFetch([session("mine", "agent"), session("theirs", "jarvis")]);
    const store = createAgentChatStore("agent");
    await store.getState().loadSessions();
    const list = calls.find((c) => c.url.startsWith("/api/agent-chat/sessions?"));
    expect(list?.url).toContain("surface=agent");
    expect(store.getState().sessions.map((s) => s.session_id)).toEqual(["mine"]);
  });

  it("keeps rows that name no surface — an older backend still lists its chats", async () => {
    stubFetch([session("old"), session("front", "jarvis"), session("ide", "agent")]);
    const store = createAgentChatStore("jarvis");
    await store.getState().loadSessions();
    expect(store.getState().sessions.map((s) => s.session_id)).toEqual(["old", "front"]);
  });

  it("asks the catalog for its surface", async () => {
    const calls = stubFetch([]);
    const store = createAgentChatStore("jarvis");
    await store.getState().loadCatalog();
    const catalog = calls.find((c) => c.url.startsWith("/api/agent-chat/catalog"));
    expect(catalog?.url).toBe("/api/agent-chat/catalog?surface=jarvis");
  });

  it("keeps one draft per surface, under separate keys", async () => {
    stubFetch([]);
    const front = createAgentChatStore("jarvis");
    const ide = createAgentChatStore("agent");
    await front.getState().setDraft({ cwd: "C:\\front" });
    await ide.getState().setDraft({ cwd: "C:\\ide" });
    expect(front.getState().draft.cwd).toBe("C:\\front");
    expect(ide.getState().draft.cwd).toBe("C:\\ide");
    expect(JSON.parse(window.localStorage.getItem(draftKey("jarvis")) ?? "{}")).toMatchObject({ cwd: "C:\\front" });
    expect(JSON.parse(window.localStorage.getItem(draftKey("agent")) ?? "{}")).toMatchObject({ cwd: "C:\\ide" });
    // A fresh store of each surface reads back its own, not the other's.
    expect(createAgentChatStore("jarvis").getState().draft.cwd).toBe("C:\\front");
    expect(createAgentChatStore("agent").getState().draft.cwd).toBe("C:\\ide");
  });
});
