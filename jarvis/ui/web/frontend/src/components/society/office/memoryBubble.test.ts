import { describe, expect, it } from "vitest";
import { memoryLine, type MemoryLabels } from "./memoryBubble";

const LABELS: MemoryLabels = {
  updating: "Updating memory", updatingFile: "Updating memory · {0}",
  saved: "Memory saved", savedFile: "Saved to {0}",
  failed: "Memory not saved", failedFile: "Couldn't save {0}",
};

describe("memoryLine", () => {
  it("names the file being written", () => {
    expect(memoryLine({ phase: "pending", files: ["MEMORY.md"], atMs: 0 }, LABELS)).toEqual({ phase: "pending", text: "Updating memory · MEMORY.md" });
  });

  it("names every file of concurrent writes", () => {
    expect(memoryLine({ phase: "saved", files: ["USER.md", "SOUL.md"], atMs: 0 }, LABELS).text).toBe("Saved to USER.md, SOUL.md");
  });

  it("falls back to a plain line while the file is not known", () => {
    expect(memoryLine({ phase: "pending", files: [], atMs: 0 }, LABELS).text).toBe("Updating memory");
    expect(memoryLine({ phase: "failed", files: [], atMs: 0 }, LABELS).text).toBe("Memory not saved");
  });

  it("states a failure as a failure", () => {
    expect(memoryLine({ phase: "failed", files: ["SOUL.md"], atMs: 0 }, LABELS)).toEqual({ phase: "failed", text: "Couldn't save SOUL.md" });
  });
});
