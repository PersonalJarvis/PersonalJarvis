import { describe, expect, it } from "vitest";
import { agentSearchDocument, cosineSimilarity, relevantAgentMatches } from "./agentSearch";
import type { WorkspacePaneRow } from "@/lib/agenticIdeApi";

describe("agent relevance", () => {
  it("returns no result when even the closest terminal is unrelated", () => {
    expect(relevantAgentMatches([{ id: "best-of-bad", score: 0.2 }, { id: "broken", score: NaN }])).toEqual([]);
  });
  it("ranks relevance across statuses and leaves source scores untouched", () => {
    const scores = [{ id: "done", score: 0.85 }, { id: "working", score: 0.9 }, { id: "unrelated", score: 0.4 }];
    expect(relevantAgentMatches(scores).map(({ id }) => id)).toEqual(["working", "done"]);
    expect(scores[0].id).toBe("done");
  });
  it("uses task content without conflating the provider or folder with the task", () => {
    const document = agentSearchDocument({ history_id: "T1@w1", recap: "Fix login", last_prompt: "Renew expired credentials", agent: "codex", folder: "/private/project" } as WorkspacePaneRow, "Fix login");
    expect(document).toEqual({ id: "T1@w1", texts: ["Fix login", "Renew expired credentials"] });
  });
  it("handles invalid and zero vectors without accidentally matching", () => {
    expect(cosineSimilarity([], [])).toBe(0);
    expect(cosineSimilarity([1], [1, 2])).toBe(0);
    expect(cosineSimilarity([0, 0], [1, 1])).toBe(0);
    expect(cosineSimilarity([1, 0], [0, 1])).toBe(0);
    expect(cosineSimilarity([1, 1], [2, 2])).toBeCloseTo(1);
  });
});
