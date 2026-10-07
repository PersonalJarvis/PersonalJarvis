/**
 * The voice call this document's browser holds itself.
 *
 * On a host with no speech pipeline of its own (a VPS, `jarvis serve`) the
 * browser that presses Start holds the call: it opens /ws/audio, streams the
 * microphone and plays the reply. The one control that owns that socket
 * (components/voice/BrowserRealtimeControl, mounted once per document)
 * registers here, so every Start/Stop surface reaches it through
 * useVoiceCall instead of growing a second copy of the call logic.
 *
 * Plain module state, like voiceInputLevel.ts: the flag is read on demand by
 * the start/stop path and by the state resync, and never rendered.
 */

export interface BrowserVoiceCallOwner {
  start(): void;
  stop(): void;
}

let owner: BrowserVoiceCallOwner | null = null;
let live = false;

/** Register this document's call owner; the returned function unregisters it. */
export function registerBrowserVoiceCallOwner(next: BrowserVoiceCallOwner): () => void {
  owner = next;
  return () => {
    if (owner !== next) return;
    owner = null;
    live = false;
  };
}

/** The owner reports whether its browser-held call is connecting or open. */
export function setBrowserVoiceCallLive(value: boolean): void {
  live = value;
}

/** Whether this document holds a browser call right now. */
export function browserVoiceCallLive(): boolean {
  return live;
}

/** Start a browser-held call. `false` when no owner is mounted here. */
export function startBrowserVoiceCall(): boolean {
  if (owner === null) return false;
  owner.start();
  return true;
}

/** End this document's browser-held call. `false` when none is open. */
export function stopBrowserVoiceCall(): boolean {
  if (owner === null || !live) return false;
  owner.stop();
  return true;
}
