import { request, teamPath } from "./api";
import type { PreparationView } from "./preparationTypes";
import type { TeamCreate, TeamRecord } from "./types";

const jsonPost = (body: unknown, signal?: AbortSignal): RequestInit => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
function check(value: PreparationView, teamId?: string): PreparationView {
  if (!value || (teamId && value.team?.id !== teamId) || !value.team?.id || !Number.isSafeInteger(value.revision) || value.revision < 0 || typeof value.busy !== "boolean" || !["clarifying", "planning", "ready", "failed", "launched"].includes(value.state) || !Array.isArray(value.questions) || value.questions.length > 3 || !value.answers || typeof value.answers !== "object" || typeof value.digest !== "string" || typeof value.error !== "string") throw new Error("Invalid or mismatched Swarm preparation");
  if (value.questions.some(question => !question || typeof question.id !== "string" || typeof question.prompt !== "string" || (question.choices !== undefined && (!Array.isArray(question.choices) || question.choices.some(choice => typeof choice !== "string"))))) throw new Error("Invalid Swarm clarification questions");
  if (value.plan && (!Array.isArray(value.plan.tasks) || value.plan.tasks.length < 1 || value.plan.tasks.length > 32 || !Array.isArray(value.plan.assumptions) || !Array.isArray(value.plan.exclusions))) throw new Error("Invalid or unbounded Swarm plan");
  return value;
}
export async function createPreparationTeam(spec: TeamCreate): Promise<TeamRecord> {
  return check(await request<PreparationView>("/api/swarm/preparations", jsonPost(spec))).team;
}
export async function readPreparation(teamId: string, signal?: AbortSignal): Promise<PreparationView> {
  return check(await request<PreparationView>(`${teamPath(teamId)}/preparation`, { signal }), teamId);
}
export async function beginPreparation(team: TeamRecord, requestKey: string, signal?: AbortSignal): Promise<PreparationView> {
  return check(await request<PreparationView>(`${teamPath(team.id)}/preparation`, jsonPost({ expected_storage_generation: team.storage_generation ?? "", request_key: requestKey }, signal)), team.id);
}
export async function answerPreparation(view: PreparationView, answers: Record<string, string>, requestKey: string, signal?: AbortSignal): Promise<PreparationView> {
  return check(await request<PreparationView>(`${teamPath(view.team.id)}/preparation/answers`, jsonPost({ expected_revision: view.revision, expected_storage_generation: view.team.storage_generation ?? "", answers, request_key: requestKey }, signal)), view.team.id);
}
export async function launchPreparation(view: PreparationView, requestKey: string, signal?: AbortSignal): Promise<PreparationView> {
  return check(await request<PreparationView>(`${teamPath(view.team.id)}/launch`, jsonPost({ expected_revision: view.revision, expected_storage_generation: view.team.storage_generation ?? "", digest: view.digest, request_key: requestKey }, signal)), view.team.id);
}
