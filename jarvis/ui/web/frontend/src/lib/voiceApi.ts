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
