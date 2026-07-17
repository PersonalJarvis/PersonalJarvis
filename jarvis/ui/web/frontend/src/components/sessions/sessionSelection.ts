import type { SessionListItem } from "./types";

/** Keep transcript selection valid as running or empty rows enter and leave. */
export function resolveSelectedSessionId(
  sessions: SessionListItem[],
  currentId: string | null,
): string | null {
  if (currentId !== null && sessions.some((session) => session.id === currentId)) {
    return currentId;
  }
  const newestFinished = sessions.find((session) => session.ended_ms !== null);
  return (newestFinished ?? sessions[0])?.id ?? null;
}
