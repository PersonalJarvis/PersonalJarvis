import type { SubAgentNode } from "@/store/jarvisAgents";

/** i18n keys for the closed MissionErrorClass vocabulary (parity-tested
 * against jarvis/missions/events.py MISSION_ERROR_CLASSES). Legacy/unknown
 * classes fall back to the raw error text. */
const ERROR_CLASS_KEYS: Record<string, string> = {
  provider_auth: "subagents_view.error_class.provider_auth",
  provider_quota: "subagents_view.error_class.provider_quota",
  provider_unreachable: "subagents_view.error_class.provider_unreachable",
  worker_timeout: "subagents_view.error_class.worker_timeout",
};

/** Human failure label for a sub-agent node: the i18n message for a known
 * error_class (with the upstream detail in parentheses), else the raw error
 * text, else null (no failure to show). */
export function failureLabel(
  node: Pick<SubAgentNode, "error" | "error_class">,
  t: (key: string) => string,
): string | null {
  const key = node.error_class ? ERROR_CLASS_KEYS[node.error_class] : undefined;
  if (key) {
    const msg = t(key);
    return node.error ? `${msg} (${node.error})` : msg;
  }
  return node.error ?? null;
}
