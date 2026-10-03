import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
import { createAgentChatStore, draftKey } from "./agentChat";
import type { AgentChatProvider, ChatSelection } from "@/lib/agentChatApi";

const provider: AgentChatProvider = {
  id: "claude-api", label: "Anthropic Claude", runner: "brain", models_source: "curated",
  family: "anthropic", keyless: false, native_resume: true, cli_installed: null,
  curated_models: [], effort_levels: ["high"], default_effort: "high", default_model: "",
  permission_modes: [{ id: "bypass", label: "Bypass", description: "Bypass" }], default_permission_mode: "bypass",
};
const pick: ChatSelection = { provider: "claude-api", model: "claude-sonnet-5-5", effort: "high" };
const draft = { ...pick, cwd: "", permissionMode: "bypass", buildMode: "bypass" };
const reply = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });

class Socket {
  static latest: Socket;
  onmessage: ((event: { data: string }) => void) | null = null;
  constructor() { Socket.latest = this; }
  close() {}
}

function backend(selection: ChatSelection | null = pick) {
  let saved = selection;
  const writes: ChatSelection[] = [];
  const fetch = vi.fn(async (input: string, init?: RequestInit) => {
    if (String(input).startsWith("/api/agent-chat/catalog")) return reply({
      providers: [provider], selection: saved, default_cwd: "", shell: "test",
    });
    if (input === "/api/agent-chat/selection") {
      saved = JSON.parse(String(init?.body));
      writes.push(saved!);
      return reply(saved);
    }
    return reply({ mapping: [], providers: [] });
  });
  vi.stubGlobal("fetch", fetch);
  return { fetch, writes };
}

beforeEach(() => { localStorage.clear(); vi.stubGlobal("WebSocket", Socket); });
afterEach(() => { vi.unstubAllGlobals(); });

describe("remembered Jarvis chat model", () => {
  it("restores the backend pick after local storage is cleared", async () => {
    backend();
    const store = createAgentChatStore("jarvis");
    await store.getState().loadCatalog();
    expect(store.getState().draft).toMatchObject(pick);
  });

  it("saves a pick before a chat exists and restores it for a fresh store", async () => {
    const { writes } = backend(null);
    const store = createAgentChatStore("jarvis");
    await store.getState().loadCatalog();
    await store.getState().setDraft(pick);
    expect(writes.at(-1)).toEqual(pick);
    expect(store.getState().activeSessionId).toBeNull();
    localStorage.clear();
    const restarted = createAgentChatStore("jarvis");
    await restarted.getState().loadCatalog();
    expect(restarted.getState().draft).toMatchObject(pick);
  });

  it("migrates the existing local choice without a paid chat turn", async () => {
    localStorage.setItem(draftKey("jarvis"), JSON.stringify(draft));
    const { writes } = backend(null);
    await createAgentChatStore("jarvis").getState().loadCatalog();
    expect(writes).toEqual([pick]);
  });

  it("reading an old conversation does not become the next chat default", async () => {
    const { writes } = backend();
    const store = createAgentChatStore("jarvis");
    await store.getState().loadCatalog();
    store.getState().openSession("old");
    await waitFor(() => expect(Socket.latest.onmessage).not.toBeNull());
    Socket.latest.onmessage!({ data: JSON.stringify({ type: "snapshot", events: [], session: {
      session_id: "old", provider: "claude-api", model: "old-model", effort: "high",
      permission_mode: "bypass", cwd: "", surface: "jarvis",
    } }) });
    expect(store.getState().draft.model).toBe("old-model");
    store.getState().newChat();
    expect(store.getState().draft.model).toBe(pick.model);
    await waitFor(() => expect(store.getState().catalog).not.toBeNull());
    expect(store.getState().draft.model).toBe(pick.model);
    expect(writes).toEqual([]);
    store.getState().disconnect();
  });

  it("does not let a slow catalog replace a newer explicit selection", async () => {
    const { fetch } = backend();
    let finish!: (response: Response) => void;
    fetch.mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve; }));
    const store = createAgentChatStore("jarvis");
    const loading = store.getState().loadCatalog();
    await store.getState().setDraft({ ...pick, model: "newer-model" });
    finish(reply({ providers: [provider], selection: pick, default_cwd: "", shell: "test" }));
    await loading;
    expect(store.getState().draft.model).toBe("newer-model");
  });

  it("serializes rapid choices so the last click wins in persistent storage", async () => {
    const { fetch, writes } = backend();
    const store = createAgentChatStore("jarvis");
    store.setState({ catalog: { providers: [provider], default_cwd: "", shell: "test" } });
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const normal = fetch.getMockImplementation()!;
    fetch.mockImplementationOnce(async (...args) => { await gate; return normal(...args); });
    const first = store.getState().setDraft({ ...pick, model: "first" });
    const last = store.getState().setDraft({ ...pick, model: "last" });
    await Promise.resolve();
    expect(writes).toEqual([]);
    release();
    await Promise.all([first, last]);
    expect(writes.map((row) => row.model)).toEqual(["first", "last"]);
  });

  it("never writes a Society or IDE pick into the Jarvis default", async () => {
    const { writes } = backend();
    await createAgentChatStore("agent").getState().setDraft(pick);
    await createAgentChatStore("society").getState().setDraft(pick);
    expect(writes).toEqual([]);
  });
});
