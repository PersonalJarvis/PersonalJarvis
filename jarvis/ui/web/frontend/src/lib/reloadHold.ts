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
 * Automatic reloads ask {@link reloadHeld} first and wait while anything is
 * held. A reload the user asked for (a crash card's button) does not.
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
