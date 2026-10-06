// The composer's side of jarvis/agent_chat/effort.py: which levels a model
// offers, and how a pick folds onto them. The backend applies the same fold
// before it launches a turn (`effort_for_model`), so the level the picker
// shows is the level the agent runs on.
import type { CuratedModel } from "@/lib/agentChatApi";

/** The universal ordering (effort.py `ORDER`); every ladder is a sub-sequence. */
export const EFFORT_ORDER: readonly string[] = ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"];

/**
 * The levels on offer for `modelId`: the model's own when the catalog lists
 * them (agy's Pro: low/high; Opus 4.6: no xhigh; Haiku 4.5: none), else the
 * provider's ladder.
 */
export function effortLadder(
  provider: { effort_levels?: string[] } | null | undefined,
  models: readonly CuratedModel[],
  modelId: string,
): string[] {
  if (!provider) return [];
  const model = models.find((m) => m.id === modelId);
  if (model && Array.isArray(model.efforts)) return model.efforts;
  return provider.effort_levels ?? [];
}

/**
 * Fold `level` onto `ladder`, preferring the lower neighbour — a model that
 * cannot go as high never silently costs more. `""` (the agent's default) on
 * a ladder that offers no default becomes `fallback`, the provider's own
 * default level. An empty ladder means the model has no effort knob: `""`.
 */
export function snapEffort(level: string, ladder: readonly string[], fallback: string): string {
  if (ladder.includes(level)) return level;
  const offered = ladder.filter(Boolean);
  if (offered.length === 0) return "";
  if (!level) return fallback ? snapEffort(fallback, ladder, "") : "";
  const idx = EFFORT_ORDER.indexOf(level);
  if (idx < 0) return offered[0];
  const lower = offered.filter((l) => {
    const at = EFFORT_ORDER.indexOf(l);
    return at >= 0 && at <= idx;
  });
  return lower.length ? lower[lower.length - 1] : offered[0];
}
