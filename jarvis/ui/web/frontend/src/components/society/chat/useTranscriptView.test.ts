import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { TimelineItem } from "@/components/agentchat/reduce";
import { readClearedViews, useTranscriptView, useTranscriptViewStore } from "./useTranscriptView";

const message = (id: string, tsMs = 1): TimelineItem => ({ type: "user", id, tsMs, text: id, attachments: [] });

beforeEach(() => {
  localStorage.clear();
  useTranscriptViewStore.setState({ boundaries: {} });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("hides previous items without changing the transcript and shows new items in order", () => {
  const items = [message("first"), message("second")];
  const { result, rerender } = renderHook(({ history }) => useTranscriptView("chat-a", history), { initialProps: { history: items } });
  act(() => result.current.clear());
  expect(result.current.items).toEqual([]);
  expect(items.map((item) => item.id)).toEqual(["first", "second"]);
  // The server may emit several events in the same millisecond.
  rerender({ history: [...items, message("third"), message("fourth")] });
  expect(result.current.items.map((item) => item.id)).toEqual(["third", "fourth"]);
  act(() => result.current.clear());
  expect(result.current.items).toEqual([]);
});

it("keeps the boundary per chat, across reopening, storage reload and snapshot replay", () => {
  const items = [message("first")];
  const first = renderHook(() => useTranscriptView("chat-a", items));
  act(() => first.result.current.clear());
  first.unmount();
  const saved = readClearedViews();
  expect(saved).toEqual({ "chat-a": { id: "first", tsMs: 1 } });
  useTranscriptViewStore.setState({ boundaries: saved });
  const reopened = renderHook(({ id }) => useTranscriptView(id, items), { initialProps: { id: "chat-a" } });
  expect(reopened.result.current.items).toEqual([]);
  reopened.rerender({ id: "chat-b" });
  expect(reopened.result.current.items).toEqual(items);
  reopened.rerender({ id: "chat-a" });
  expect(reopened.result.current.items).toEqual([]);
});

it("hides old partial snapshots until the boundary arrives again", () => {
  const items = [message("old", 1), message("boundary", 2)];
  const { result, rerender } = renderHook(({ history }) => useTranscriptView("chat-a", history), { initialProps: { history: items } });
  act(() => result.current.clear());
  rerender({ history: [message("old", 1)] });
  expect(result.current.items).toEqual([]);
  rerender({ history: [message("new", 3)] });
  expect(result.current.items.map((item) => item.id)).toEqual(["new"]);
});

it("does nothing for a fresh or empty chat", () => {
  const { result, rerender } = renderHook(({ id, history }) => useTranscriptView(id, history), {
    initialProps: { id: null as string | null, history: [message("old")] },
  });
  act(() => result.current.clear());
  rerender({ id: "empty", history: [] });
  act(() => result.current.clear());
  expect(useTranscriptViewStore.getState().boundaries).toEqual({});
});

it("still clears the view when browser storage is unavailable", () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("storage disabled"); });
  const { result } = renderHook(() => useTranscriptView("chat-a", [message("old")]));
  act(() => result.current.clear());
  expect(result.current.items).toEqual([]);
});

it("ignores corrupt browser preferences", () => {
  localStorage.setItem("jarvis.chatView.cleared.v1", "not json");
  expect(readClearedViews()).toEqual({});
  localStorage.setItem("jarvis.chatView.cleared.v1", '{"bad":null,"also-bad":{"id":1,"tsMs":2}}');
  expect(readClearedViews()).toEqual({});
});
