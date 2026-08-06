/**
 * A pane's conversation, as the coding CLI itself recorded it.
 *
 * The counterpart of `/report`, and the reason it exists: `/report` answers
 * with the pane's SCREEN — the picture a TUI drew, banners and spinners and
 * all — while this answers with the file the CLI keeps in order to be able to
 * resume itself. Roles are declared there, tool calls carry their arguments,
 * and prose is prose, so nothing downstream has to guess what a line was.
 *
 * Two different "no", and the caller must tell them apart. `readable: false`
 * is settled — this CLI keeps no record anyone can read, so switch to the live
 * pane and stay there. `available: false` with `readable: true` is transient —
 * the file appears when the conversation starts, and one CLI only reveals its
 * session id afterwards — so WAIT. Treating the second as the first is how a
 * conversation that was seconds away never gets shown.
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
  /** Does this CLI keep a record that can be read at all? Settled, not timing. */
  readable: boolean;
  /** Is there a conversation to show right now? False may simply mean "not yet". */
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
