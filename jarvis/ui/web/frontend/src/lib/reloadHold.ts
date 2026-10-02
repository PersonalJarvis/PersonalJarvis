/**
 * Things this window is doing that a reload would destroy.
 *
 * A live voice call is held by the page itself: the WebRTC media, the
 * microphone and the control socket all belong to this document. Reloading it
 * unmounts the call's owner, which sends `audio_stop` and hangs up mid-sentence.
 * That is exactly what happened on 2026-10-02: a sibling session rebuilt the
 * frontend during a 15-minute call, the bundle watch saw the user had not
 * touched keyboard or mouse for two seconds — they were talking — and reloaded
 * the window, which the backend recorded as a `client_stop` nobody pressed.
 *
 * A typed reply that is still streaming holds them too (main.tsx): a reload
 * mid-answer drops what was being written.
 *
 * Every automatic reload — the bundle watch and the preload recovery — asks
 * {@link reloadHeld}, both before it decides and again right before the
 * navigation (./safeReload), and waits while anything is held. The preload
 * recovery used to skip it, so opening a not-yet-loaded view during a call
 * after a rebuild still hung the call up. A reload the user asked for (a crash
 * card's button) does not wait.
 */

const holds = new Set<string>();

/** Hold (or release) automatic reloads on behalf of one owner. */
export function setReloadHold(owner: string, held: boolean): void {
  if (held) holds.add(owner);
  else holds.delete(owner);
}

/** True while any owner holds automatic reloads off. */
export function reloadHeld(): boolean {
  return holds.size > 0;
}
