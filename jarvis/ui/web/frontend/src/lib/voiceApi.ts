// REST client for the voice orb. Plain same-origin fetch (mirrors
// agenticIdeApi), `no-store` so a WebView cannot answer with yesterday's state.

export interface VoiceCallAnswer {
  armed: boolean;
}

export interface VoiceHangupAnswer {
  stopped: boolean;
}

async function post<T>(path: string): Promise<T> {
  const res = await fetch(path, { method: "POST", cache: "no-store" });
  if (!res.ok) {
    let detail = "";
    try {
      detail = ((await res.json()) as { detail?: string }).detail ?? "";
    } catch {
      /* a non-JSON error body — the status line below still names the failure */
    }
    if (res.status === 503) {
      throw new Error("Voice is not running on this computer.");
    }
    throw new Error(detail || `Voice request failed (${res.status}).`);
  }
  return (await res.json()) as T;
}

/** Start a voice conversation — the click-shaped wake word. */
export function requestVoiceCall(): Promise<VoiceCallAnswer> {
  return post<VoiceCallAnswer>("/api/voice/call");
}

/** End the running voice conversation — same contract as the hangup key. */
export function requestVoiceHangup(): Promise<VoiceHangupAnswer> {
  return post<VoiceHangupAnswer>("/api/voice/hangup");
}

/**
 * Whether the configured realtime transport needs a browser-supplied WebRTC
 * offer at all (`requires_webrtc_offer` from GET /api/settings/voice-mode).
 *
 * A capability, never a provider id (AP-21). `null` means the backend could not
 * be asked and is NOT the same as `false`: an unknown answer must keep every
 * genuine failure visible, because hiding a real blocker is the worse failure.
 */
export async function fetchRealtimeOfferRequirement(): Promise<boolean | null> {
  try {
    const res = await fetch("/api/settings/voice-mode", { cache: "no-store" });
    if (!res.ok) return null;
    const body = (await res.json()) as { requires_webrtc_offer?: unknown };
    return typeof body.requires_webrtc_offer === "boolean"
      ? body.requires_webrtc_offer
      : null;
  } catch (error) {
    // Deliberately degraded to "unknown" rather than swallowed: the caller
    // treats null as "cannot rule the requirement out" and still reports.
    console.info("Realtime offer requirement could not be read.", error);
    return null;
  }
}

export interface TtsVolumeState {
  volume: number;
}

/** The master TTS volume — 0.0–1.0, the same value the settings slider shows. */
export async function fetchTtsVolume(): Promise<TtsVolumeState> {
  const res = await fetch("/api/settings/tts-volume", { cache: "no-store" });
  if (!res.ok) throw new Error(`Voice volume request failed (${res.status}).`);
  return (await res.json()) as TtsVolumeState;
}

/**
 * Set the master TTS volume. `persist: false` keeps the change session-only —
 * the voice bubble's speaker toggle must never write a mute into jarvis.toml
 * that would survive a restart the user has long forgotten about.
 */
export async function setTtsVolume(
  volume: number,
  persist: boolean,
): Promise<void> {
  const res = await fetch("/api/settings/tts-volume", {
    method: "PUT",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ volume, persist }),
  });
  if (!res.ok) {
    let detail = "";
    try {
      detail = ((await res.json()) as { detail?: string }).detail ?? "";
    } catch {
      /* a non-JSON error body — the status line below still names the failure */
    }
    throw new Error(detail || `Voice volume change failed (${res.status}).`);
  }
}

export interface VoiceRuntimeState {
  /** Is a speech pipeline running on this host at all? */
  available: boolean;
  /** The fine-grained state every voice surface renders, straight from the
   *  supervisor. `"idle"` whenever no session is running. */
  voiceState: string;
}

/**
 * The backend's own word on what voice is doing RIGHT NOW.
 *
 * `SystemStateChanged` is a one-shot event, so a window that missed one — a
 * dropped socket, a backend restart, a fresh mount — keeps whatever it heard
 * last with nothing to correct it. This is the REST mirror the surfaces
 * reconcile against (hooks/useVoiceStateResync).
 *
 * `null` means "could not be asked" and is NOT "idle": an unknown answer must
 * never overwrite a live state the events are keeping correct.
 */
export async function fetchVoiceRuntimeState(): Promise<VoiceRuntimeState | null> {
  try {
    const res = await fetch("/api/voice/state", { cache: "no-store" });
    if (!res.ok) return null;
    const body = (await res.json()) as {
      available?: unknown;
      state?: unknown;
      voice_state?: unknown;
    };
    const available = body.available === true;
    if (typeof body.voice_state === "string") {
      return { available, voiceState: body.voice_state };
    }
    // A backend from before the field existed still answers the coarse
    // question honestly: an idle pipeline IS "no session runs", which is the
    // whole correction this is read for. Anything else stays "unknown" so a
    // live call is never touched on a guess.
    if (typeof body.state === "string") {
      return { available, voiceState: body.state === "idle" ? "idle" : "unknown" };
    }
    return null;
  } catch (error) {
    // Offline / headless / the route missing on an older backend: the caller
    // treats null as "no answer" and leaves the store exactly as it is.
    console.info("Voice runtime state could not be read.", error);
    return null;
  }
}
