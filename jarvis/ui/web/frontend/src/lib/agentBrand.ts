/**
 * Public display brand of the agent system, derived from the assistant name
 * (which is itself derived from the configured wake phrase, see
 * `useAssistantNameSeed` / backend `resolve_assistant_name`):
 * "Ruben" -> "Ruben-Agent", "Athena" -> "Athena-Agent".
 *
 * The 2026-07-17 rebrand: the user-visible agent-system name follows WHATEVER
 * wake word the user configured — it is never a hardcoded product name. Code
 * identifiers (files, classes, i18n keys) keep the internal "JarvisAgent"
 * naming; only display strings go through this helper or the `{name}` i18n
 * token. Mirror of the backend `jarvis.brain.assistant_name.agent_brand`.
 */
import { useEventStore } from "@/store/events";

const NEUTRAL_NAME = "Assistant";

export function agentBrand(assistantName: string): string {
  const name = (assistantName || "").trim() || NEUTRAL_NAME;
  return `${name}-Agent`;
}

export function agentsBrand(assistantName: string): string {
  return `${agentBrand(assistantName)}s`;
}

/**
 * Reactive hook form — re-renders the consumer when the assistant name
 * changes (a wake-word save), so the brand live-updates like every other
 * `{name}` string. Same hook/imperative split as `useT` vs `translate`.
 */
export function useAgentBrand(): string {
  return agentBrand(useEventStore((s) => s.assistantName));
}

/** Imperative form for non-render contexts (toasts, event handlers). */
export function agentBrandNow(): string {
  return agentBrand(useEventStore.getState().assistantName);
}
