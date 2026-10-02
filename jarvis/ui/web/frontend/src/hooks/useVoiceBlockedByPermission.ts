import { usePermissionsStore } from "@/store/permissions";

/**
 * Whether voice is blocked by a macOS permission RIGHT NOW: an open episode that
 * needs the microphone and is waiting on the person (`phase = "blocked"`), of
 * any origin. A wake word that boots without the microphone opens a
 * background-origin episode (no card, by design), so this is how the sidebar's
 * voice status keeps a dead wake word from being silent.
 *
 * `restart_hint` is excluded: access is granted and only a restart is pending,
 * so voice is not blocked, just not yet applied. macOS asking by itself
 * (`phase = "os_dialog"`) is not a block either. Off macOS there are no
 * episodes, so it is always false.
 */
export function useVoiceBlockedByPermission(): boolean {
  return usePermissionsStore((state) =>
    state.episodes.some(
      (episode) =>
        episode.phase === "blocked" &&
        episode.reason !== "restart_hint" &&
        episode.permissions.includes("microphone"),
    ),
  );
}
