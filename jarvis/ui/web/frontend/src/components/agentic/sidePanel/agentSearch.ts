import type { WorkspacePaneRow } from "@/lib/agenticIdeApi";

export interface AgentSearchDocument {
  id: string;
  texts: string[];
}

export interface AgentSearchMatch {
  id: string;
  score: number;
}

export type AgentSearchRequest =
  | { type: "search"; id: number; query: string; documents: AgentSearchDocument[] }
  | { type: "cancel"; id: number };

export type AgentSearchResponse =
  | { type: "loading" | "searching" | "error"; id: number }
  | { type: "result"; id: number; matches: AgentSearchMatch[] };

// Absolute relevance matters: even the nearest neighbour can be unrelated.
// Calibrated with multilingual paraphrases and unrelated queries, not a top-k.
export const MIN_AGENT_SIMILARITY = 0.29;
export const AGENT_SEARCH_MODEL = "Xenova/paraphrase-multilingual-MiniLM-L12-v2";
export const AGENT_SEARCH_REVISION = "2c4055b12046f11709e9df2c122e59ffbdc2f900";

export function agentSearchDocument(pane: WorkspacePaneRow, title: string): AgentSearchDocument {
  return {
    id: pane.history_id,
    // Keep the task separate from its title so a long prompt cannot drown it out.
    // Provider names, folder paths and status are not evidence of a task match.
    texts: [...new Set([title, pane.recap, pane.last_prompt].map((text) => text.trim().slice(0, 1000)))].filter(Boolean),
  };
}

export function cosineSimilarity(left: readonly number[], right: readonly number[]): number {
  if (!left.length || left.length !== right.length) return 0;
  let dot = 0;
  let leftNorm = 0;
  let rightNorm = 0;
  for (let index = 0; index < left.length; index += 1) {
    dot += left[index] * right[index];
    leftNorm += left[index] ** 2;
    rightNorm += right[index] ** 2;
  }
  return leftNorm && rightNorm ? dot / Math.sqrt(leftNorm * rightNorm) : 0;
}

export function relevantAgentMatches(matches: AgentSearchMatch[]): AgentSearchMatch[] {
  const best = Math.max(0, ...matches.map(({ score }) => Number.isFinite(score) ? score : 0));
  const threshold = Math.max(MIN_AGENT_SIMILARITY, best * 0.75);
  return matches
    .filter(({ score }) => Number.isFinite(score) && score >= threshold)
    .sort((left, right) => right.score - left.score);
}
