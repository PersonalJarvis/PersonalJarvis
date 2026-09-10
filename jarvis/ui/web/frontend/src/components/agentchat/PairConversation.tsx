import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { ArrowLeftRight, Lock, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useT } from "@/i18n";
import type { SocietyEnvelope } from "@/lib/societyApi";
import { useStickToBottom } from "@/hooks/useStickToBottom";
import { ScrollToEndButton } from "@/components/ui/scroll-to-end-button";

interface Participant { id: string; name: string }
type Pair = [Participant, Participant];
const PairContext = createContext<((sender: Participant) => void) | null>(null);
export const useOpenPairConversation = () => useContext(PairContext);

/** Both directions share one identity; broadcasts and other pairs never enter it. */
export function pairMessages(events: SocietyEnvelope[], first: string, second: string): SocietyEnvelope[] {
  return events.filter((event) => (
    (event.from_agent === first && event.to_agent === second)
    || (event.from_agent === second && event.to_agent === first)
  ) && typeof event.payload.text === "string" && event.payload.text.trim().length > 0);
}

/** Keep the original chat mounted so closing the read-only view restores its draft. */
export function PairConversationBoundary({ recipient, children }: { recipient: Participant; children: ReactNode }) {
  const [pair, setPair] = useState<Pair | null>(null);
  const trigger = useRef<HTMLElement | null>(null);
  const close = () => {
    setPair(null);
    requestAnimationFrame(() => trigger.current?.focus());
  };
  useEffect(() => { setPair(null); }, [recipient.id]);
  return (
    <PairContext.Provider value={(sender) => {
      if (!sender.id || sender.id === "user" || sender.id === recipient.id) return;
      trigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      setPair([recipient, sender]);
    }}>
      <div className="flex h-full min-h-0 w-full flex-1 flex-col">
        <div hidden={pair !== null} className={pair ? "hidden" : "flex min-h-0 flex-1 flex-col"}>{children}</div>
        {pair && <PairConversation key={JSON.stringify(pair.map((p) => p.id).sort())} pair={pair} onClose={close} />}
      </div>
    </PairContext.Provider>
  );
}

export function PairConversation({ pair, onClose }: { pair: Pair; onClose: () => void }) {
  const t = useT();
  const [messages, setMessages] = useState<SocietyEnvelope[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);
  const { rootRef, contentRef, atEnd, jumpToEnd, follow } = useStickToBottom();
  const first = pair[0].id;
  const second = pair[1].id;
  useLayoutEffect(() => { closeRef.current?.focus(); }, []);
  useLayoutEffect(follow, [follow, messages.length]);

  useEffect(() => {
    const controller = new AbortController();
    let cursor = 0;
    let timer: ReturnType<typeof setTimeout>;
    // The unfiltered board endpoint is ascending. The agent-filtered endpoint
    // returns the latest page and cannot page through a complete history.
    const read = async () => {
      try {
        if (document.hidden) return;
        let more = true;
        while (more && !controller.signal.aborted) {
          const response = await fetch(`/api/society/events?after_seq=${cursor}&limit=1000`, { signal: controller.signal });
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const data: { events: SocietyEnvelope[] } = await response.json();
          if (controller.signal.aborted) return;
          const rows = pairMessages(data.events, first, second);
          if (rows.length) setMessages((previous) => [...previous, ...rows]);
          const next = data.events.at(-1)?.seq ?? cursor;
          more = data.events.length === 1000 && next > cursor;
          cursor = next;
        }
        setError(false);
        setLoading(false);
      } catch (cause) {
        if (controller.signal.aborted) return;
        console.warn("Could not load the agent conversation", cause);
        setError(true);
        setLoading(false);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(() => void read(), 3000 + Math.random() * 2000);
      }
    };
    void read();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [first, second]);

  return (
    <section className="flex min-h-0 flex-1 flex-col" data-testid="agent-pair-conversation" onKeyDown={(event) => {
      if (event.key === "Escape") { event.stopPropagation(); onClose(); }
    }}>
      <header className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-3 text-sm text-foreground">
        <span className="truncate font-medium">{pair[0].name}</span>
        <ArrowLeftRight className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
        <span className="truncate font-medium">{pair[1].name}</span>
        <button ref={closeRef} type="button" onClick={onClose} aria-label={t("agent_chat.pair_close")} className="ml-auto rounded-md p-1.5 hover:bg-secondary focus-visible:ring-2 focus-visible:ring-ring"><X className="h-4 w-4" /></button>
      </header>
      <div className="relative flex min-h-0 flex-1 flex-col">
        <div ref={rootRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-5">
          <div ref={contentRef} className="mx-auto flex w-full max-w-[820px] flex-col gap-5">
            {loading && <p role="status" className="text-center text-xs text-muted-foreground">{t("agent_chat.pair_loading")}</p>}
            {error && <p role="alert" className="text-center text-xs text-destructive">{t("agent_chat.pair_error")}</p>}
            {!loading && !error && messages.length === 0 && <p className="text-center text-xs text-muted-foreground">{t("agent_chat.pair_empty")}</p>}
            {messages.map((message) => <article key={message.event_id} className="max-w-[90%] self-start" data-message-id={message.event_id}>
              <div className="mb-1 flex flex-wrap items-baseline gap-2 text-xs">
                <span className="font-medium text-foreground">{pair.find((person) => person.id === message.from_agent)?.name ?? message.from_agent}</span>
                <time className="text-muted-foreground" dateTime={new Date(message.ts_ms).toISOString()}>{new Date(message.ts_ms).toLocaleString()}</time>
              </div>
              <div className="rounded-2xl border border-border bg-card px-3.5 py-2.5 text-foreground">
                <div className="prose prose-sm prose-neutral max-w-none dark:prose-invert prose-p:my-1 prose-pre:overflow-x-auto [overflow-wrap:anywhere]">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{String(message.payload.text)}</ReactMarkdown>
                </div>
              </div>
            </article>)}
          </div>
        </div>
        {!atEnd && <ScrollToEndButton onClick={jumpToEnd} className="top-auto bottom-3" />}
      </div>
      <footer className="flex shrink-0 flex-wrap items-center justify-center gap-2 border-t border-border px-4 py-3 text-xs text-muted-foreground">
        <Lock className="h-3.5 w-3.5" aria-hidden />
        <span>{t("agent_chat.pair_read_only")}</span>
        <button type="button" onClick={onClose} className="rounded-full border border-border px-3 py-1 text-foreground hover:bg-secondary">{t("agent_chat.pair_close")}</button>
      </footer>
    </section>
  );
}
