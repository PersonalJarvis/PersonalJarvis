/**
 * The one client-side writer for `[voice].mode`.
 *
 * It lives outside `useVoiceMode` so callers that are not React hooks can pin
 * the engine through the SAME route the segmented Pipeline|Realtime switch
 * uses. The onboarding local path needs exactly that from a plain click
 * handler, and a second hand-rolled fetch is precisely where the two would
 * drift apart (persist flag, error shape, endpoint).
 */

/** Engine modes the backend accepts (`settings_routes._VOICE_MODES`). */
export type VoiceEngineModeValue = "pipeline" | "realtime";

/**
 * PUT `[voice].mode`. Persisted by default — an engine choice is a standing
 * decision that has to survive the next restart, not a session-only nudge.
 *
 * Throws with the backend's own message on a refusal (e.g. pinning realtime
 * while no realtime provider is ready), so the caller can render the reason
 * verbatim instead of inventing one.
 */
export async function putVoiceMode(
  mode: VoiceEngineModeValue,
  { persist = true }: { persist?: boolean } = {},
): Promise<unknown> {
  const res = await fetch("/api/settings/voice-mode", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ mode, persist }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
