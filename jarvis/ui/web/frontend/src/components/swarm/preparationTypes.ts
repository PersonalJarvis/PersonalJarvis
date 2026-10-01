import type { TaskSpec, TeamRecord } from "./types";

export type PreparationState = "clarifying" | "planning" | "ready" | "failed" | "launched";
export interface PreparationQuestion {
  id: string;
  prompt: string;
  choices?: string[];
  hint?: string;
}
export interface PreparationPlan {
  goal: string;
  acceptance: string;
  summary: string;
  assumptions: string[];
  exclusions: string[];
  tasks: TaskSpec[];
  remaining_decomposition: boolean;
}
export interface PreparationView {
  team: TeamRecord;
  revision: number;
  state: PreparationState;
  busy: boolean;
  questions: PreparationQuestion[];
  answers: Record<string, string>;
  plan: PreparationPlan | null;
  digest: string;
  error: string;
}
