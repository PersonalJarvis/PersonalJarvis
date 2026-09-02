/**
 * The model card's chat column, kept deliberately plain (maintainer,
 * 2026-09-02): bubbles, a time stamp, one pill-shaped composer with a "+"
 * for files and voice, the model and the thinking effort — and nothing else.
 *
 * For Jarvis the column speaks to the SAME store the front page and the
 * voice stage use (`useAgentChatStore`, the "jarvis" surface): one history,
 * whatever a person said or typed anywhere. A "@Name" in the message hands
 * the task to that agent — Jarvis delegates through its router tool and
 * reports back here. The reasoning trail stays readable: a folded
 * "Thinking · 4 s" line above the answer, never a second wall of text.
 *
 * Jarvis' card alone also has a `Voice | Chat` switch (maintainer,
 * 2026-09-02): Jarvis is the one agent a person talks to by voice, so the
 * column can show the front page's voice stage in place — the Jarvis bar,
 * the wake word, the realtime brain — instead of a typed chat. The two run
 * on different brains on purpose: the typed chat is Jarvis' own harness on
 * a provider API behind a key with a per-chat model pick (runner_brain),
 * the voice runs on the realtime tier (`[brain.realtime]`), which no text
 * runner can drive. The header says so while voice is showing.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MessageSquare, Mic, MicOff, Paperclip, Plus, RotateCcw, Send, Square } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { AgentChatStoreProvider, useAgentChat } from "@/components/agentchat/AgentChatStoreContext";
import { ChatAttachmentStrip } from "@/components/agentchat/ChatAttachmentStrip";
import { useChatAttachments } from "@/components/agentchat/useChatAttachments";
import { useComposerDictation } from "@/components/agentchat/useComposerDictation";
import type { ReasoningBlock, TimelineItem, ToolBlock, TurnItem, UserItem } from "@/components/agentchat/reduce";
import { VoiceStage } from "@/components/home/VoiceStage";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { createAgentChatStore, useAgentChatStore } from "@/store/agentChat";
import type { AgentChatSurface } from "@/lib/agentChatApi";

import { AgentSwatch } from "../AgentSwatch";
import type { SocietyAgent } from "../data";

/** A gap this long between messages earns a fresh time stamp. */
const STAMP_GAP_MS = 30 * 60_000;

/** The line appended to a message that names an agent; Jarvis delegates on it. */
const DELEGATE_MARK = "[to jarvis]";

export interface AgentChatPanelProps {
  agent: SocietyAgent;
  roster: SocietyAgent[];
}

/**
 * Every other agent speaks in its OWN canonical chat (`society:<agent_id>`,
 * surface `society`): the backend binds the session to the roster row on
 * request, and this store — one socket for the society surface — opens it.
 * The brain is the roster's choice, so the column shows it instead of the
 * front page's pickers.
 */
const useSocietyChatStore = createAgentChatStore("society");

export function AgentChatPanel({ agent, roster }: AgentChatPanelProps) {
  if (agent.tier === "lead") {
    return (
      <AgentChatStoreProvider store={useAgentChatStore}>
        <JarvisChat agent={agent} roster={roster} />
      </AgentChatStoreProvider>
    );
  }
  if (!agent.chatSessionId) return <NotBoundYet />;
  return (
    <AgentChatStoreProvider store={useSocietyChatStore}>
      <SpecialistChat agent={agent} roster={roster} />
    </AgentChatStoreProvider>
  );
}

function NotBoundYet({ detail }: { detail?: string | null }) {
  const t = useT();
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
      <p className="text-sm font-medium text-foreground">{t("society.card.chat_empty_title")}</p>
      <p className="max-w-[30ch] text-xs text-muted-foreground">{detail ?? t("society.card.chat_empty_hint")}</p>
    </div>
  );
}

async function bindAgentChat(agentId: string): Promise<void> {
  const res = await fetch(`/api/society/agents/${encodeURIComponent(agentId)}/chat`, { method: "POST" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

function SpecialistChat({ agent, roster }: AgentChatPanelProps) {
  const t = useT();
  const items = useAgentChat((s) => s.timeline.items);
  const activeSessionId = useAgentChat((s) => s.activeSessionId);
  const busy = useAgentChat((s) => s.busy);
  const lastError = useAgentChat((s) => s.lastError);
  const loadCatalog = useAgentChat((s) => s.loadCatalog);
  const loadSessions = useAgentChat((s) => s.loadSessions);
  const openSession = useAgentChat((s) => s.openSession);
  const send = useAgentChat((s) => s.send);
  const cancel = useAgentChat((s) => s.cancel);
  const decide = useAgentChat((s) => s.decide);
  const [bindError, setBindError] = useState<string | null>(null);
  const sessionId = agent.chatSessionId;

  // Bind first (idempotent, no spend), then open: the socket needs the row to exist.
  useEffect(() => {
    if (!sessionId) return;
    let alive = true;
    setBindError(null);
    void (async () => {
      try {
        await bindAgentChat(agent.agentId);
        await loadCatalog();
        await loadSessions();
        if (alive) openSession(sessionId);
      } catch (err) {
        if (alive) setBindError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      alive = false;
    };
  }, [agent.agentId, sessionId, loadCatalog, loadSessions, openSession]);

  const mentionable = useMemo(
    () => roster.filter((a) => a.agentId !== agent.agentId && a.tier !== "lead"),
    [roster, agent.agentId],
  );

  if (bindError) return <NotBoundYet detail={`${t("society.card.chat_bind_failed")} (${bindError})`} />;

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="society-chat">
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2 text-xs text-muted-foreground">
        {agent.provider ? <ProviderLogo providerId={agent.provider} label={agent.providerLabel} size="sm" /> : null}
        <span className="truncate text-foreground">{agent.providerLabel || t("society.chat.model_default")}</span>
        {agent.model ? <span className="truncate font-mono">{agent.model}</span> : null}
        {agent.effort ? <span className="ml-auto rounded-full border border-border px-2 py-0.5">{agent.effort}</span> : null}
      </div>
      <Transcript items={items} agent={agent} busy={busy} onDecide={decide} />
      {lastError ? (
        <p role="alert" className="px-4 pb-1 text-xs text-destructive">
          {lastError}
        </p>
      ) : null}
      <Composer
        agent={agent}
        mentionable={mentionable}
        busy={busy || activeSessionId !== sessionId}
        sessionId={activeSessionId}
        cwd=""
        provider={agent.provider}
        surface="society"
        onSend={send}
        onCancel={cancel}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// the chat
// ---------------------------------------------------------------------------

function JarvisChat({ agent, roster }: AgentChatPanelProps) {
  const t = useT();
  const items = useAgentChat((s) => s.timeline.items);
  const sessions = useAgentChat((s) => s.sessions);
  const activeSessionId = useAgentChat((s) => s.activeSessionId);
  const busy = useAgentChat((s) => s.busy);
  const lastError = useAgentChat((s) => s.lastError);
  const draft = useAgentChat((s) => s.draft);
  const loadCatalog = useAgentChat((s) => s.loadCatalog);
  const loadSessions = useAgentChat((s) => s.loadSessions);
  const openSession = useAgentChat((s) => s.openSession);
  const newChat = useAgentChat((s) => s.newChat);
  const send = useAgentChat((s) => s.send);
  const cancel = useAgentChat((s) => s.cancel);
  const decide = useAgentChat((s) => s.decide);

  useEffect(() => {
    void loadCatalog();
    void loadSessions();
  }, [loadCatalog, loadSessions]);

  // The front page's current conversation, or the latest one when the card
  // opens before the front page ever did.
  useEffect(() => {
    if (activeSessionId || sessions.length === 0) return;
    openSession(sessions[0].session_id);
  }, [activeSessionId, sessions, openSession]);

  const mentionable = useMemo(() => roster.filter((a) => a.tier !== "lead"), [roster]);

  // Voice or typed — Jarvis' card only. The other agents have no voice: the
  // wake word, the realtime brain and the microphone belong to the lead.
  const [mode, setMode] = useState<JarvisCardMode>(lastJarvisCardMode);
  const pickMode = (next: JarvisCardMode) => {
    lastJarvisCardMode = next;
    setMode(next);
  };

  if (mode === "voice") {
    return (
      <div className="flex h-full min-h-0 flex-col" data-testid="society-chat" data-mode="voice">
        <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2">
          <JarvisModeSwitch mode={mode} onPick={pickMode} />
          <span className="min-w-0 truncate text-xs text-muted-foreground" title={t("society.chat.voice_note")}>
            {t("society.chat.voice_note")}
          </span>
        </div>
        <VoiceStage />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="society-chat" data-mode="chat">
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2">
        <JarvisModeSwitch mode={mode} onPick={pickMode} />
        <ModelPicker />
        <EffortPicker />
        <button
          type="button"
          onClick={newChat}
          title={t("society.chat.new_chat")}
          aria-label={t("society.chat.new_chat")}
          className="ml-auto rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground"
        >
          <RotateCcw className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>
      <Transcript items={items} agent={agent} busy={busy} onDecide={decide} />
      {lastError ? (
        <p role="alert" className="px-4 pb-1 text-xs text-destructive">
          {lastError}
        </p>
      ) : null}
      <Composer
        agent={agent}
        mentionable={mentionable}
        busy={busy}
        sessionId={activeSessionId}
        cwd={draft.cwd}
        provider={draft.provider}
        onSend={send}
        onCancel={cancel}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// voice | chat — the lead's card only
// ---------------------------------------------------------------------------

type JarvisCardMode = "chat" | "voice";

/** Remembered for the app session, so a card reopened stays on the half you last used. */
let lastJarvisCardMode: JarvisCardMode = "chat";

/**
 * The same `Voice | Chat` idea as the sidebar's switch, scoped to the card:
 * it changes what THIS column shows and leaves the front page's own choice
 * alone. Voice is the front page's voice stage itself (the Jarvis bar, the
 * wake word, the realtime brain) — one voice, shown in a second place, never
 * a second microphone.
 */
function JarvisModeSwitch({ mode, onPick }: { mode: JarvisCardMode; onPick: (m: JarvisCardMode) => void }) {
  const t = useT();
  const tab = (value: JarvisCardMode, icon: React.ReactNode, label: string) => (
    <button
      type="button"
      role="tab"
      aria-selected={mode === value}
      data-testid={`society-jarvis-mode-${value}`}
      onClick={() => onPick(value)}
      className={cn(
        "flex items-center gap-1 rounded-[5px] px-2 py-0.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        mode === value ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground",
      )}
    >
      {icon}
      {label}
    </button>
  );
  return (
    <div
      role="tablist"
      aria-label={t("society.chat.mode_hint")}
      className="grid shrink-0 grid-cols-2 gap-0.5 rounded-md border border-border bg-background p-0.5"
    >
      {tab("voice", <Mic aria-hidden className="h-3 w-3" />, t("society.chat.mode_voice"))}
      {tab("chat", <MessageSquare aria-hidden className="h-3 w-3" />, t("society.chat.mode_chat"))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// pickers
// ---------------------------------------------------------------------------

function ModelPicker() {
  const t = useT();
  const draft = useAgentChat((s) => s.draft);
  const providerOptions = useAgentChat((s) => s.providerOptions);
  const providerById = useAgentChat((s) => s.providerById);
  const liveModels = useAgentChat((s) => s.liveModels);
  const loadModels = useAgentChat((s) => s.loadModels);
  const setDraft = useAgentChat((s) => s.setDraft);
  const locks = useAgentChat((s) => s.locks);
  const [open, setOpen] = useState(false);
  const [providerId, setProviderId] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  const current = providerById(draft.provider);
  const chosen = providerId ? providerById(providerId) : current;
  const providers = providerOptions();
  const models = useMemo(() => {
    if (!chosen) return [];
    const seen = new Set<string>();
    return [...(liveModels[chosen.id] ?? []), ...chosen.curated_models].filter((m) => {
      if (seen.has(m.id)) return false;
      seen.add(m.id);
      return true;
    });
  }, [chosen, liveModels]);

  useEffect(() => {
    if (open && chosen && chosen.models_source === "live") void loadModels(chosen.id);
  }, [open, chosen, loadModels]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const label = current ? current.label : t("society.chat.model_default");
  const modelLabel = draft.model || t("society.chat.model_provider_default");
  const locked = locks?.provider ?? locks?.model;

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        disabled={Boolean(locked)}
        title={locked ?? t("society.chat.model")}
        onClick={() => {
          setProviderId(current?.id ?? providers[0]?.id ?? null);
          setOpen((v) => !v);
        }}
        className="flex max-w-[260px] items-center gap-1.5 rounded-full border border-border px-2 py-1 text-xs text-foreground hover:bg-secondary disabled:opacity-60"
      >
        {current ? <ProviderLogo providerId={current.id} label={current.label} size="sm" /> : null}
        <span className="truncate">{label}</span>
        <span className="truncate font-mono text-xs text-muted-foreground">{modelLabel}</span>
      </button>
      {open ? (
        <div className="absolute left-0 top-full z-20 mt-1 flex w-[440px] max-w-[80vw] overflow-hidden rounded-lg border border-border bg-popover shadow-float">
          <ul className="max-h-72 w-1/2 overflow-y-auto border-r border-border py-1">
            {providers.map((p) => (
              <li key={p.id}>
                <button
                  type="button"
                  onClick={() => setProviderId(p.id)}
                  className={cn(
                    "flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs hover:bg-secondary",
                    chosen?.id === p.id && "bg-secondary",
                    !p.connected && "opacity-50",
                  )}
                  title={p.connected ? p.label : t("society.card.not_connected")}
                >
                  <ProviderLogo providerId={p.id} label={p.label} size="sm" />
                  <span className="truncate">{p.label}</span>
                </button>
              </li>
            ))}
          </ul>
          <ul className="max-h-72 w-1/2 overflow-y-auto py-1">
            {chosen ? (
              <li>
                <button
                  type="button"
                  onClick={() => {
                    void setDraft({ provider: chosen.id, model: "" });
                    setOpen(false);
                  }}
                  className={cn(
                    "w-full px-2.5 py-1.5 text-left text-xs hover:bg-secondary",
                    draft.provider === chosen.id && !draft.model && "bg-secondary",
                  )}
                >
                  {t("society.chat.model_provider_default")}
                </button>
              </li>
            ) : null}
            {models.map((m) => (
              <li key={m.id}>
                <button
                  type="button"
                  onClick={() => {
                    void setDraft({ provider: chosen?.id ?? draft.provider, model: m.id });
                    setOpen(false);
                  }}
                  title={m.note}
                  className={cn(
                    "w-full px-2.5 py-1.5 text-left text-xs hover:bg-secondary",
                    draft.provider === chosen?.id && draft.model === m.id && "bg-secondary",
                  )}
                >
                  <span className="block truncate">{m.label}</span>
                  {m.note ? <span className="block truncate text-xs text-muted-foreground">{m.note}</span> : null}
                </button>
              </li>
            ))}
            {chosen && models.length === 0 ? (
              <li className="px-2.5 py-1.5 text-xs text-muted-foreground">{t("society.chat.models_loading")}</li>
            ) : null}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function EffortPicker() {
  const t = useT();
  const draft = useAgentChat((s) => s.draft);
  const providerById = useAgentChat((s) => s.providerById);
  const setDraft = useAgentChat((s) => s.setDraft);
  const locks = useAgentChat((s) => s.locks);
  const provider = providerById(draft.provider);
  const levels = provider?.effort_levels ?? [];
  if (levels.length === 0) return null;
  return (
    <div role="radiogroup" aria-label={t("society.chat.effort")} className="inline-flex rounded-full border border-border p-0.5">
      {levels.map((level) => {
        const value = level || "";
        const on = (draft.effort || "") === value;
        return (
          <button
            key={level || "default"}
            type="button"
            role="radio"
            aria-checked={on}
            disabled={Boolean(locks?.effort)}
            onClick={() => void setDraft({ effort: value })}
            className={cn(
              "rounded-full px-2 py-0.5 text-xs capitalize transition-colors disabled:opacity-60",
              on ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {level || t("society.chat.effort_default")}
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// transcript
// ---------------------------------------------------------------------------

function Transcript({
  items,
  agent,
  busy,
  onDecide,
}: {
  items: TimelineItem[];
  agent: SocietyAgent;
  busy: boolean;
  onDecide: (approvalId: string, decision: "allow" | "deny") => Promise<void>;
}) {
  const t = useT();
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [items.length, busy]);

  if (items.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
        <AgentSwatch agent={agent} size={56} />
        <p className="text-sm font-medium text-foreground">{t("society.chat.empty_title").replace("{0}", agent.name)}</p>
        <p className="max-w-[32ch] text-xs text-muted-foreground">{t("society.chat.empty_hint")}</p>
      </div>
    );
  }

  let lastStamp = 0;
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
      <div className="flex flex-col gap-2">
        {items.map((item) => {
          const ts = item.type === "user" ? item.tsMs : item.type === "turn" ? item.startedMs : 0;
          const stamp = ts && ts - lastStamp > STAMP_GAP_MS ? ts : 0;
          if (stamp) lastStamp = ts;
          return (
            <div key={item.id} className="flex flex-col gap-2">
              {stamp ? <TimeStamp ms={stamp} /> : null}
              {item.type === "user" ? (
                <UserBubble item={item} />
              ) : item.type === "turn" ? (
                <TurnBubble item={item} onDecide={onDecide} />
              ) : (
                <p className="self-start rounded-2xl bg-destructive/10 px-3 py-2 text-xs text-destructive">{item.text}</p>
              )}
            </div>
          );
        })}
        <div ref={bottom} />
      </div>
    </div>
  );
}

function TimeStamp({ ms }: { ms: number }) {
  const t = useT();
  const date = new Date(ms);
  const today = new Date().toDateString() === date.toDateString();
  const time = date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  const day = today ? t("society.chat.today") : date.toLocaleDateString(undefined, { day: "2-digit", month: "short" });
  return <p className="my-1 text-center text-xs text-muted-foreground">{`${day} ${time}`}</p>;
}

/** What the person typed, without the delegation line the composer added. */
function visibleUserText(text: string): string {
  return text
    .split("\n")
    .filter((line) => !line.trimStart().startsWith(DELEGATE_MARK))
    .join("\n")
    .trimEnd();
}

function UserBubble({ item }: { item: UserItem }) {
  return (
    <div className="flex max-w-[85%] flex-col items-end gap-1 self-end">
      <div className="whitespace-pre-wrap rounded-2xl rounded-br-md bg-secondary px-3.5 py-2 text-sm leading-relaxed text-foreground">
        {visibleUserText(item.text)}
      </div>
      {item.attachments.length > 0 ? (
        <div className="flex flex-wrap justify-end gap-1">
          {item.attachments.map((a) => (
            <span key={a.name} className="rounded-full border border-border px-2 py-0.5 text-xs text-muted-foreground">
              {a.name}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function TurnBubble({
  item,
  onDecide,
}: {
  item: TurnItem;
  onDecide: (approvalId: string, decision: "allow" | "deny") => Promise<void>;
}) {
  const t = useT();
  const running = item.status === "running";
  return (
    <div className="flex max-w-[88%] flex-col gap-1.5 self-start">
      {item.blocks.map((block) => {
        if (block.kind === "reasoning") return <Thinking key={block.id} block={block} />;
        if (block.kind === "tool") return <ToolLine key={block.callId} block={block} onDecide={onDecide} />;
        if (!block.text.trim()) return null;
        return (
          <div
            key={block.id}
            className="society-prose rounded-2xl rounded-bl-md bg-popover px-3.5 py-2 text-sm leading-relaxed text-foreground"
          >
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{block.text}</ReactMarkdown>
          </div>
        );
      })}
      {running && item.blocks.every((b) => b.kind !== "text") ? (
        <div className="flex items-center gap-1 rounded-2xl rounded-bl-md bg-popover px-3.5 py-2.5" aria-label={t("society.chat.thinking")}>
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-muted-foreground" />
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-muted-foreground [animation-delay:150ms]" />
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-muted-foreground [animation-delay:300ms]" />
        </div>
      ) : null}
      {item.error ? <p className="text-xs text-destructive">{item.error}</p> : null}
    </div>
  );
}

function Thinking({ block }: { block: ReasoningBlock }) {
  const t = useT();
  const seconds = block.durationMs !== null ? Math.max(1, Math.round(block.durationMs / 1000)) : null;
  const label = block.live
    ? t("society.chat.thinking")
    : seconds !== null
      ? t("society.chat.thought_for").replace("{0}", String(seconds))
      : t("society.chat.thought");
  return (
    <details className="group text-xs text-muted-foreground">
      <summary className="cursor-pointer select-none list-none px-1 hover:text-foreground">
        <span className={cn(block.live && "animate-pulse")}>{label}</span>
      </summary>
      {block.text.trim() ? (
        <div className="mt-1 whitespace-pre-wrap rounded-xl border border-border px-3 py-2 text-xs leading-relaxed">
          {block.text}
        </div>
      ) : (
        <p className="px-3 py-1 text-xs italic">{t("society.chat.thought_hidden")}</p>
      )}
    </details>
  );
}

function ToolLine({
  block,
  onDecide,
}: {
  block: ToolBlock;
  onDecide: (approvalId: string, decision: "allow" | "deny") => Promise<void>;
}) {
  const t = useT();
  const pending = block.approval && block.approval.decision === null;
  const state = block.isError
    ? t("society.chat.tool_failed")
    : block.output === null && !pending
      ? t("society.chat.tool_running")
      : pending
        ? t("society.chat.tool_waiting")
        : t("society.chat.tool_done");
  return (
    <div className="flex flex-wrap items-center gap-2 px-1 text-xs text-muted-foreground">
      <span className="font-mono">{block.name}</span>
      <span>· {state}</span>
      {pending && block.approval ? (
        <span className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => void onDecide(block.approval!.approvalId, "allow")}
            className="rounded-full border border-border px-2 py-0.5 text-foreground hover:bg-secondary"
          >
            {t("society.chat.approve")}
          </button>
          <button
            type="button"
            onClick={() => void onDecide(block.approval!.approvalId, "deny")}
            className="rounded-full border border-border px-2 py-0.5 hover:bg-secondary"
          >
            {t("society.chat.deny")}
          </button>
        </span>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// composer
// ---------------------------------------------------------------------------

interface ComposerProps {
  agent: SocietyAgent;
  mentionable: SocietyAgent[];
  busy: boolean;
  sessionId: string | null;
  cwd: string;
  provider: string;
  /** Which chat surface the attachments belong to (the front page by default). */
  surface?: AgentChatSurface;
  onSend: (text: string, attachments?: ReturnType<typeof useChatAttachments>["attachments"]) => Promise<void>;
  onCancel: () => Promise<void>;
}

function Composer({ agent, mentionable, busy, sessionId, cwd, provider, surface = "jarvis", onSend, onCancel }: ComposerProps) {
  const t = useT();
  const [value, setValue] = useState("");
  const [plusOpen, setPlusOpen] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [mention, setMention] = useState<{ query: string; start: number } | null>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const attachments = useChatAttachments({ sessionId, cwd, provider, surface }, (message) => setProblem(message));
  const dictation = useComposerDictation(value, setValue);

  const resize = useCallback(() => {
    const el = textarea.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, []);
  useEffect(resize, [value, resize]);

  const onChange = (next: string, caret: number) => {
    setValue(next);
    const before = next.slice(0, caret);
    const m = /(?:^|\s)@([^\s@]*)$/.exec(before);
    setMention(m && mentionable.length > 0 ? { query: m[1].toLowerCase(), start: caret - m[1].length - 1 } : null);
  };

  const matches = useMemo(
    () => (mention ? mentionable.filter((a) => a.name.toLowerCase().startsWith(mention.query)).slice(0, 6) : []),
    [mention, mentionable],
  );

  const insertMention = (name: string) => {
    if (!mention) return;
    const el = textarea.current;
    const caret = el?.selectionStart ?? value.length;
    const next = `${value.slice(0, mention.start)}@${name} ${value.slice(caret)}`;
    setValue(next);
    setMention(null);
    requestAnimationFrame(() => el?.focus());
  };

  const submit = async () => {
    const text = value.trim();
    if (!text || busy) return;
    const named = mentionable.filter((a) => text.includes(`@${a.name}`));
    const hint = named
      .map((a) => `${DELEGATE_MARK} ${t("society.chat.delegate_line").replace("{0}", a.name).replace("{1}", a.agentId)}`)
      .join("\n");
    setValue("");
    setMention(null);
    setProblem(null);
    try {
      await onSend(hint ? `${text}\n\n${hint}` : text, attachments.attachments);
      attachments.clear();
    } catch (err) {
      setProblem(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="shrink-0 border-t border-border px-3 pb-3 pt-2">
      {problem ? <p className="mb-1 px-1 text-xs text-destructive">{problem}</p> : null}
      <ChatAttachmentStrip attachments={attachments.attachments} analyzing={attachments.analyzing} onRemove={attachments.remove} />
      {matches.length > 0 ? (
        <ul className="mb-1 flex flex-wrap gap-1 px-1" role="listbox" aria-label={t("society.chat.mention_hint")}>
          {matches.map((a) => (
            <li key={a.agentId}>
              <button
                type="button"
                role="option"
                aria-selected={false}
                onClick={() => insertMention(a.name)}
                className="flex items-center gap-1.5 rounded-full border border-border px-2 py-0.5 text-xs text-foreground hover:bg-secondary"
              >
                <AgentSwatch agent={a} size={16} />
                {a.name}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <div
        className={cn(
          "relative flex items-end gap-1 rounded-[22px] border border-border bg-background px-1.5 py-1",
          attachments.dragging && "border-border-strong",
        )}
        {...attachments.dragHandlers}
      >
        <div className="relative">
          <button
            type="button"
            onClick={() => setPlusOpen((v) => !v)}
            aria-label={t("society.chat.more")}
            aria-expanded={plusOpen}
            className="flex h-8 w-8 items-center justify-center rounded-full text-muted-foreground hover:bg-secondary hover:text-foreground"
          >
            <Plus className={cn("h-4 w-4 transition-transform", plusOpen && "rotate-45")} aria-hidden />
          </button>
          {plusOpen ? (
            <div className="absolute bottom-full left-0 z-20 mb-1 w-52 overflow-hidden rounded-lg border border-border bg-popover py-1 shadow-float">
              <button
                type="button"
                onClick={() => {
                  setPlusOpen(false);
                  fileInput.current?.click();
                }}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-foreground hover:bg-secondary"
              >
                <Paperclip className="h-3.5 w-3.5" aria-hidden />
                {t("society.chat.attach")}
              </button>
              <button
                type="button"
                onClick={() => {
                  setPlusOpen(false);
                  dictation.toggle();
                }}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-foreground hover:bg-secondary"
              >
                <Mic className="h-3.5 w-3.5" aria-hidden />
                {dictation.dictating ? t("society.chat.stop_recording") : t("society.chat.record")}
              </button>
            </div>
          ) : null}
          <input
            ref={fileInput}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              if (files.length) void attachments.attachFiles(files);
              e.target.value = "";
            }}
          />
        </div>
        <textarea
          ref={textarea}
          value={value}
          rows={1}
          placeholder={t("society.chat.placeholder").replace("{0}", agent.name)}
          onChange={(e) => onChange(e.target.value, e.target.selectionStart ?? e.target.value.length)}
          onPaste={attachments.onPaste}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              if (matches.length > 0 && mention) insertMention(matches[0].name);
              else void submit();
            }
            if (e.key === "Escape" && mention) setMention(null);
          }}
          className="max-h-[180px] min-h-[32px] flex-1 resize-none bg-transparent px-1 py-1.5 text-sm leading-relaxed text-foreground placeholder:text-muted-foreground focus:outline-none"
        />
        <button
          type="button"
          onClick={dictation.toggle}
          aria-label={dictation.dictating ? t("society.chat.stop_recording") : t("society.chat.record")}
          aria-pressed={dictation.dictating}
          className={cn(
            "flex h-8 w-8 items-center justify-center rounded-full text-muted-foreground hover:bg-secondary hover:text-foreground",
            dictation.dictating && "bg-destructive/15 text-destructive",
          )}
        >
          {dictation.dictating ? <MicOff className="h-4 w-4" aria-hidden /> : <Mic className="h-4 w-4" aria-hidden />}
        </button>
        {busy ? (
          <button
            type="button"
            onClick={() => void onCancel()}
            aria-label={t("society.chat.stop")}
            className="flex h-8 w-8 items-center justify-center rounded-full bg-secondary text-foreground hover:bg-popover"
          >
            <Square className="h-3.5 w-3.5" aria-hidden />
          </button>
        ) : (
          <button
            type="button"
            onClick={() => void submit()}
            disabled={!value.trim()}
            aria-label={t("society.chat.send")}
            className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-primary-foreground disabled:opacity-40"
          >
            <Send className="h-3.5 w-3.5" aria-hidden />
          </button>
        )}
      </div>
    </div>
  );
}
