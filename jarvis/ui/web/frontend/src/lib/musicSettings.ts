/**
 * Music settings vocabulary — the TypeScript mirror (five-layer pattern L4) of
 * `jarvis/core/music_constants.py`. A Python parity test regex-extracts the
 * unions below and fails when they drift from the tuples there, so a value
 * cannot be spelled one way in the config and another in the UI.
 */

/** Which connector a music request that names no service goes to. */
export const MUSIC_SERVICES = ["auto", "spotify", "youtube_music"] as const;
export type MusicService = (typeof MUSIC_SERVICES)[number];

/** Where YouTube Music plays. */
export const MUSIC_PLAYBACK_MODES = ["background", "browser"] as const;
export type MusicPlaybackMode = (typeof MUSIC_PLAYBACK_MODES)[number];

export function isMusicService(value: unknown): value is MusicService {
  return (
    typeof value === "string" && (MUSIC_SERVICES as readonly string[]).includes(value)
  );
}

export function isMusicPlaybackMode(value: unknown): value is MusicPlaybackMode {
  return (
    typeof value === "string" &&
    (MUSIC_PLAYBACK_MODES as readonly string[]).includes(value)
  );
}

/** What GET /api/settings/music answers. */
export interface MusicSettings {
  preferred_service: MusicService;
  playback: MusicPlaybackMode;
  service_options: MusicService[];
  playback_options: MusicPlaybackMode[];
  connected: string[];
  background_player_available: boolean;
}
