import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { useEventStore } from "@/store/events";
import { WORLD_ID, type MarsSnapshot, type CommandState } from "../mars/api";
import { useCompanionPresentation } from "./useCompanionPresentation";

const key = ["mars", WORLD_ID, "snapshot"];
function snapshot(state: CommandState, seq = 1): MarsSnapshot {
  return { world_id: WORLD_ID, schema_version: 1, layout_version: 1, seq, commands: [{
    command_id: "test-command", agent_id: "test-agent", request_id: "test-request", trace_id: "test-trace",
    world_id: WORLD_ID, state, task_ref: "test-task", result_ref: null, reason: "", cancel_requested: false,
  }] };
}
afterEach(() => { useEventStore.getState().setVoice("idle"); vi.useRealTimers(); });
function harness(initial: CommandState) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  client.setQueryData(key, snapshot(initial));
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const hook = renderHook(() => useCompanionPresentation(false), { wrapper });
  return { client, ...hook };
}

it("does not replay a completion from the initial snapshot", () => {
  const h = harness("completed");
  expect(h.result.current.task).toBe("idle");
  h.unmount(); h.client.clear();
});
it.each(["unknown", "interrupted"] as const)("keeps initial %s work blocked beyond notification expiry", async (state) => {
  vi.useFakeTimers();
  const h = harness(state);
  expect(h.result.current.task).toBe("blocked");
  act(() => { vi.advanceTimersByTime(6000); useEventStore.getState().setVoice("thinking"); });
  expect(h.result.current.task).toBe("blocked");
  const mixed = snapshot(state, 2);
  mixed.commands.push({ ...snapshot("queued").commands[0], command_id: "queued-other-command" });
  await act(async () => { h.client.setQueryData(key, mixed); await vi.advanceTimersByTimeAsync(1); });
  expect(h.result.current.task).toBe("blocked");
  await act(async () => { h.client.setQueryData(key, snapshot("active", 3)); await vi.advanceTimersByTimeAsync(1); });
  expect(h.result.current.task).toBe("working");
  h.unmount(); h.client.clear();
});
it("keeps an observed unknown outcome blocked until the snapshot resolves it", async () => {
  vi.useFakeTimers();
  const h = harness("active");
  await act(async () => { h.client.setQueryData(key, snapshot("unknown", 2)); await vi.advanceTimersByTimeAsync(1); });
  expect(h.result.current.task).toBe("blocked");
  act(() => vi.advanceTimersByTime(6000));
  expect(h.result.current.task).toBe("blocked");
  await act(async () => { h.client.setQueryData(key, snapshot("completed", 3)); await vi.advanceTimersByTimeAsync(1); });
  expect(h.result.current.task).toBe("complete");
  act(() => vi.advanceTimersByTime(6000));
  expect(h.result.current.task).toBe("idle");
  h.unmount(); h.client.clear();
});
it("shows real active work and a subsequent observed failure", async () => {
  const h = harness("active");
  expect(h.result.current.task).toBe("working");
  await act(async () => { h.client.setQueryData(key, snapshot("failed", 2)); await new Promise(resolve => setTimeout(resolve, 5)); });
  expect(h.result.current.task).toBe("error");
  h.unmount(); h.client.clear();
});
it("stops speaking when the shared voice state is paused or interrupted", () => {
  const h = harness("completed");
  act(() => useEventStore.getState().setVoice("speaking"));
  expect(h.result.current.audio).toBe("speaking");
  act(() => useEventStore.getState().setVoice("paused"));
  expect(h.result.current.audio).toBe("idle");
  expect(h.result.current.muted).toBe(true);
  act(() => useEventStore.getState().setVoice("idle"));
  expect(h.result.current.audio).toBe("idle");
  h.unmount(); h.client.clear();
});
