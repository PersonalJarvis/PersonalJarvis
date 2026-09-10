import { useId, useState, type ReactNode } from "react";
import { ArrowDownLeft, ArrowUpRight, ChevronRight, Clock3 } from "lucide-react";
import { ChatMarkdown } from "@/components/agentchat/ChatMarkdown";
import { useOpenPairConversation } from "@/components/agentchat/PairConversation";
import type { InternalMessageItem } from "@/components/agentchat/reduce";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { AgentSwatch } from "../AgentSwatch";
import type { SocietyAgent } from "../data";

/** A quiet event in the conversation; details remain keyboard accessible. */
export function ChatActivity({ label, icon, children, failed = false }: {
  label: ReactNode; icon?: ReactNode; children: ReactNode; failed?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return <div className="my-3 w-full min-w-0 text-xs text-muted-foreground">
    <button type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}
      className={cn("mx-auto flex max-w-[min(100%,36rem)] items-center justify-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", failed && "text-destructive")}>
      {icon}<span className="min-w-0 truncate">{label}</span>
      <ChevronRight aria-hidden className={cn("h-3 w-3 shrink-0 transition-transform", open && "rotate-90")} />
    </button>
    {open ? <div id={id} className="mx-auto mt-2 max-w-xl rounded-xl bg-secondary px-4 py-3 text-sm leading-relaxed [overflow-wrap:anywhere]">{children}</div> : null}
  </div>;
}

/** Recognize only the scheduler's complete envelope; ordinary messages stay intact. */
export function routineTask(text: string): string | null {
  const envelope = /^Scheduled routine [^\s.]+\. Follow your CURRENT standing instructions and permissions\.\r?\nUse your memory and conversation archive for prior results\. For information watches, check sources and dates, remember last-seen items, and report only meaningful new findings\.\r?\n\r?\n/;
  const match = envelope.exec(text);
  return match ? text.slice(match[0].length).trim() : null;
}

export function RoutineActivity({ task, original }: { task: string; original: string }) {
  const t = useT();
  return <ChatActivity label={<>{t("society.chat.routine_check")} · {task.split(/\r?\n/)[0]}</>} icon={<Clock3 aria-hidden className="h-3.5 w-3.5 shrink-0" />}>
    <ChatMarkdown text={task} />
    <details className="mt-2 text-xs"><summary className="cursor-pointer">{t("society.chat.activity_details")}</summary><p className="mt-2 whitespace-pre-wrap">{original}</p></details>
  </ChatActivity>;
}

export function AgentMessageActivity({ item, roster }: { item: InternalMessageItem; roster: SocietyAgent[] }) {
  const t = useT();
  const openPair = useOpenPairConversation();
  const outgoing = Boolean(item.outgoing);
  const id = item.outgoing?.recipientId ?? item.message.sender_id;
  const participant = roster.find(agent => agent.agentId === id);
  const name = participant?.name || item.outgoing?.recipientName || item.message.sender_name;
  const status = item.message.status;
  const failed = status === "failed";
  const canOpenPair = Boolean(openPair && id && id !== "user" && item.message.sender_kind !== "user");
  const label = t(failed ? "society.chat.message_failed" : outgoing ? "society.chat.message_to" : "society.chat.message_from");
  return <ChatActivity failed={failed} icon={outgoing ? <ArrowUpRight aria-hidden className="h-3.5 w-3.5 shrink-0" /> : <ArrowDownLeft aria-hidden className="h-3.5 w-3.5 shrink-0" />}
    label={<span className="inline-flex max-w-full items-center gap-2"><span>{label}</span>{participant ? <AgentSwatch agent={participant} size={18} /> : null}<span className="truncate">{name}</span>{status === "queued" ? <span>· {t("agent_chat.delivery_queued")}</span> : null}</span>}>
    <ChatMarkdown text={item.message.text} />
    {item.message.error ? <p role="alert" className="mt-2 text-destructive">{item.message.error}</p> : null}
    {canOpenPair ? <button type="button" onClick={() => openPair?.({ id, name })} className="mt-3 rounded-sm text-xs underline underline-offset-4 focus-visible:ring-2 focus-visible:ring-ring">{t("agent_chat.pair_open")}</button> : null}
  </ChatActivity>;
}
