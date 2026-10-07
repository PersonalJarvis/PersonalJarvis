import { afterEach, describe, expect, it, vi } from "vitest";
import {
  applyMemoryWrite, FAILED_SHOW_MS, memoryNoteFor, nextExpiryIn, PENDING_STALE_MS, SAVED_SHOW_MS, useMemoryWrites,
  type MemoryWritesState,
} from "./memoryWrites";

const EMPTY: MemoryWritesState = { writes: {}, settled: {} };

function receipt(phase: string, patch: Record<string, unknown> = {}) {
  return { update_id: "u1", agent_id: "jarvis", file: "MEMORY.md", phase, operation: "add", ...patch };
}

function run(steps: [string, Record<string, unknown>?, number?][]): MemoryWritesState {
  return steps.reduce((s, [phase, patch, at]) => applyMemoryWrite(s, receipt(phase, patch), at ?? 1_000), EMPTY);
}

describe("applyMemoryWrite", () => {
  it("records a pending write and settles it as saved under the same id", () => {
    const pending = run([["pending"]]);
    expect(memoryNoteFor(pending.writes, "jarvis", 1_000)).toEqual({ phase: "pending", files: ["MEMORY.md"], atMs: 1_000 });
    const saved = applyMemoryWrite(pending, receipt("saved"), 1_200);
    expect(memoryNoteFor(saved.writes, "jarvis", 1_200)).toEqual({ phase: "saved", files: ["MEMORY.md"], atMs: 1_200 });
  });

  it("ignores a pending receipt that arrives after its write settled", () => {
    const state = run([["saved", {}, 1_000], ["pending", {}, 1_100]]);
    expect(memoryNoteFor(state.writes, "jarvis", 1_100)?.phase).toBe("saved");
  });

  it("drops a write that changed nothing without ever claiming saved", () => {
    const state = run([["pending"], ["unchanged"]]);
    expect(memoryNoteFor(state.writes, "jarvis", 1_000)).toBeNull();
  });

  it("keeps the file a pending receipt named when the settle receipt has none", () => {
    const state = run([["pending", { file: "USER.md" }], ["saved", { file: "" }]]);
    expect(memoryNoteFor(state.writes, "jarvis", 1_000)?.files).toEqual(["USER.md"]);
  });

  it("lets an unconfirmed pending write go stale instead of turning it into saved", () => {
    const state = run([["pending"]]);
    expect(memoryNoteFor(state.writes, "jarvis", 1_000 + PENDING_STALE_MS - 1)?.phase).toBe("pending");
    expect(memoryNoteFor(state.writes, "jarvis", 1_000 + PENDING_STALE_MS)).toBeNull();
  });

  it("shows saved and failed only briefly", () => {
    const saved = run([["saved"]]);
    expect(memoryNoteFor(saved.writes, "jarvis", 1_000 + SAVED_SHOW_MS)).toBeNull();
    const failed = run([["failed"]]);
    expect(memoryNoteFor(failed.writes, "jarvis", 1_000 + FAILED_SHOW_MS - 1)?.phase).toBe("failed");
  });

  it("refuses malformed payloads and never shows a path", () => {
    expect(applyMemoryWrite(EMPTY, null, 1)).toBe(EMPTY);
    expect(applyMemoryWrite(EMPTY, receipt("exploded"), 1)).toBe(EMPTY);
    expect(applyMemoryWrite(EMPTY, receipt("pending", { update_id: "" }), 1)).toBe(EMPTY);
    const pathy = applyMemoryWrite(EMPTY, receipt("pending", { file: "C:\\Users\\x\\MEMORY.md" }), 1);
    expect(memoryNoteFor(pathy.writes, "jarvis", 1)).toEqual({ phase: "pending", files: [], atMs: 1 });
  });
});

describe("memoryNoteFor with concurrent writes", () => {
  it("lists every file still being written", () => {
    const state = run([["pending", { update_id: "a" }, 1_000], ["pending", { update_id: "b", file: "SOUL.md" }, 1_100]]);
    expect(memoryNoteFor(state.writes, "jarvis", 1_100)).toMatchObject({ phase: "pending", files: ["MEMORY.md", "SOUL.md"] });
  });

  it("keeps writing ahead of an earlier success on another file", () => {
    const state = run([["saved", { update_id: "a" }, 1_000], ["pending", { update_id: "b", file: "USER.md" }, 1_100]]);
    expect(memoryNoteFor(state.writes, "jarvis", 1_100)).toMatchObject({ phase: "pending", files: ["USER.md"] });
  });

  it("never lets a later success hide a failure", () => {
    const state = run([["failed", { update_id: "a", file: "SOUL.md" }, 1_000], ["saved", { update_id: "b" }, 1_100]]);
    expect(memoryNoteFor(state.writes, "jarvis", 1_100)).toMatchObject({ phase: "failed", files: ["SOUL.md"] });
  });

  it("only reports the asked agent's writes", () => {
    const state = run([["pending", { agent_id: "scout" }]]);
    expect(memoryNoteFor(state.writes, "jarvis", 1_000)).toBeNull();
  });
});

describe("useMemoryWrites", () => {
  afterEach(() => {
    useMemoryWrites.getState().reset();
    vi.useRealTimers();
  });

  it("clears expired receipts on its own", () => {
    vi.useFakeTimers();
    vi.setSystemTime(10_000);
    useMemoryWrites.getState().receive(receipt("saved"));
    expect(Object.keys(useMemoryWrites.getState().writes)).toEqual(["u1"]);
    expect(nextExpiryIn(useMemoryWrites.getState().writes, Date.now())).toBe(SAVED_SHOW_MS);
    vi.advanceTimersByTime(SAVED_SHOW_MS + 100);
    expect(useMemoryWrites.getState().writes).toEqual({});
  });
});
