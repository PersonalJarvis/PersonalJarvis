/** Mounted only for the execution the user opens; owns and releases its socket. */
import { useEffect, useState } from "react";
import { ArrowLeft, Send, Square } from "lucide-react";
import { AgentTimeline } from "@/components/agentchat/AgentTimeline";
import { runningTurn } from "@/components/agentchat/reduce";
import { AgentChatStoreProvider } from "@/components/agentchat/AgentChatStoreContext";
import { createAgentChatStore } from "@/store/agentChat";
import { useStickToBottom } from "@/hooks/useStickToBottom";
import { useT } from "@/i18n";

export default function RoutineChat({ sessionId, onClose }: { sessionId: string; onClose: () => void }) {
  const t = useT();
  const label = (key: string) => t(`society.routine_detail.${key}`);
  const [store] = useState(() => createAgentChatStore("society", "routine"));
  const timeline = store((state) => state.timeline);
  const items = timeline.items;
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
    <section className="flex min-h-0 flex-1 flex-col text-foreground" data-testid="routine-chat" data-session-id={sessionId}>
      <header className="flex shrink-0 items-center justify-between gap-2 pb-3">
        <button type="button" className="flex items-center gap-1 text-[12px] text-muted-foreground" onClick={onClose}><ArrowLeft size={14} />{label("history")}</button>
        <span className="text-[11px] text-muted-foreground">{label("background_chat")}</span>
      </header>
      <div ref={scroll.rootRef} className="min-h-0 flex-1 overflow-y-auto px-1"><div ref={scroll.contentRef} className="space-y-3">
        {!ready && !error && <p role="status" className="text-[12px]">{t("tasks_view.loading_details")}</p>}
        <AgentTimeline items={items} assistantName={session?.title.split(" · ")[0] ?? ""} providerLabel={(id) => id} onDecide={store.getState().decide} />
      </div></div>
      {error && <p role="alert" className="text-[12px] text-destructive">{error}</p>}
      <form className="mt-3 flex shrink-0 items-end gap-2" onSubmit={(event) => {
        event.preventDefault();
        if (!message.trim() || !ready || running) return;
        void store.getState().send(message).then(() => { if (!store.getState().lastError) setMessage(""); });
      }}>
        <textarea className="min-w-0 flex-1 rounded-lg border border-border bg-background p-2 text-[12px]" rows={2} aria-label={label("follow_up")} placeholder={label("follow_up")} value={message} onChange={(event) => setMessage(event.target.value)} />
        {running ? <button type="button" className="rounded p-2 hover:bg-secondary" aria-label={label("stop_run")} onClick={() => void store.getState().cancel()}><Square size={16} /></button>
          : <button type="submit" className="rounded p-2 hover:bg-secondary disabled:opacity-50" disabled={!ready || !message.trim()} aria-label={label("send")}><Send size={16} /></button>}
      </form>
    </section>
  </AgentChatStoreProvider>;
}
