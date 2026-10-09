import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { MODEL_ACCESS_KEY, preferredModelAccess, useModelAccess } from "./modelAccess";

beforeEach(() => localStorage.clear());
afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); });

test("starts on a subscription, honors explicit choices and only offers available access", () => {
  const api = { kind: "api" as const };
  const subscription = { kind: "subscription" as const };
  expect(preferredModelAccess([api, subscription])).toBe(subscription);
  expect(preferredModelAccess([api, subscription], "api")).toBe(api);
  expect(preferredModelAccess([api], "subscription")).toBe(api);
  expect(preferredModelAccess([api, { ...subscription, disabled: true }])).toBe(api);
  expect(preferredModelAccess([])).toBeUndefined();
});

test("shares explicit choices across pickers and remounts without changing other providers", () => {
  const first = renderHook(useModelAccess);
  const second = renderHook(useModelAccess);
  act(() => first.result.current[1]("openai", "api"));
  act(() => first.result.current[1]("antigravity", "subscription"));
  expect(second.result.current[0]).toEqual({ openai: "api", gemini: "subscription" });
  first.unmount(); second.unmount();
  expect(renderHook(useModelAccess).result.current[0]).toEqual({ openai: "api", gemini: "subscription" });
});

test("reloads cross-window changes and clearing preferences", () => {
  const state = renderHook(useModelAccess);
  act(() => {
    localStorage.setItem(MODEL_ACCESS_KEY, JSON.stringify({ openai: "api", invalid: "paid" }));
    window.dispatchEvent(new StorageEvent("storage", { key: MODEL_ACCESS_KEY }));
  });
  expect(state.result.current[0]).toEqual({ openai: "api" });
  act(() => { localStorage.clear(); window.dispatchEvent(new StorageEvent("storage", { key: null })); });
  expect(state.result.current[0]).toEqual({});
});

test("ignores corrupt storage and retains an explicit choice when writing is blocked", () => {
  localStorage.setItem(MODEL_ACCESS_KEY, "broken");
  const state = renderHook(useModelAccess);
  expect(state.result.current[0]).toEqual({});
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
  act(() => state.result.current[1]("openai", "api"));
  expect(state.result.current[0]).toEqual({ openai: "api" });
});
