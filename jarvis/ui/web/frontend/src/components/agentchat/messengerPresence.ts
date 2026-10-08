/**
 * The pure model behind an agent chat's messenger look (MessengerTurn).
 *
 * An agent chat shows no reasoning and no work log. While a turn runs, ONE
 * presence line says what the agent is doing in a word or two ("Thinking",
 * "Writing something down"), and a finished call that changed something the
 * person cares about — a routine, a rule, a saved skill — leaves a quiet
 * centred notice between the messages. Everything else the agent did stays
 * out of the chat; its own messages say what it means.
 */

import { waitsOnCard, type ToolBlock, type TurnBlock, type TurnItem } from "./reduce";

export type Presence =
  | "thinking"
  | "typing"
  | "writing"
  | "searching"
  | "browsing"
  | "setting_up"
  | "working";

/** The bare operation of a call, without its transport prefix (`mcp__jarvis__…`, `jarvis/…`). */
export function bareToolName(name: string): string {
  return (name.split(/__|\//).pop() ?? name).replace(/^(?:functions|tools)\./, "").toLowerCase();
}

function inputOf(block: ToolBlock): Record<string, unknown> {
  return block.input && typeof block.input === "object" ? (block.input as Record<string, unknown>) : {};
}

function payloadOf(block: ToolBlock): Record<string, unknown> {
  const payload = inputOf(block).payload;
  return payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
}

const PROPOSE = "society_propose_change";

/** What kind of work a call is, as the presence line names it. */
export function callPresence(block: ToolBlock): Presence {
  const name = bareToolName(block.name);
  if (name === PROPOSE) {
    const kind = String(inputOf(block).kind ?? "");
    return kind === "rule" || kind === "skill" ? "writing" : "setting_up";
  }
  if (/(wiki_?note|remember|memory_?(write|update|save)|^(write|edit|multiedit|applypatch|apply_patch|str_replace|writefile|editfile|createfile)$)/.test(name)) {
    return "writing";
  }
  if (/(^browser|browser_|navigate|click|screenshot|computer)/.test(name)) return "browsing";
  if (/(search|recall|^grep$|^rg$|^glob$|^read|^ls$|^list|fetch|lookup|^find)/.test(name)) return "searching";
  if (/(credential|routine|install|connect|plugin|connector|configure|setup)/.test(name)) return "setting_up";
  return "working";
}

function waitsForPerson(block: ToolBlock): boolean {
  return waitsOnCard(block) || Boolean(block.approval && block.approval.decision === null);
}

/**
 * What the running turn is doing right now, or null when nothing should say
 * so: the turn is over, or a card is waiting for the person (the card says it).
 */
export function presenceOf(turn: TurnItem): Presence | null {
  if (turn.status !== "running") return null;
  const blocks: TurnBlock[] = turn.blocks;
  if (blocks.some((block) => block.kind === "tool" && waitsForPerson(block))) return null;
  for (let i = blocks.length - 1; i >= 0; i--) {
    const block = blocks[i];
    if (block.kind === "tool") return block.output === null ? callPresence(block) : "thinking";
    if (block.kind === "text") return block.text.trim() ? "typing" : "thinking";
    // A thought is never shown; it only means the agent is thinking.
    return "thinking";
  }
  return "thinking";
}

export type NoticeVerb = "created" | "updated" | "paused" | "resumed" | "deleted";

export interface ActionNotice {
  /** i18n key under `messenger_turn.` naming what happened ("Created: Routine"). */
  key: string;
  /** The thing it happened to ("Daily contributor check"), when the call names it. */
  subject: string;
  icon: "routine" | "rule" | "skill" | "settings" | "note";
}

const ROUTINE_VERB: Record<string, NoticeVerb> = {
  create: "created", update: "updated", pause: "paused", resume: "resumed", delete: "deleted",
};

/**
 * The quiet notice a FINISHED call leaves in the chat, or null when the call
 * changed nothing worth a line of its own. A call that failed, or a proposal
 * still waiting on its card, leaves none: the agent's message and the card
 * speak for those.
 */
export function actionNoticeOf(block: ToolBlock): ActionNotice | null {
  if (block.output === null || block.isError) return null;
  if (/"status"\s*:\s*"pending"/.test(block.output)) return null;
  const name = bareToolName(block.name);
  if (name === PROPOSE) {
    const kind = String(inputOf(block).kind ?? "");
    const payload = payloadOf(block);
    if (kind === "routine") {
      const verb = ROUTINE_VERB[String(payload.operation ?? "create")] ?? "updated";
      return { key: `routine_${verb}`, subject: String(payload.title ?? ""), icon: "routine" };
    }
    if (kind === "rule") return { key: "rule_saved", subject: "", icon: "rule" };
    if (kind === "skill") return { key: "skill_saved", subject: String(payload.name ?? ""), icon: "skill" };
    if (kind === "focus" || kind === "approval_rule") return { key: "settings_updated", subject: "", icon: "settings" };
    // An identity change has its own notice with Undo (IdentityNotice).
    return null;
  }
  if (/(wiki_?note|^remember$)/.test(name)) {
    const input = inputOf(block);
    return { key: "note_saved", subject: String(input.title ?? ""), icon: "note" };
  }
  return null;
}
