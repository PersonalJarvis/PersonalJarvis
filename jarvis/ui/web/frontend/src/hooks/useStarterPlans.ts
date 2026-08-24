/**
 * Starter plans ("one key and you are done") and the readiness note.
 *
 * A plan is data from the backend catalog: a voice mode plus one provider per
 * surface, all on one or two key families. Applying it means calling the
 * ordinary switch routes in order — nothing here knows how a switch works.
 */
import { putVoiceMode } from "@/lib/voiceEngineMode";
import {
  switchBrainProvider,
  switchComputerUseProvider,
  switchRealtimeProvider,
  switchSttProvider,
  switchSubagentProvider,
  switchTtsProvider,
} from "@/hooks/useProviders";

export interface StarterPlanKeySlot {
  family: string;
  slot: string;
  label: string;
  present: boolean;
}

export interface StarterPlan {
  id: string;
  label: string;
  summary: string;
  mode: "pipeline" | "realtime";
  recommended: boolean;
  assignments: Record<string, string>;
  key_slots: StarterPlanKeySlot[];
  keys_complete: boolean;
  ready_sections: string[];
}

export interface StarterPlansResponse {
  plans: StarterPlan[];
  selected: string | null;
  custom_id: string;
}

export interface ReadinessResponse {
  mode: "pipeline" | "realtime";
  required: string[];
  sections: Record<string, { status: string; reason?: string; detail?: string }>;
  ready: boolean;
  celebrated: boolean;
  starter_plan: string | null;
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

export async function getStarterPlans(): Promise<StarterPlansResponse> {
  return json(await fetch("/api/setup/starter-plans", { cache: "no-store" }));
}

export async function selectStarterPlan(planId: string): Promise<void> {
  await json(await fetch(`/api/setup/starter-plans/${encodeURIComponent(planId)}`, { method: "POST" }));
}

export async function getReadiness(refresh = false): Promise<ReadinessResponse> {
  return json(
    await fetch(`/api/setup/readiness${refresh ? "?refresh=true" : ""}`, { cache: "no-store" }),
  );
}

export async function markReadyCelebrated(): Promise<void> {
  await json(await fetch("/api/setup/readiness/celebrated", { method: "POST" }));
}

/** Surfaces in the order a plan is applied; the voice mode goes last. */
const APPLY_ORDER = ["brain", "computer-use", "subagent", "tts", "stt", "realtime"] as const;

export interface ApplyPlanOutcome {
  applied: string[];
  failed: { surface: string; error: string }[];
  modeSet: boolean;
}

/**
 * Point every surface of the plan at its provider, then pin the voice mode.
 * One surface failing does not stop the others — the readiness note only
 * appears once everything answers, so a partial apply stays visible.
 */
export async function applyStarterPlan(plan: StarterPlan): Promise<ApplyPlanOutcome> {
  const out: ApplyPlanOutcome = { applied: [], failed: [], modeSet: false };
  for (const surface of APPLY_ORDER) {
    const provider = plan.assignments[surface];
    if (!provider) continue;
    try {
      switch (surface) {
        case "brain":
          await switchBrainProvider(provider);
          break;
        case "computer-use":
          await switchComputerUseProvider(provider);
          break;
        case "subagent":
          await switchSubagentProvider(provider);
          break;
        case "tts":
          await switchTtsProvider(provider);
          break;
        case "stt":
          await switchSttProvider(provider);
          break;
        case "realtime":
          await switchRealtimeProvider(provider);
          break;
      }
      out.applied.push(surface);
    } catch (e) {
      out.failed.push({ surface, error: e instanceof Error ? e.message : String(e) });
    }
  }
  try {
    await putVoiceMode(plan.mode);
    out.modeSet = true;
  } catch (e) {
    out.failed.push({ surface: "voice-mode", error: e instanceof Error ? e.message : String(e) });
  }
  window.dispatchEvent(new CustomEvent("jarvis:provider-config-changed"));
  return out;
}
