import { memo, useMemo, type ReactNode } from "react";
import { BookmarkPlus, CircleAlert, Clock3, Feather, MousePointer2, NotebookPen, Search, Settings, SlidersHorizontal, Sparkles } from "lucide-react";
import { ChatMarkdown } from "./ChatMarkdown";
import { CredentialCard } from "./CredentialCard";
import { QuestionCard } from "./QuestionCard";
import type { ApprovalDecision } from "@/lib/agentChatApi";
import type { TurnItem } from "./reduce";
import { actionNoticeOf, presenceOf, type ActionNotice, type Presence } from "./messengerPresence";
import { ApprovalPrompt, PROSE, THREAD_MEDIA } from "@/components/agentic/threads/ThreadTimeline";
import { SubagentCard } from "@/components/agentic/threads/ThreadSubagents";
import { isSubagentCall } from "@/components/agentic/threads/subagents";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import "./MessengerTurn.css";

/**
 * One agent turn read like a messenger conversation. The agent's messages
 * are grey bubbles; what it changed for the person (a routine, a rule, a
 * saved skill) is a quiet centred line between them; while it works, its
 * face and a word or two say what it is doing, with a small effect — a
 * quill while it writes something down, a magnifier while it looks
 * something up. No reasoning and no work log: the agent's own messages say
 * what is happening, what is done and what comes next.
 */
export const MessengerTurn = memo(function MessengerTurn({ turn, avatar, onDecide, extras }: {
  turn: TurnItem;
  /** The agent's face, drawn beside the presence line. */
  avatar: ReactNode;
  onDecide: (approvalId: string, decision: ApprovalDecision) => void | Promise<void>;
  /** Notices that belong to this turn without being blocks of it (a memory receipt). */
  extras?: { key: string; node: ReactNode }[];
}) {
  const t = useT();
  const presence = presenceOf(turn);
  const hasText = useMemo(() => turn.blocks.some((block) => block.kind === "text" && block.text.trim()), [turn.blocks]);
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
      return notice ? <ActionLine key={block.callId} notice={notice} /> : null;
    })}
    {extras?.map((extra) => <div key={`extra:${extra.key}`} className="min-w-0">{extra.node}</div>)}
    {presence && <PresenceLine presence={presence} avatar={avatar} />}
    {turn.status === "error" && (
      <p role="alert" className="mx-auto flex max-w-[85%] items-center gap-1.5 py-1 text-center text-xs text-destructive">
        <CircleAlert aria-hidden className="h-3.5 w-3.5 shrink-0" />
        <span>{turn.error || t("thread_turn.failed")}</span>
      </p>
    )}
    {turn.status === "cancelled" && <SystemLine>{t("thread_turn.stopped")}</SystemLine>}
    {turn.status === "done" && !hasText && <SystemLine>{t("thread_turn.no_answer")}</SystemLine>}
  </div>;
});

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

/** "Created: Routine  ⏱ Daily contributor check" — centred, quiet, not a message. */
function ActionLine({ notice }: { notice: ActionNotice }) {
  const t = useT();
  const Icon = NOTICE_ICON[notice.icon];
  return <p className="flex flex-wrap items-center justify-center gap-x-1.5 py-1 text-center text-xs text-muted-foreground" data-testid="messenger-action">
    <span>{t(`messenger_turn.${notice.key}`)}</span>
    <Icon aria-hidden className="h-3.5 w-3.5 shrink-0" />
    {notice.subject ? <span className="text-foreground-secondary">{notice.subject}</span> : null}
  </p>;
}

const EFFECT: Record<Presence, ReactNode> = {
  thinking: <span className="messenger-thought" aria-hidden><i /><i /><i /></span>,
  typing: <span className="messenger-typing" aria-hidden><i /><i /><i /></span>,
  writing: <span className="messenger-quill" aria-hidden>
    <svg viewBox="0 0 24 10" className="messenger-quill-line"><path d="M1 6 C4 2, 6 9, 9 5 S14 2, 16 6 S21 8, 23 4" /></svg>
    <Feather className="messenger-quill-pen" />
  </span>,
  searching: <span className="messenger-search" aria-hidden><Search /></span>,
  browsing: <span className="messenger-browse" aria-hidden><MousePointer2 /></span>,
  setting_up: <span className="messenger-gear" aria-hidden><Settings /></span>,
  working: <span className="messenger-sparkle" aria-hidden><Sparkles /></span>,
};

/** The agent's face, a small effect for what it is doing, and the word for it. */
export function PresenceLine({ presence, avatar }: { presence: Presence; avatar: ReactNode }) {
  const t = useT();
  const label = t(`messenger_turn.${presence}`);
  return <div className="messenger-presence flex items-center gap-2.5 py-1.5" role="status" aria-label={label}
    data-testid="messenger-presence" data-presence={presence}>
    <span className="messenger-avatar relative inline-flex shrink-0">{avatar}</span>
    <span className="messenger-effect inline-flex shrink-0 items-center text-muted-foreground">{EFFECT[presence]}</span>
    <span className="live-tool-shine text-sm text-muted-foreground">{label}</span>
  </div>;
}
