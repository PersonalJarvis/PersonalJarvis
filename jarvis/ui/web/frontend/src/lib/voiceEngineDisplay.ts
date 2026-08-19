import { prettyProviderName } from "@/lib/prettyProviderName";

/**
 * Which provider + model the voice surfaces should name right now.
 *
 * Pipeline and realtime are independent picks: someone can run OpenRouter as
 * the classic brain and Vertex AI Live on the duplex socket. Showing the
 * dormant one is the lie the mission deck told (OpenRouter on the header and
 * the orb while Vertex was doing the talking).
 *
 * One display, one rule, shared by the sidebar footer and the deck:
 *
 *   - Pipeline mode names the classic brain and its model.
 *   - Realtime mode names the realtime provider. A live session outranks the
 *     configured pick so a mid-call cross-family fallback (AP-22) is visible.
 *   - Never both. The compact header has room for the engine that will answer
 *     the next spoken turn; both picks stay on API Keys.
 */

export type VoiceEngineTier = "pipeline" | "realtime";

export interface VoiceEngineDisplay {
  tier: VoiceEngineTier;
  providerId: string;
  providerLabel: string;
  model: string;
}

export interface VoiceEngineDisplayInput {
  mode: string;
  activeProvider: string | null;
  activeProviderLabel: string | null;
  activeModel: string | null;
  sessionActive: boolean;
  activeSessionMode: "pipeline" | "realtime" | null;
  activeSessionProvider: string;
  activeSessionModel: string;
  brainProvider: string;
  brainModel: string;
}

export function resolveVoiceEngineDisplay(
  input: VoiceEngineDisplayInput,
): VoiceEngineDisplay {
  const realtime = input.mode === "realtime";
  const liveSession =
    realtime &&
    input.sessionActive &&
    input.activeSessionMode === "realtime" &&
    Boolean(input.activeSessionProvider);

  if (realtime) {
    const providerId = liveSession
      ? input.activeSessionProvider
      : input.activeProvider ?? "";
    const providerLabel = realtimeProviderLabel(input, liveSession, providerId);
    const model = liveSession
      ? input.activeSessionModel
      : input.activeModel ?? "";
    return {
      tier: "realtime",
      providerId,
      providerLabel: providerLabel || "—",
      model,
    };
  }

  return {
    tier: "pipeline",
    providerId: input.brainProvider,
    providerLabel: prettyProviderName(input.brainProvider),
    model: input.brainModel,
  };
}

/** Registry label when we have it; pretty-id fallback when the session crossed families. */
function realtimeProviderLabel(
  input: VoiceEngineDisplayInput,
  liveSession: boolean,
  providerId: string,
): string {
  if (liveSession) {
    if (
      input.activeSessionProvider === input.activeProvider &&
      input.activeProviderLabel?.trim()
    ) {
      return input.activeProviderLabel.trim();
    }
    return prettyProviderName(input.activeSessionProvider);
  }
  return input.activeProviderLabel?.trim() || prettyProviderName(providerId);
}
