import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAgentSearch } from "./useAgentSearch";
import type { AgentSearchRequest, AgentSearchResponse } from "./agentSearch";

class SearchWorker {
  static instances: SearchWorker[] = [];
  messages: AgentSearchRequest[] = [];
  terminated = false;
  onmessage: ((event: MessageEvent<AgentSearchResponse>) => void) | null = null;
  onerror: (() => void) | null = null;
  onmessageerror: (() => void) | null = null;
  constructor() { SearchWorker.instances.push(this); }
  postMessage(message: AgentSearchRequest) { this.messages.push(message); }
  terminate() { this.terminated = true; }
  reply(message: AgentSearchResponse) { this.onmessage?.({ data: message } as MessageEvent<AgentSearchResponse>); }
  get lastId() { return this.messages.at(-1)!.id; }
}

const documents = [{ id: "voice@w1", texts: ["Prevent unexpected hangups"] }];
beforeEach(() => {
  vi.useFakeTimers();
  SearchWorker.instances = [];
  vi.stubGlobal("Worker", SearchWorker);
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });
const debounce = () => act(() => vi.advanceTimersByTime(300));

describe("local agent search lifecycle", () => {
  it("loads nothing for an empty search and debounces typing", () => {
    const { result, rerender } = renderHook(({ query }) => useAgentSearch(query, documents), { initialProps: { query: "" } });
    debounce();
    expect(SearchWorker.instances).toHaveLength(0);
    rerender({ query: "call" });
    rerender({ query: "calls end unexpectedly" });
    expect(result.current.matches).toEqual([]);
    debounce();
    expect(SearchWorker.instances).toHaveLength(1);
    expect(SearchWorker.instances[0].messages).toEqual([expect.objectContaining({ type: "search", query: "calls end unexpectedly" })]);
  });

  it("discards stale results immediately when query or scope changes", () => {
    const { result, rerender } = renderHook(({ query, docs }) => useAgentSearch(query, docs), { initialProps: { query: "calls", docs: documents } });
    debounce();
    const worker = SearchWorker.instances[0];
    const previous = worker.lastId;
    act(() => worker.reply({ type: "result", id: previous, matches: [{ id: "voice@w1", score: 0.8 }] }));
    expect(result.current.matches).toHaveLength(1);
    rerender({ query: "calls", docs: [{ id: "voice@w2", texts: ["Different task"] }] });
    expect(result.current.matches).toEqual([]);
    debounce();
    act(() => worker.reply({ type: "result", id: previous, matches: [{ id: "voice@w1", score: 0.8 }] }));
    expect(result.current.matches).toEqual([]);
    act(() => worker.reply({ type: "result", id: worker.lastId, matches: [] }));
    expect(result.current.status).toBe("ready");
  });

  it("does not reindex unchanged task text when pane polling returns new objects", () => {
    const { rerender } = renderHook(({ docs }) => useAgentSearch("calls", docs), { initialProps: { docs: documents } });
    debounce();
    const worker = SearchWorker.instances[0];
    rerender({ docs: structuredClone(documents) });
    debounce();
    expect(worker.messages).toHaveLength(1);
  });

  it("reports failure and creates a fresh native session on retry", () => {
    const { result } = renderHook(() => useAgentSearch("calls", documents));
    debounce();
    const worker = SearchWorker.instances[0];
    act(() => worker.reply({ type: "error", id: worker.lastId }));
    expect(result.current.status).toBe("error");
    expect(worker.terminated).toBe(true);
    act(() => result.current.retry());
    debounce();
    expect(SearchWorker.instances).toHaveLength(2);
  });

  it("times out stuck inference and releases memory on clear and unmount", () => {
    const { result, rerender, unmount } = renderHook(({ query }) => useAgentSearch(query, documents), { initialProps: { query: "calls" } });
    debounce();
    act(() => vi.advanceTimersByTime(180_000));
    expect(result.current.status).toBe("error");
    expect(SearchWorker.instances[0].terminated).toBe(true);
    act(() => result.current.retry());
    debounce();
    rerender({ query: " " });
    expect(result.current.status).toBe("idle");
    expect(SearchWorker.instances[1].terminated).toBe(true);
    rerender({ query: "calls" });
    debounce();
    unmount();
    expect(SearchWorker.instances[2].terminated).toBe(true);
  });
});
