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
 * (`phase = "os_dialog"`) is not a block either. `not_determined` is not a block
 * either: nothing was denied and macOS was never asked, so "fix it in System
 * Settings" would be the wrong remedy (the pane lists no entry yet); see
 * {@link useVoiceNotAskedByPermission}. Off macOS there are no episodes, so it is
 * always false.
 */
export function useVoiceBlockedByPermission(): boolean {
  return usePermissionsStore((state) => microphoneEpisodeKind(state.episodes) === "blocked");
}

/**
 * The microphone was never answered (a wake word that booted without the
 * permission, or after a reset): not a denial, and the remedy is to be asked,
 * not to visit System Settings.
 */
export function useVoiceNotAskedByPermission(): boolean {
  return usePermissionsStore((state) => microphoneEpisodeKind(state.episodes) === "not_asked");
}

function microphoneEpisodeKind(
  episodes: ReturnType<typeof usePermissionsStore.getState>["episodes"],
): "blocked" | "not_asked" | null {
  const open = episodes.filter(
    (episode) =>
      episode.phase === "blocked" &&
      episode.reason !== "restart_hint" &&
      episode.permissions.includes("microphone"),
  );
  if (open.some((episode) => episode.reason !== "not_determined")) return "blocked";
  return open.length > 0 ? "not_asked" : null;
}
