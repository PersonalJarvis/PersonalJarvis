/**
 * A pane's conversation, as the coding CLI itself recorded it.
 *
 * The counterpart of `/report`, and the reason it exists: `/report` answers
 * with the pane's SCREEN — the picture a TUI drew, banners and spinners and
 * all — while this answers with the file the CLI keeps in order to be able to
 * resume itself. Roles are declared there, tool calls carry their arguments,
 * and prose is prose, so nothing downstream has to guess what a line was.
 *
 * `available: false` is a normal answer and the caller must handle it: a CLI
 * that keeps no readable record, or a pane whose conversation has not been
 * written yet. Watching the live pane is the honest fallback — never an error
 * message, because nothing is wrong.
 */

/** One thing the agent did between two things it said. */
export interface AgentStep {
  /** The CLI's own name for the tool — shown as-is, never translated. */
  tool: string;
  /** The one argument worth reading at a glance: a path, a command. */
  target: string;
  /** Everything else about the call, for a reader who opens the step. */
  detail: string;
}

export interface AgentTurn {
  role: "user" | "assistant";
  text: string;
  steps: AgentStep[];
}

export interface AgentConversation {
  terminal: string;
  agent: string;
  available: boolean;
  turns: AgentTurn[];
}

export async function fetchConversation(
  terminal: string,
  signal?: AbortSignal,
): Promise<AgentConversation> {
  const response = await fetch(
    `/api/agentic-ide/terminals/${encodeURIComponent(terminal)}/conversation`,
    { signal },
  );
  if (!response.ok) throw new Error(String(response.status));
  return (await response.json()) as AgentConversation;
}
