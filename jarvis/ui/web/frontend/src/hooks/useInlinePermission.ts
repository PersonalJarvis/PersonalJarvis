import { useEffect } from "react";
import { usePermissionsStore } from "@/store/permissions";
import type { PromptEpisode, ResolvedNote } from "@/lib/permissionPrompts";

/** What an inline surface learns about its feature's permission episode. */
export interface InlinePermission {
  /**
   * The newest open episode of this feature, whatever its origin or phase, or
   * null. `phase === "os_dialog"` means macOS is asking right now; `"blocked"`
   * means the person has to act (Settings switch, restart, a restriction).
   */
  episode: PromptEpisode | null;
  /**
   * The newest ended episode of this feature (kept for a few seconds by the
   * store). `granted` drives "Microphone allowed - press again" style notes.
   */
  resolved: ResolvedNote | null;
  /** Hide the card-style note for this episode until it says something new. */
  dismiss: () => void;
}

/**
 * Register an inline surface for a feature and read its episode.
 *
 * A surface that explains a missing permission in place (the dictation
 * composer, the wake-word panel, the mute-music row, the shortcuts hint) calls
 * this while it is mounted. The floating card consults the same ref-counted
 * registry and does not repeat a feature that already has a surface on screen,
 * so the person sees one explanation, in the place they are looking at.
 *
 * `enabled = false` reads without registering (a surface that is mounted but
 * not currently showing its note must not silence the card).
 */
export function useInlinePermission(feature: string, enabled = true): InlinePermission {
  useEffect(() => {
    if (!enabled) return undefined;
    return usePermissionsStore.getState().registerInline(feature);
  }, [feature, enabled]);

  const episode = usePermissionsStore((state) => {
    let best: PromptEpisode | null = null;
    for (const entry of state.episodes) {
      if (entry.feature === feature && (best === null || entry.updatedAt > best.updatedAt)) {
        best = entry;
      }
    }
    return best;
  });
  const resolved = usePermissionsStore(
    (state) => state.resolved.find((note) => note.feature === feature) ?? null,
  );

  return {
    episode,
    resolved,
    dismiss: () => {
      if (episode) usePermissionsStore.getState().dismiss(episode.key);
    },
  };
}
