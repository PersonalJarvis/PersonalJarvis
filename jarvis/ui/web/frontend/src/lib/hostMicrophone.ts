/**
 * The host's microphone, asked from a user gesture before the WebView opens it.
 *
 * In the embedded desktop window `getUserMedia` reaches the same macOS
 * microphone permission the native voice pipeline uses (the WebView lives in the
 * app's own process). macOS must hear about it from OUR explicit request, made
 * from the click: `POST /api/permissions/microphone/request` with the feature
 * `browser_voice`. That is also how the permission episode is opened, so a
 * denial gets the same explanation and the same one click to System Settings as
 * every other feature.
 *
 * A remote browser never calls this: the microphone it opens belongs to ITS
 * computer, and the only honest message there is the browser's site settings.
 */
import {
  PERMISSION_NEEDED_REASONS,
  type PermissionNeededReason,
} from "./permissionEvents";
import { requestPermission } from "./permissionsApi";

/** The feature the browser-voice start is attributed to (a `PERMISSION_FEATURES` token). */
export const BROWSER_VOICE_FEATURE = "browser_voice";

export type HostMicrophoneAnswer =
  /** Allowed (or macOS needs nothing): go on and open the stream. */
  | { kind: "granted" }
  /** macOS is asking right now: do not open the stream, let the person answer. */
  | { kind: "pending" }
  /** The person has to act (Settings switch, a restriction): do not open the stream. */
  | { kind: "blocked"; reason: PermissionNeededReason }
  /** The route failed (offline, rate limit): go on and let `getUserMedia` speak for itself. */
  | { kind: "unknown" };

const REASON_FOR_OUTCOME: Record<string, PermissionNeededReason> = {
  denied: "denied",
  needs_settings: "needs_settings",
  unavailable: "unavailable",
};

/** Ask macOS (from a gesture) for the microphone and say what comes next. Never throws. */
export async function askHostMicrophone(): Promise<HostMicrophoneAnswer> {
  try {
    const answer = await requestPermission("microphone", { feature: BROWSER_VOICE_FEATURE });
    if (answer.granted || answer.outcome === "granted" || answer.outcome === "not_required") {
      return { kind: "granted" };
    }
    if (answer.outcome === "pending") return { kind: "pending" };
    const known = (PERMISSION_NEEDED_REASONS as readonly string[]).includes(answer.reason);
    return {
      kind: "blocked",
      reason: known
        ? (answer.reason as PermissionNeededReason)
        : (REASON_FOR_OUTCOME[answer.outcome] ?? "unavailable"),
    };
  } catch {
    // A failed read must not make voice impossible: the stream open reports its own error.
    return { kind: "unknown" };
  }
}
