import type { Counter } from "./types";

export interface MeasuredRate { numerator: Counter | null; denominator: Counter | null; rate: number | null }
export interface SkillProfile {
  domain: string; level: number; reliability: number; uncertainty: number; samples: Counter;
  verified_tasks: Counter; difficulty_counts: Record<string, Counter>; credits: number; next_level_credits: number | null;
  acceptance: MeasuredRate; regression: MeasuredRate; rollback: MeasuredRate;
}
export interface RatingChange {
  id: string; task_id: string; evidence_key: string; domain: string; previous_level: number;
  level: number; credit_delta: number; reason: string; created_at: number;
}
export interface AgentSkills {
  team_id: string; agent_id: string; level: number; profiles: SkillProfile[];
  history: RatingChange[]; has_more: boolean;
}
export interface ContributionRecheck {
  id: string; team_id: string; task_id: string; contribution_id: string;
  state: "passed" | "regression" | "fabrication" | "inconclusive" | "unsupported" | "running";
  reason: string; created_at: number; finished_at: number | null;
  evidence_ids: string[]; rating_id: string | null;
}
const counter = (value: unknown): value is Counter => typeof value === "string" && /^\d{1,31}$/.test(value);
export function validRate(value: MeasuredRate): boolean {
  if (!value) return false;
  if (value.numerator === null || value.denominator === null) return value.numerator === null && value.denominator === null && value.rate === null;
  return counter(value.numerator) && counter(value.denominator) && BigInt(value.numerator) <= BigInt(value.denominator)
    && (BigInt(value.denominator) === 0n ? value.rate === null : typeof value.rate === "number" && Number.isFinite(value.rate) && value.rate >= 0 && value.rate <= 1);
}
export function validSkills(value: AgentSkills, teamId: string, agentId: string): boolean {
  return value?.team_id === teamId && value.agent_id === agentId && Array.isArray(value.profiles)
    && value.profiles.length <= 50 && Array.isArray(value.history) && value.history.length <= 50
    && value.profiles.every(profile => typeof profile.domain === "string" && Number.isInteger(profile.level)
      && Number.isFinite(profile.reliability) && Number.isFinite(profile.uncertainty)
      && [profile.samples, profile.verified_tasks, ...Object.values(profile.difficulty_counts ?? {})].every(counter)
      && [profile.acceptance, profile.regression, profile.rollback].every(validRate));
}
