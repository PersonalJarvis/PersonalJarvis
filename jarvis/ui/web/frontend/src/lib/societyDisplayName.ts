/**
 * Display name for society agents — the lead follows the wake word.
 *
 * The backend roster keeps the lead's internal identity as "jarvis"
 * (LEAD_AGENT_ID, session id `society:jarvis`, avatar, rules). The name the
 * person sees, however, is the wake-word-derived assistant name from the
 * global store (e.g. "Hanna" for "Hey Hanna", "Assistant" when nothing is
 * configured). Every other agent keeps its roster name.
 *
 * Internal identifiers (files, classes, i18n keys, agent ids) keep their
 * existing naming; only display strings go through this helper — the same
 * split as `lib/agentBrand.ts`.
 */
import { useEventStore } from "@/store/events";

import { NEUTRAL_ASSISTANT_NAME } from "./assistantNameCache";

export interface SocietyDisplayAgent {
  tier?: string | null;
  name: string;
}

export function societyDisplayName(
  agent: SocietyDisplayAgent,
  assistantName: string,
): string {
  if (agent.tier === "lead") {
    const trimmed = (assistantName || "").trim();
    return trimmed || NEUTRAL_ASSISTANT_NAME;
  }
  return agent.name;
}

/**
 * Reactive hook form — re-renders when the wake-word name changes, so the
 * lead hero live-updates after a wake-word save like every other `{name}`
 * string.
 */
export function useSocietyDisplayName(agent: SocietyDisplayAgent): string {
  const assistantName = useEventStore((s) => s.assistantName);
  return societyDisplayName(agent, assistantName);
}

/** Imperative form for non-render contexts (toasts, event handlers). */
export function societyDisplayNameNow(agent: SocietyDisplayAgent): string {
  return societyDisplayName(agent, useEventStore.getState().assistantName);
}
