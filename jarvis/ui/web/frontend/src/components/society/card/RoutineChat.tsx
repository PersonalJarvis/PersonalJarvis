/** The selected execution occupies the agent's chat lane and loads only on navigation. */
import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, Send, Square } from "lucide-react";
import { AgentTimeline } from "@/components/agentchat/AgentTimeline";
import { ChatMarkdown } from "@/components/agentchat/ChatMarkdown";
import { EMPTY_TIMELINE, reduceEvents, runningTurn, type TimelineItem } from "@/components/agentchat/reduce";
import { AgentChatStoreProvider } from "@/components/agentchat/AgentChatStoreContext";
import { fetchAgentChatSession } from "@/lib/agentChatApi";
import { createAgentChatStore } from "@/store/agentChat";
import { useStickToBottom } from "@/hooks/useStickToBottom";
import { useT } from "@/i18n";
import { legacyRoutineEvents, routineTask, type RoutineChatTarget } from "../chat/routineExecution";

function displayItems(items: TimelineItem[]): TimelineItem[] {
  return items.map((item) => item.type === "user" ? { ...item, text: routineTask(item.text) ?? item.text } : item);
}

export default function RoutineChat({ target, onClose }: { target: RoutineChatTarget; onClose: () => void }) {
  const t = useT();
  return <section className="flex h-full min-h-0 flex-1 flex-col bg-background p-4 text-foreground sm:p-6" data-testid="routine-chat" data-session-id={target.sessionId}>
    <header className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-border pb-3">
      <button type="button" className="flex items-center gap-1 text-[12px] text-muted-foreground hover:text-foreground" onClick={onClose}><ArrowLeft size={14} />{t("society.routine_detail.back_to_agent")}</button>
      <div className="min-w-0 flex-1"><h3 className="truncate text-sm font-medium">{target.title}</h3>
        <time className="text-[11px] text-muted-foreground" dateTime={new Date(target.timestamp).toISOString()}>{new Date(target.timestamp).toLocaleString()}</time>
      </div>
      <span className="text-[11px] text-muted-foreground">{t("society.routine_detail.background_chat")}</span>
    </header>
    {target.legacy ? <HistoricalExecution target={target} /> : <LiveExecution sessionId={target.sessionId} />}
  </section>;
}

function HistoricalExecution({ target }: { target: RoutineChatTarget }) {
  const t = useT();
  const [snapshot, setSnapshot] = useState<{ name: string; items: TimelineItem[] } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    void fetchAgentChatSession(target.sessionId).then(({ session, events }) => {
      if (!alive) return;
      const selected = legacyRoutineEvents(events, target);
      const items = displayItems(reduceEvents(EMPTY_TIMELINE, selected).items).map((item) => item.type === "turn"
        ? { ...item, blocks: item.blocks.map((block) => block.kind === "tool" ? { ...block, approval: null } : block) } : item);
      setSnapshot({ name: session.title, items });
    }).catch((err) => { if (alive) setError(err instanceof Error ? err.message : String(err)); });
    return () => { alive = false; };
  }, [target]);
  return <>
    <div className="min-h-0 flex-1 space-y-4 overflow-y-auto py-4" data-testid="routine-historical-transcript">
      {!snapshot && !error && <p role="status">{t("tasks_view.loading_details")}</p>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {snapshot?.items.length ? <AgentTimeline items={snapshot.items} assistantName={snapshot.name} providerLabel={(id) => id} onDecide={async () => { /* Historical approvals are hidden; this transcript cannot act. */ }} /> : null}
      {(snapshot?.items.length === 0 || error) && <>
        <p className="text-sm text-muted-foreground">{t("society.routine_detail.trace_unavailable")}</p>
        {target.result && <ChatMarkdown text={target.result} />}
      </>}
    </div>
    <p className="shrink-0 border-t border-border pt-3 text-xs text-muted-foreground">{t("society.routine_detail.historical_chat")}</p>
  </>;
}

function LiveExecution({ sessionId }: { sessionId: string }) {
  const t = useT();
  const label = (key: string) => t(`society.routine_detail.${key}`);
  const [store] = useState(() => createAgentChatStore("society", "routine"));
  const timeline = store((state) => state.timeline);
  const visibleItems = useMemo(() => displayItems(timeline.items), [timeline.items]);
  const session = store((state) => state.activeSession);
  const busy = store((state) => state.busy);
  const socketState = store((state) => state.socketState);
  const error = store((state) => state.lastError);
  const [message, setMessage] = useState("");
  const running = Boolean(runningTurn(timeline)) || busy;
  const ready = session?.session_id === sessionId && socketState === "open";
  const scroll = useStickToBottom();
  useEffect(() => {
    store.getState().openSession(sessionId);
    return () => store.getState().disconnect();
  }, [sessionId, store]);
  return <AgentChatStoreProvider store={store}>
    <div ref={scroll.rootRef} className="min-h-0 flex-1 overflow-y-auto py-4"><div ref={scroll.contentRef} className="space-y-3">
      {!ready && !error && <p role="status" className="text-sm">{t("tasks_view.loading_details")}</p>}
      <AgentTimeline items={visibleItems} assistantName={session?.title.split(" · ")[0] ?? ""} providerLabel={(id) => id} onDecide={store.getState().decide} />
    </div></div>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    <form className="mt-3 flex shrink-0 items-end gap-2" onSubmit={(event) => {
      event.preventDefault();
      if (!message.trim() || !ready || running) return;
      void store.getState().send(message).then(() => { if (!store.getState().lastError) setMessage(""); });
    }}>
      <textarea className="min-w-0 flex-1 rounded-lg border border-border bg-background p-2 text-sm" rows={2} aria-label={label("follow_up")} placeholder={label("follow_up")} value={message} onChange={(event) => setMessage(event.target.value)} />
      {running ? <button type="button" className="rounded p-2 hover:bg-secondary" aria-label={label("stop_run")} onClick={() => void store.getState().cancel()}><Square size={16} /></button>
        : <button type="submit" className="rounded p-2 hover:bg-secondary disabled:opacity-50" disabled={!ready || !message.trim()} aria-label={label("send")}><Send size={16} /></button>}
    </form>
  </AgentChatStoreProvider>;
}
