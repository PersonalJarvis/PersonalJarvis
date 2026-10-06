/**
 * An agent that has nothing to add to a shared meeting replies with this
 * marker alone. The meeting never publishes it, and the agent's own chat,
 * where the meeting turn also runs, shows the reply as empty — including
 * while the marker is still streaming in. The marker is pinned to the
 * server's by a parity test (tests/unit/society/test_meetings.py).
 */
export const MEETING_PASS = "[[MEETING_PASS]]";

/**
 * The reply without the meeting's silence marker. Stray closing punctuation
 * still counts as the marker (as on the server), and any start of it stays
 * hidden while it streams, so not even its first bracket flickers.
 */
export function hideMeetingPass(text: string): string {
  const trimmed = text.trim().replace(/[.!]+$/, "").trimEnd();
  if (!trimmed.startsWith("[")) return text;
  return MEETING_PASS.startsWith(trimmed) ? "" : text;
}
