import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ArrowUpRight, AudioLines, Keyboard, Loader2, Plus, X } from "lucide-react";

import { useAgentChat } from "@/components/agentchat/AgentChatStoreContext";
import { MascotGigi } from "@/components/MascotGigi";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { TONE_DOT, useAssistantStatus, useVoiceModeSwitch } from "@/components/home/assistantStatus";
import { useAgentInstructions } from "@/hooks/useAgentInstructions";
import { fill, useT } from "@/i18n";
import { startNewTextChat } from "@/lib/newChat";
import { cn } from "@/lib/utils";
import { fetchWikiHealth, type WikiHealthSnapshot } from "@/lib/wikiApi";
import { useEventStore } from "@/store/events";
import type { TimelineItem } from "@/components/agentchat/reduce";

/**
 * The assistant card — the right-hand column of the front page.
 *
 * Who you are talking to, as a contact card in a messenger: the living
 * mascot in a round frame (it reacts to the voice), the name, what it is
 * doing right now, and the two things you do with an assistant — talk to it
 * or start over. Under that two tabs:
 *
 *   Overview  — two vivid tiles that open what makes this assistant YOURS:
 *               Soul (the standing-instructions file it reads every turn)
 *               and Memory (the wiki it writes to), plus the model this chat
 *               runs on.
 *   This chat — plain facts about the conversation on screen, counted from
 *               its own timeline.
 *
 * Every number comes from a real read; while a read is in flight the tile
 * shows a placeholder bar, never an invented zero.
 */
export function AssistantProfilePanel({ onClose }: { onClose: () => void }) {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const status = useAssistantStatus();
  const voice = useVoiceModeSwitch();

  return (
    <aside
      className="hidden w-[320px] shrink-0 flex-col border-l border-border bg-sidebar xl:flex"
      data-testid="assistant-panel"
      aria-label={assistantName}
    >
      <div className="flex justify-end px-3 pt-3">
        <button
          type="button"
          onClick={onClose}
          aria-label={t("assistant_chat.panel_close")}
          title={t("assistant_chat.panel_close")}
          data-testid="assistant-panel-close"
          className="flex h-8 w-8 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <X aria-hidden className="h-4 w-4" />
        </button>
      </div>
      <ScrollArea className="min-h-0 flex-1">
        <div className="flex flex-col items-center px-5 pb-6">
          <div className="relative">
            <div
              className={cn(
                "flex h-28 w-28 items-center justify-center overflow-hidden rounded-full border border-border bg-card shadow-float transition-shadow",
                status.tone === "live" && "ring-4 ring-accent/40",
              )}
            >
              <MascotGigi size={84} reactToVoice enableComments={false} />
            </div>
            <span
              aria-hidden
              className={cn(
                "absolute bottom-1.5 right-1.5 h-4 w-4 rounded-full ring-4 ring-sidebar",
                TONE_DOT[status.tone],
                status.tone === "live" && "animate-jarvis-pulse",
              )}
            />
          </div>
          <h2 className="mt-4 text-xl font-semibold text-foreground-strong" data-testid="assistant-panel-name">
            {assistantName}
          </h2>
          <p className="mt-1 flex items-center gap-1.5 text-sm text-muted-foreground" data-testid="assistant-panel-status">
            {status.tone === "busy" || status.tone === "live" ? (
              <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin text-accent motion-reduce:animate-none" />
            ) : null}
            {status.label}
          </p>

          <div className="mt-5 grid w-full grid-cols-2 gap-2">
            <button
              type="button"
              onClick={voice.toggle}
              disabled={voice.busy}
              data-testid="assistant-panel-talk"
              className={cn(
                "flex h-10 items-center justify-center gap-2 rounded-full text-sm font-medium transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60",
                "bg-accent text-accent-foreground hover:bg-accent/90",
              )}
            >
              {voice.on ? <Keyboard aria-hidden className="h-4 w-4" /> : <AudioLines aria-hidden className="h-4 w-4" />}
              {t(voice.on ? "assistant_chat.voice_exit" : "assistant_chat.talk")}
            </button>
            <button
              type="button"
              onClick={() => {
                voice.exit();
                startNewTextChat();
              }}
              data-testid="assistant-panel-new-chat"
              className="flex h-10 items-center justify-center gap-2 rounded-full border border-border bg-card text-sm font-medium text-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <Plus aria-hidden className="h-4 w-4" />
              {t("assistant_chat.new_chat")}
            </button>
          </div>

          <Tabs defaultValue="overview" className="mt-6 w-full">
            <TabsList className="grid w-full grid-cols-2">
              <TabsTrigger value="overview" data-testid="assistant-tab-overview">{t("assistant_chat.tab_overview")}</TabsTrigger>
              <TabsTrigger value="chat" data-testid="assistant-tab-chat">{t("assistant_chat.tab_chat")}</TabsTrigger>
            </TabsList>
            <TabsContent value="overview" className="mt-4 space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <SoulTile />
                <MemoryTile />
              </div>
              <BrainRow />
            </TabsContent>
            <TabsContent value="chat" className="mt-4">
              <ChatFacts />
            </TabsContent>
          </Tabs>
        </div>
      </ScrollArea>
    </aside>
  );
}

function SoulTile() {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const { config, loading, error } = useAgentInstructions();
  const value = loading
    ? null
    : error || !config
      ? t("assistant_chat.memory_unknown")
      : config.exists
        ? fill(t("assistant_chat.soul_chars"), { count: config.char_count.toLocaleString() })
        : t("assistant_chat.soul_empty");
  const file = config?.filename || `${assistantName}.md`;
  return (
    <VividTile
      kind="soul"
      title={t("assistant_chat.soul")}
      subtitle={file}
      value={value}
      hint={fill(t("assistant_chat.soul_open"), { file, name: assistantName })}
      onOpen={() => setActiveSection("agent-instructions")}
      testId="assistant-tile-soul"
    />
  );
}

function MemoryTile() {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const [health, setHealth] = useState<WikiHealthSnapshot | null | undefined>(undefined);
  useEffect(() => {
    let alive = true;
    void fetchWikiHealth().then((snapshot) => {
      if (alive) setHealth(snapshot);
    });
    return () => {
      alive = false;
    };
  }, []);
  const value =
    health === undefined
      ? null
      : health === null
        ? t("assistant_chat.memory_unknown")
        : fill(t("assistant_chat.memory_pages"), { count: health.vault_pages.toLocaleString() });
  // The last time something was written to the wiki, when this run has seen
  // a write; the health record lives in memory and starts empty after a boot.
  const lastWrite = health?.last_write?.ok && health.last_write.ts
    ? new Date(health.last_write.ts * 1000).toLocaleDateString(undefined, { month: "2-digit", day: "2-digit", year: "2-digit" })
    : null;
  return (
    <VividTile
      kind="memory"
      title={t("assistant_chat.memory")}
      subtitle={lastWrite ?? t("assistant_chat.memory_hint")}
      value={value}
      hint={fill(t("assistant_chat.memory_open"), { name: assistantName })}
      onOpen={() => setActiveSection("memory")}
      testId="assistant-tile-memory"
    />
  );
}

function VividTile({
  kind,
  title,
  subtitle,
  value,
  hint,
  onOpen,
  testId,
}: {
  kind: "soul" | "memory";
  title: string;
  subtitle: string;
  /** Null while the read is in flight — drawn as a placeholder bar. */
  value: string | null;
  hint: string;
  onOpen: () => void;
  testId: string;
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      title={hint}
      aria-label={hint}
      data-testid={testId}
      className={cn(
        kind === "soul" ? "jarvis-tile-soul" : "jarvis-tile-memory",
        "group flex aspect-[4/5] flex-col rounded-2xl p-3.5 text-left shadow-float transition-transform duration-200",
        "hover:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-sidebar motion-reduce:transition-none motion-reduce:hover:translate-y-0",
      )}
    >
      <span className="text-lg font-bold uppercase leading-tight tracking-tight">{title}</span>
      <span className="mt-0.5 truncate font-mono text-xs uppercase tracking-wide opacity-85">{subtitle}</span>
      <span className="mt-auto flex items-end justify-between gap-2">
        {value === null ? (
          <span aria-hidden className="h-3 w-16 animate-pulse rounded bg-current opacity-30 motion-reduce:animate-none" />
        ) : (
          <span className="font-mono text-xs font-semibold leading-tight">{value}</span>
        )}
        <ArrowUpRight aria-hidden className="h-4 w-4 shrink-0 opacity-80 transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5 motion-reduce:transition-none" />
      </span>
    </button>
  );
}

/** The provider and model this chat runs on — the composer's picks, read back. */
function BrainRow() {
  const t = useT();
  const draft = useAgentChat((s) => s.draft);
  const catalog = useAgentChat((s) => s.catalog);
  const provider = catalog?.providers.find((p) => p.id === draft.provider);
  const model = provider?.curated_models.find((m) => m.id === draft.model)?.label || draft.model;
  return (
    <div className="rounded-xl border border-border bg-card px-3.5 py-3" data-testid="assistant-brain">
      <div className="text-xs font-medium text-muted-foreground">{t("assistant_chat.brain")}</div>
      {provider ? (
        <div className="mt-1.5 flex min-w-0 items-center gap-2 text-sm text-foreground">
          <ProviderLogo providerId={provider.id} label={provider.label} size="sm" />
          <span className="truncate font-medium">{provider.label}</span>
          <span className="truncate text-muted-foreground">· {model || t("agent_chat.model_default")}</span>
        </div>
      ) : (
        <div className="mt-1.5 text-sm text-muted-foreground">{t("assistant_chat.brain_none")}</div>
      )}
    </div>
  );
}

/** What this conversation holds, counted from its own timeline. */
export function chatFacts(items: TimelineItem[]) {
  let messages = 0;
  let answers = 0;
  let tools = 0;
  let startedMs = 0;
  for (const item of items) {
    const ts = item.type === "turn" ? item.startedMs : item.tsMs;
    if (ts && (!startedMs || ts < startedMs)) startedMs = ts;
    if (item.type === "user" && item.origin !== "control") messages += 1;
    if (item.type === "turn") {
      answers += 1;
      tools += item.blocks.filter((b) => b.kind === "tool").length;
    }
  }
  return { messages, answers, tools, startedMs };
}

function ChatFacts() {
  const t = useT();
  const items = useAgentChat((s) => s.timeline.items);
  const facts = useMemo(() => chatFacts(items), [items]);
  if (!items.length) {
    return <p className="px-1 text-sm text-muted-foreground" data-testid="assistant-chat-empty">{t("assistant_chat.chat_empty")}</p>;
  }
  const started = facts.startedMs
    ? new Date(facts.startedMs).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
    : "—";
  return (
    <dl className="divide-y divide-border rounded-xl border border-border bg-card" data-testid="assistant-chat-facts">
      <Fact label={t("assistant_chat.chat_started")}>{started}</Fact>
      <Fact label={t("assistant_chat.chat_messages")}>{facts.messages}</Fact>
      <Fact label={t("assistant_chat.chat_answers")}>{facts.answers}</Fact>
      <Fact label={t("assistant_chat.chat_tools")}>{facts.tools}</Fact>
    </dl>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 px-3.5 py-2.5 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium tabular-nums text-foreground">{children}</dd>
    </div>
  );
}
