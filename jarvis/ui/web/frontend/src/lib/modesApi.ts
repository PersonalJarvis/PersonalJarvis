/**
 * Client for /api/modes — the assistant's shelf of characters.
 *
 * A mode changes how Jarvis behaves in voice AND in chat from the next turn on,
 * with no restart, because both brains read the persona through one function on
 * the backend. Nothing here needs to know that; it matters only in that a
 * switch is a single call and the UI never has to tell the user to restart.
 */

/** Layer 4 of the five-layer enum pattern — mirrors `modes.VERBOSITIES`. */
export type Verbosity = "brief" | "normal" | "rich";
/** Mirrors `modes.PROACTIVITIES`. */
export type Proactivity = "reactive" | "normal" | "forward";

export interface AssistantMode {
  slug: string;
  name: string;
  emoji: string;
  description: string;
  /** How the assistant behaves — appended to the base persona, never replacing it. */
  character: string;
  built_in: boolean;
  voice: string;
  verbosity: Verbosity;
  proactivity: Proactivity;
}

export interface ModesState {
  modes: AssistantMode[];
  active: string;
  /**
   * A mode a SCREEN switched on (today: the Agentic IDE), which outranks the
   * user's own choice while it lasts. Surfaced so the UI can say why the active
   * card is not the one the user picked — an unexplained mode is exactly what
   * this feature was built to replace.
   */
  section_override: string;
  verbosities: Verbosity[];
  proactivities: Proactivity[];
}

async function readJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    // FastAPI puts the human-readable reason in `detail`; surfacing it beats a
    // bare status code, because every failure here has a specific cause the
    // user can act on ("that is a built-in mode", "that looks like a path").
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      /* a non-JSON error body leaves the status line as the message */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export async function fetchModes(): Promise<ModesState> {
  return readJson<ModesState>(await fetch("/api/modes"));
}

export async function activateMode(slug: string): Promise<ModesState> {
  return readJson<ModesState>(
    await fetch("/api/modes/active", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slug }),
    }),
  );
}

export interface ModeDraft {
  slug?: string;
  name: string;
  character: string;
  emoji?: string;
  description?: string;
  voice?: string;
  verbosity?: Verbosity;
  proactivity?: Proactivity;
}

export async function saveMode(draft: ModeDraft): Promise<ModesState> {
  return readJson<ModesState>(
    await fetch("/api/modes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(draft),
    }),
  );
}

export async function deleteMode(slug: string): Promise<ModesState> {
  return readJson<ModesState>(
    await fetch(`/api/modes/${encodeURIComponent(slug)}`, { method: "DELETE" }),
  );
}

export async function restoreBuiltin(slug: string): Promise<ModesState> {
  return readJson<ModesState>(
    await fetch(`/api/modes/${encodeURIComponent(slug)}/restore`, { method: "POST" }),
  );
}
