const LAST_AGENT_KEY = "jarvis.society.lastAgent";

/** Read the most recently opened Society agent, if browser storage is available. */
export function storedLastAgentId(): string | null {
  try {
    return window.localStorage.getItem(LAST_AGENT_KEY);
  } catch {
    // A disabled browser storage must not prevent the Agents view from opening.
    return null;
  }
}

/** Persist the latest explicit agent selection across section visits and app launches. */
export function rememberLastAgentId(agentId: string): void {
  try {
    window.localStorage.setItem(LAST_AGENT_KEY, agentId);
  } catch {
    /* The selection remains available for this visit when storage is unavailable. */
  }
}

/** Remove a remembered agent that no longer exists in the roster. */
export function forgetLastAgentId(): void {
  try {
    window.localStorage.removeItem(LAST_AGENT_KEY);
  } catch {
    /* Nothing to do when storage is unavailable. */
  }
}

export const LAST_AGENT_STORAGE_KEY = LAST_AGENT_KEY;
