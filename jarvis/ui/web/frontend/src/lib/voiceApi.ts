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
