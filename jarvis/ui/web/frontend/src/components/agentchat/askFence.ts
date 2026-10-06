/**
 * A coding agent's end-of-turn question block (jarvis/agent_chat/turn_prompts.py).
 *
 * A CLI with no way to ask mid-turn ends its reply with one fenced block in
 * this language; the server turns it into the chat's question card. The reply
 * shows without it — the card already asks — including while the block is
 * still streaming in. The fence name is pinned to the server's by a parity
 * test (tests/unit/agent_chat/test_turn_prompts.py).
 */
export const ASK_FENCE = "jarvis-ask";

const COMPLETE = new RegExp("```[ \\t]*" + ASK_FENCE + "[ \\t]*\\r?\\n[\\s\\S]*?```", "g");
const STREAMING = new RegExp("```[ \\t]*" + ASK_FENCE + "[\\s\\S]*$");

/** The reply without its question blocks, finished or still arriving. */
export function hideAskBlocks(text: string): string {
  if (!text.includes(ASK_FENCE)) return text;
  return text.replace(COMPLETE, "").replace(STREAMING, "").replace(/\n{3,}/g, "\n\n").trimEnd();
}
