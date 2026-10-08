import { memo, useEffect, useMemo, useState, type ReactNode } from "react";
import { BookmarkPlus, CircleAlert, Clock3, Feather, NotebookPen, SlidersHorizontal } from "lucide-react";
import { ChatMarkdown } from "./ChatMarkdown";
import { CredentialCard } from "./CredentialCard";
import { QuestionCard } from "./QuestionCard";
import type { ApprovalDecision } from "@/lib/agentChatApi";
import type { TurnItem } from "./reduce";
import { actionNoticeOf, presenceOf, type ActionNotice } from "./messengerPresence";
import { BotStage, useDoneFlourish, useTypingPresence } from "./botStage/BotStage";
import { ApprovalPrompt, PROSE, THREAD_MEDIA } from "@/components/agentic/threads/ThreadTimeline";
import { SubagentCard } from "@/components/agentic/threads/ThreadSubagents";
import { isSubagentCall } from "@/components/agentic/threads/subagents";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * One agent turn read like a messenger conversation. The agent's messages
 * are grey bubbles; what it changed for the person (a routine, a rule, a
 * saved skill) is a quiet centred line between them; while it works, its
 * own face plays a little scene of what it is doing on a stage below
 * (BotStage) and hops once when the turn is done. No reasoning and no work
 * log: the agent's own messages say what is happening, what is done and
 * what comes next.
 */
export const MessengerTurn = memo(function MessengerTurn({ turn, avatar, color, onDecide, memoryLinks }: {
  turn: TurnItem;
  /** The agent's face at 30 px; it plays the scenes. */
  avatar: ReactNode;
  /** The agent's colour, for the accents of the scenes. */
  color: string;
  onDecide: (approvalId: string, decision: ApprovalDecision) => void | Promise<void>;
  /** Memory receipts posted after the turn: each a "Noted" quill line that opens the file. */
  memoryLinks?: MemoryLink[];
}) {
  const t = useT();
  const textLength = useMemo(() => turn.blocks.reduce((n, block) => n + (block.kind === "text" ? block.text.length : 0), 0), [turn.blocks]);
  const presence = useTypingPresence(presenceOf(turn), textLength);
  const hasText = textLength > 0 && turn.blocks.some((block) => block.kind === "text" && block.text.trim());
  const flourish = useDoneFlourish(turn.status === "running", turn.status === "done");
  const stage = presence ?? (flourish ? "done" : null);
  let noteIndex = 0;
  return <div className="flex min-w-0 flex-col gap-1.5" data-testid="messenger-turn" data-status={turn.status}>
    {turn.blocks.map((block) => {
      if (block.kind === "text") {
        return block.text.trim() ? <MessageBubble key={block.id} text={block.text} /> : null;
      }
      if (block.kind !== "tool") return null;
      if (block.credential) return <CredentialCard key={block.callId} credential={block.credential} />;
      if (block.question) return <QuestionCard key={block.callId} question={block.question} />;
      if (block.approval && block.approval.decision === null) return <ApprovalPrompt key={block.callId} block={block} onDecide={onDecide} />;
      if (isSubagentCall(block)) return <SubagentCard key={block.callId} block={block} turn={turn} />;
      const notice = actionNoticeOf(block);
      if (!notice) return null;
      // The turn's own "Noted" lines open its memory receipts first, in order.
      const link = notice.icon === "note" ? memoryLinks?.[noteIndex++] : undefined;
      return <ActionLine key={block.callId} notice={notice} link={link} />;
    })}
    {memoryLinks?.slice(noteIndex).map((link) => <ActionLine key={`memory:${link.key}`} notice={NOTED} link={link} />)}
    {stage && <BotStage presence={stage} seed={turn.id} avatar={avatar} color={color} />}
    {turn.status === "error" && (
      <p role="alert" className="mx-auto flex max-w-[85%] items-center gap-1.5 py-1 text-center text-xs text-destructive">
        <CircleAlert aria-hidden className="h-3.5 w-3.5 shrink-0" />
        <span>{turn.error || t("thread_turn.failed")}</span>
      </p>
    )}
    {turn.status === "cancelled" && <SystemLine>{t("thread_turn.stopped")}</SystemLine>}
    {turn.status === "done" && !hasText && !flourish && <SystemLine>{t("thread_turn.no_answer")}</SystemLine>}
  </div>;
});

/** How long a sent message may wait for its turn before the stage stops reading it. */
const AWAIT_MS = 90_000;

/**
 * The person's message is in and the agent's turn has not started yet: its
 * face reads the message, so the chat never sits silent after a send.
 */
export function AwaitingTurn({ since, seed, avatar, color }: { since: number; seed: string; avatar: ReactNode; color: string }) {
  const [, tick] = useState(0);
  const left = AWAIT_MS - (Date.now() - since);
  const waiting = left > 0;
  useEffect(() => {
    if (!waiting) return;
    const id = window.setTimeout(() => tick((n) => n + 1), left + 50);
    return () => window.clearTimeout(id);
    // `left` shrinks every render; only the start of the wait matters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [waiting, since]);
  return waiting ? <BotStage presence="reading" seed={seed} avatar={avatar} color={color} /> : null;
}

/** One message of the agent's: a grey bubble on the left. */
export function MessageBubble({ text }: { text: string }) {
  return <div className="flex min-w-0" data-testid="agent-message-bubble">
    <div className="jarvis-chat-in min-w-0 max-w-[min(85%,42rem)] rounded-[20px] px-4 py-2.5">
      <div className={cn(PROSE, THREAD_MEDIA, "py-0")}><ChatMarkdown text={text} /></div>
    </div>
  </div>;
}

function SystemLine({ children }: { children: ReactNode }) {
  return <p className="py-1 text-center text-xs text-muted-foreground">{children}</p>;
}

const NOTICE_ICON: Record<ActionNotice["icon"], typeof Clock3> = {
  routine: Clock3,
  rule: BookmarkPlus,
  skill: NotebookPen,
  settings: SlidersHorizontal,
  note: Feather,
};

export interface MemoryLink {
  key: string;
  /** What the line opens ("Memory updated · USER.md"), as its tooltip. */
  title: string;
  onOpen: () => void;
}

const NOTED: ActionNotice = { key: "note_saved", subject: "", icon: "note" };

/** A "Noted" quill line that opens a memory file, for a receipt with no turn of its own. */
export function NotedLine({ link }: { link: MemoryLink }) {
  return <ActionLine notice={NOTED} link={link} />;
}

/**
 * "Created: Routine  ⏱ Daily contributor check" — centred, quiet, not a
 * message. With a memory link it is a button that opens the changed file.
 */
function ActionLine({ notice, link }: { notice: ActionNotice; link?: MemoryLink }) {
  const t = useT();
  const Icon = NOTICE_ICON[notice.icon];
  const body = <>
    <span>{t(`messenger_turn.${notice.key}`)}</span>
    <Icon aria-hidden className="h-3.5 w-3.5 shrink-0" />
    {notice.subject ? <span className="text-foreground-secondary">{notice.subject}</span> : null}
  </>;
  if (link) {
    return <div className="flex justify-center py-1">
      <button type="button" onClick={link.onOpen} title={link.title} aria-label={link.title}
        className="flex flex-wrap items-center justify-center gap-x-1.5 rounded-md px-1.5 text-center text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        data-testid="messenger-action" data-memory-link={link.key}>
        {body}
      </button>
    </div>;
  }
  return <p className="flex flex-wrap items-center justify-center gap-x-1.5 py-1 text-center text-xs text-muted-foreground" data-testid="messenger-action">
    {body}
  </p>;
}
