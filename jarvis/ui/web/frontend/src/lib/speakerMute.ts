/**
 * Window event fired when the assistant's voice went silent or audible from
 * anywhere (the desktop pet's speaker disc, another window). `detail` is
 * `{ muted: boolean }`. Relayed by `useWebSocket` from the backend's
 * `VoiceSpeakerMuteChanged`, the one authoritative source.
 *
 * Its own module rather than a member of `lib/voiceApi.ts`: tests replace the
 * whole API module with a mock, and an event NAME has nothing to mock.
 */
export const SPEAKER_MUTE_EVENT = "jarvis:speaker-mute-changed";
