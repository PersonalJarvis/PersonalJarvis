import { PairConversationBoundary } from "@/components/agentchat/PairConversation";
import { InternalMessageBubble } from "@/components/agentchat/InternalMessageBubble";
/**
 * The model card's chat column, kept deliberately plain (maintainer,
 * 2026-09-02): bubbles, a time stamp, one pill-shaped composer with a "+"
 * for files and voice, the model and the thinking effort — and nothing else.
 *
 * For Jarvis the column speaks to the SAME store the front page and the
 * voice stage use (`useAgentChatStore`, the "jarvis" surface): one history,
 * whatever a person said or typed anywhere. "@Name" hands the task to that
 * agent; "@gmail" (and the other catalog tags) pins that plugin, MCP server
 * or tool for the turn. The shared work trace preserves the order of thoughts, tools and replies.
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
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { MessageSquare, Mic, Paperclip, Plus, RotateCcw, Send, Square } from "lucide-react";
import { ChatMarkdown, MediaPreview, mediaKind } from "@/components/agentchat/ChatMarkdown";

import { AgentChatStoreProvider, useAgentChat } from "@/components/agentchat/AgentChatStoreContext";
import { ChatAttachmentStrip } from "@/components/agentchat/ChatAttachmentStrip";
import { ScrollToEndButton } from "@/components/ui/scroll-to-end-button";
import { useStickToBottom } from "@/hooks/useStickToBottom";
import { ComposerChipField, type ComposerChipFieldHandle } from "@/components/agentchat/ComposerChipField";
import { MessageWithChips } from "@/components/agentchat/ToolChoiceChips";
import { choiceToken } from "@/components/agentchat/composerChips";
import { useChatAttachments } from "@/components/agentchat/useChatAttachments";
import { DictationStatus } from "@/components/agentchat/DictationStatus";
import { useComposerDictation } from "@/components/agentchat/useComposerDictation";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";
import { startNewVoiceRun } from "@/lib/chatsApi";
import type {
  NoticeItem,
  TimelineItem,
  TurnItem,
  UserItem,
} from "@/components/agentchat/reduce";
import { TurnTrace } from "@/components/agentchat/WorkTrace";
import { VoiceStage } from "@/components/home/VoiceStage";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { createAgentChatStore, useAgentChatStore } from "@/store/agentChat";
import type { AgentChatSurface, ApprovalDecision } from "@/lib/agentChatApi";

import { AgentSwatch } from "../AgentSwatch";
import { useResolveProposal, useSocietyCapabilities, type SocietyAgent } from "../data";
import { MentionPicker } from "./MentionPicker";
import { AgentModelPicker } from "./AgentModelPicker";
import { mentionChoice, messageChoices } from "./mentionChoices";
import {
  buildMentionCatalog,
  filterMentions,
  mentionToken,
  mentionsInText,
  type MentionItem,
} from "./mentionItems";

/** A gap this long between messages earns a fresh time stamp. */
const STAMP_GAP_MS = 30 * 60_000;

/**
 * The chat owns six eighths of the agent card, which is far wider than a line
 * of prose should ever be. Transcript and composer share this one measure so
 * the column reads like a chat instead of a stretched log; the panes, borders
 * and scrollbars still span the full width.
 */
const CHAT_MEASURE = "mx-auto w-full max-w-[820px]";

/** The line appended to a message that names an agent; Jarvis delegates on it. */
const DELEGATE_MARK = "[to jarvis]";

/** Specialist mentions identify teammates; they do not change the chat's recipient. */
const MENTION_MARK = "[agent mentions]";

/** The line a message adds when it names a capability: pin those tools for the turn. */
const TOOL_PIN_MARK = "[tools:";

/** The team offer is asked for once per app load; the backend decides the rest. */
let onboardingAsked = false;

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
export const useSocietyChatStore = createAgentChatStore("society");

type JarvisCardMode = "chat" | "voice";

/** The lead card and its history share the voice lifecycle, even while closed. */
export function getJarvisCardMode(): JarvisCardMode {
  return useHomeStore.getState().jarvisCardMode;
}

export function setJarvisCardMode(next: JarvisCardMode): void {
  useHomeStore.getState().setJarvisCardMode(next);
}

export function useJarvisCardMode(): JarvisCardMode {
  return useHomeStore((s) => s.jarvisCardMode);
}
/**
 * The transcript a specialist column may paint. One store serves every
 * specialist, so the chrome (name, rail, composer) can already show the
 * agent you clicked while the socket is still on the previous session —
 * those items must not appear under the new name.
 */
export function itemsForOpenSession(
  sessionId: string | null,
  activeSessionId: string | null,
  items: TimelineItem[],
): TimelineItem[] {
  return sessionId !== null && sessionId === activeSessionId ? items : [];
}

export function AgentChatPanel(props: AgentChatPanelProps) {
  return <PairConversationBoundary key={props.agent.agentId} recipient={{ id: props.agent.agentId, name: props.agent.name }}><AgentChatPanelContent {...props} /></PairConversationBoundary>;
}

function AgentChatPanelContent({ agent, roster }: AgentChatPanelProps) {
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
  const sessionReady = Boolean(sessionId) && activeSessionId === sessionId;
  const visibleItems = itemsForOpenSession(sessionId, activeSessionId, items);

  // Open the agent's own session before the browser paints. Waiting on bind /
  // catalog / sessions left the previous specialist's transcript on screen
  // under the new name.
  useLayoutEffect(() => {
    if (!sessionId) return;
    openSession(sessionId);
  }, [sessionId, openSession]);

  useEffect(() => {
    void loadCatalog();
    void loadSessions();
  }, [loadCatalog, loadSessions]);

  // Bind is idempotent and the session id is already on the roster row; a
  // failure must not replace a chat that is already open.
  useEffect(() => {
    let alive = true;
    setBindError(null);
    void bindAgentChat(agent.agentId).catch((err) => {
      if (alive) setBindError(err instanceof Error ? err.message : String(err));
    });
    return () => {
      alive = false;
    };
  }, [agent.agentId]);

  const mentionable = useMemo(
    () => roster.filter((a) => a.agentId !== agent.agentId && a.tier !== "lead"),
    [roster, agent.agentId],
  );

  return (
    <div
      className="flex h-full min-h-0 flex-col"
      data-testid="society-chat"
      data-session-id={sessionId ?? ""}
      data-session-ready={sessionReady ? "true" : "false"}
    >
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2 text-xs text-muted-foreground">
        {agent.provider ? <ProviderLogo providerId={agent.provider} label={agent.providerLabel} size="sm" /> : null}
        <span className="truncate text-foreground">{agent.providerLabel || t("society.chat.model_default")}</span>
        {agent.model ? <span className="truncate font-mono">{agent.model}</span> : null}
        {agent.effort ? <span className="ml-auto rounded-full border border-border px-2 py-0.5">{agent.effort}</span> : null}
      </div>
      <Transcript key={sessionId ?? agent.agentId} items={visibleItems} agent={agent} roster={roster} onDecide={decide} />
      {lastError && sessionReady ? (
        <p role="alert" className="px-4 pb-1 text-xs text-destructive">
          {lastError}
        </p>
      ) : null}
      {bindError ? (
        <p role="alert" className="px-4 pb-1 text-xs text-destructive">
          {`${t("society.card.chat_bind_failed")} (${bindError})`}
        </p>
      ) : null}
      <Composer
        key={agent.agentId}
        agent={agent}
        mentionable={mentionable}
        busy={busy || !sessionReady}
        sessionId={sessionReady ? activeSessionId : sessionId}
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
  const activeSessionId = useAgentChat((s) => s.activeSessionId);
  const busy = useAgentChat((s) => s.busy);
  const lastError = useAgentChat((s) => s.lastError);
  const draft = useAgentChat((s) => s.draft);
  const loadCatalog = useAgentChat((s) => s.loadCatalog);
  const loadSessions = useAgentChat((s) => s.loadSessions);
  const newChat = useAgentChat((s) => s.newChat);
  const send = useAgentChat((s) => s.send);
  const cancel = useAgentChat((s) => s.cancel);
  const decide = useAgentChat((s) => s.decide);

  useEffect(() => {
    void loadCatalog();
    void loadSessions();
  }, [loadCatalog, loadSessions]);

  // First time the lead's card opens on a fresh society: it offers a team.
  // The backend decides whether anything is offered and remembers that it
  // asked, so this may fire as often as it likes.
  useEffect(() => {
    if (onboardingAsked) return;
    onboardingAsked = true;
    void fetch("/api/society/onboarding/start", { method: "POST" })
      .then(() => loadSessions())
      .catch(() => undefined);
  }, [loadSessions]);

  const setActiveConversation = useEventStore((s) => s.setActiveConversation);
  const setMessages = useEventStore((s) => s.setMessages);
  const voiceState = useEventStore((s) => s.voiceState);
  const freshVoicePending = useHomeStore((s) => s.freshVoicePending);

  useEffect(() => {
    if (!freshVoicePending || voiceState !== "idle" || !useHomeStore.getState().freshVoicePending) return;
    useHomeStore.setState({ freshVoicePending: false });
    // Use the existing reset contract once after hangup, including re-entry
    // after the card was closed. Never interrupt a call started elsewhere.
    void startNewVoiceRun().catch(() => {
      useEventStore.getState().pushToast("error", `${t("sidebar.new_voice_chat")}: ${t("voice_state.error")}`);
    });
  }, [freshVoicePending, voiceState, t]);

  // A null session is an intentional fresh chat. Only an explicit history
  // selection may open an older session; polling must not undo New chat.

  const mentionable = useMemo(() => roster.filter((a) => a.tier !== "lead"), [roster]);

  // Voice or typed — Jarvis' card only. The other agents have no voice: the
  // wake word, the realtime brain and the microphone belong to the lead.
  const mode = useJarvisCardMode();
  const pickMode = setJarvisCardMode;

  // A fresh page: no chat, no spoken thread — the composer below starts it.
  const startFresh = useCallback(() => {
    setActiveConversation("text", null);
    setMessages([]);
    newChat();
  }, [newChat, setActiveConversation, setMessages]);


  if (mode === "voice") {
    return (
      <div className="flex h-full min-h-0 flex-col" data-testid="society-chat" data-mode="voice">
        <div className="grid shrink-0 grid-cols-[1fr_auto_1fr] items-center gap-2 border-b border-border px-3 py-2">
          <span className="min-w-0 truncate text-xs text-muted-foreground" title={t("society.chat.voice_note")}>
            {t("society.chat.voice_note")}
          </span>
          <JarvisModeSwitch mode={mode} onPick={pickMode} />
          <span aria-hidden />
        </div>
        <VoiceStage />
      </div>
    );
  }

  const header = (
    <div className="grid shrink-0 grid-cols-[1fr_auto_1fr] items-center gap-2 border-b border-border px-3 py-2">
      <div className="flex min-w-0 items-center gap-2">
        <ModelPicker />
        <EffortPicker />
      </div>
      <JarvisModeSwitch mode={mode} onPick={pickMode} />
      <div className="flex items-center justify-end">
        <button
          type="button"
          onClick={startFresh}
          title={t("society.chat.new_chat")}
          aria-label={t("society.chat.new_chat")}
          className="rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground"
        >
          <RotateCcw className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>
    </div>
  );

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="society-chat" data-mode="chat">
      {header}
      <Transcript items={items} agent={agent} roster={roster} onDecide={decide} />
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

export function Transcript({
  items,
  agent,
  roster,
  onDecide,
}: {
  items: TimelineItem[];
  agent: SocietyAgent;
  roster: SocietyAgent[];
  onDecide: (approvalId: string, decision: ApprovalDecision) => Promise<void>;
}) {
  const t = useT();
  // Follow the newest while the view sits at the end — the rule every
  // conversation surface shares (hooks/useStickToBottom). This used to scroll
  // a bottom sentinel into view on `[items.length, busy]` only, so a
  // reasoning trace or answer that STREAMS — growing in place, no new item —
  // never pulled the view along and the reader scrolled by hand. The hook
  // watches the content's own size too, so growth follows; scrolled up, the
  // reader keeps their place and gets a button back.
  const { rootRef, contentRef, atEnd, jumpToEnd, follow } = useStickToBottom();
  useLayoutEffect(follow, [follow, items.length]);

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
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div ref={rootRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-3" data-testid="society-transcript">
        <div ref={contentRef} className={cn(CHAT_MEASURE, "flex flex-col gap-2")}>
        {items.map((item) => {
          const ts = item.type === "user" || item.type === "internal" ? item.tsMs : item.type === "turn" ? item.startedMs : 0;
          const stamp = ts && ts - lastStamp > STAMP_GAP_MS ? ts : 0;
          if (stamp) lastStamp = ts;
          return (
            <div key={item.id} className="flex flex-col gap-2">
              {stamp ? <TimeStamp ms={stamp} /> : null}
              {item.type === "internal" ? (
                <InternalMessageBubble
                  item={item}
                  sender={roster.find((a) => a.agentId === item.message.sender_id) ?? null}
                  recipient={agent}
                />
              ) : item.type === "user" ? (
                <UserBubble item={item} />
              ) : item.type === "turn" ? (
                <TurnBubble item={item} onDecide={onDecide} />
              ) : item.type === "notice" ? (
                item.kind === "proposal" ? (
                  <ProposalCard item={item} />
                ) : (
                  <NoticeLine item={item} />
                )
              ) : (
                <p className="self-start rounded-2xl bg-destructive/10 px-3 py-2 text-xs text-destructive">{item.text}</p>
              )}
            </div>
          );
        })}
        </div>
      </div>
      {!atEnd && <ScrollToEndButton onClick={jumpToEnd} testId="society-scroll-end" className="top-auto bottom-3" />}
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

/**
 * A delegated task coming back: the agent's name as the headline, its
 * summary underneath, muted — it is the society reporting, not Jarvis
 * speaking, so it never wears an assistant bubble.
 */
function NoticeLine({ item }: { item: NoticeItem }) {
  const t = useT();
  const headline =
    item.kind === "society_result"
      ? t(item.status === "done" ? "society.chat.result_done" : "society.chat.result_blocked").replace(
          "{0}",
          item.agentName || t("society.chat.result_agent"),
        )
      : item.agentName;
  return (
    <div className="flex max-w-[85%] flex-col gap-0.5 self-start rounded-2xl rounded-bl-md border border-border bg-card px-3.5 py-2 text-xs">
      {headline ? <p className="font-medium text-foreground">{headline}</p> : null}
      {item.text ? <ChatMarkdown text={item.text} className="leading-relaxed text-muted-foreground" /> : null}
    </div>
  );
}

/** One human-readable line per proposal kind, read off the typed payload. */
function proposalDetail(kind: string, payload: Record<string, unknown>): string {
  const list = (v: unknown): string => (Array.isArray(v) ? v.map(String).join(", ") : "");
  switch (kind) {
    case "rule":
      return String(payload.text ?? "");
    case "skill":
      return `${String(payload.name ?? "")}: ${String(payload.goal ?? "")}`;
    case "routine": {
      const schedule = (payload.schedule ?? {}) as Record<string, unknown>;
      const when = Object.entries(schedule)
        .map(([k, v]) => `${k}=${String(v)}`)
        .join(" ");
      return `${String(payload.title ?? "")} — ${when}`;
    }
    case "approval_rule": {
      const parts: string[] = [];
      if (list(payload.require_approval)) parts.push(`ask first: ${list(payload.require_approval)}`);
      if (list(payload.always_allow)) parts.push(`always allow: ${list(payload.always_allow)}`);
      return parts.join(" · ");
    }
    case "focus":
      return list(payload.focus);
    case "team":
      return list(payload.names);
    default:
      return "";
  }
}

/**
 * The agent proposed a change to itself (a standing rule, a skill, a routine,
 * approval rules, its focus). Nothing has changed yet: the person decides here,
 * and the outcome patches this same card.
 */
function ProposalCard({ item }: { item: NoticeItem }) {
  const t = useT();
  const resolve = useResolveProposal();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const kind = String(item.data.proposal_kind ?? "");
  const proposalId = String(item.data.proposal_id ?? "");
  const summary = String(item.data.summary ?? item.text);
  const reason = String(item.data.reason ?? "");
  const payload = (item.data.payload ?? {}) as Record<string, unknown>;
  const detail = proposalDetail(kind, payload);
  const outcome = item.text.includes("\n") ? item.text.slice(item.text.indexOf("\n") + 1) : "";
  // A team offer is a pick list: everyone is proposed, the person keeps the
  // ones they want and the picked names ride along in the decision's note.
  const offered = useMemo(() => {
    const rows = (payload.proposals ?? []) as { name?: string; title?: string }[];
    if (rows.length > 0) return rows.map((r) => ({ name: String(r.name ?? ""), title: String(r.title ?? "") }));
    return ((payload.names ?? []) as string[]).map((n) => ({ name: String(n), title: "" }));
  }, [payload]);
  const [picked, setPicked] = useState<string[] | null>(null);
  const chosen = picked ?? offered.map((o) => o.name);
  const decide = async (approve: boolean) => {
    setBusy(true);
    setError("");
    try {
      await resolve(proposalId, approve, kind === "team" && approve ? chosen.join(", ") : "");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  const resolvedLabel =
    item.resolved === "applied"
      ? t("society.chat.proposal_applied")
      : item.resolved === "rejected"
        ? t("society.chat.proposal_rejected")
        : item.resolved
          ? t("society.chat.proposal_failed")
          : "";
  return (
    <div
      className={cn(
        "flex max-w-[85%] flex-col gap-1.5 self-start rounded-2xl rounded-bl-md border px-3.5 py-2.5 text-xs",
        item.resolved === "applied"
          ? "border-primary/40 bg-primary/5"
          : item.resolved
            ? "border-border bg-card"
            : "border-primary/60 bg-card",
      )}
    >
      <p className="font-medium text-foreground">
        {t("society.chat.proposal_title").replace("{0}", item.agentName || t("society.chat.result_agent"))}
      </p>
      <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
        {kind ? t(`society.chat.proposal_kind_${kind}`) : ""}
      </p>
      {kind === "team" && !item.resolved ? (
        <ul className="flex flex-col gap-1">
          {offered.map((row) => (
            <li key={row.name} className="flex items-center gap-2">
              <input
                type="checkbox"
                id={`${proposalId}-${row.name}`}
                checked={chosen.includes(row.name)}
                disabled={busy}
                onChange={(e) =>
                  setPicked(
                    e.target.checked
                      ? [...chosen, row.name]
                      : chosen.filter((name) => name !== row.name),
                  )
                }
                className="h-3.5 w-3.5 accent-[var(--primary)]"
              />
              <label htmlFor={`${proposalId}-${row.name}`} className="cursor-pointer text-foreground">
                <span className="font-medium">{row.name}</span>
                {row.title ? <span className="text-muted-foreground"> — {row.title}</span> : null}
              </label>
            </li>
          ))}
        </ul>
      ) : (
        <p className="whitespace-pre-wrap leading-relaxed text-foreground">{detail || summary}</p>
      )}
      {reason ? (
        <p className="leading-relaxed text-muted-foreground">
          <span className="font-medium">{t("society.chat.proposal_reason")}: </span>
          {reason}
        </p>
      ) : null}
      {item.resolved ? (
        <p className={cn("font-medium", item.resolved === "failed" ? "text-destructive" : "text-foreground")}>
          {resolvedLabel}
          {outcome ? <span className="font-normal text-muted-foreground"> — {outcome}</span> : null}
        </p>
      ) : (
        <div className="mt-1 flex items-center gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => void decide(true)}
            className="rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            {t("society.chat.proposal_confirm")}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void decide(false)}
            className="rounded-md border border-border px-2.5 py-1 text-xs font-medium text-foreground hover:bg-muted disabled:opacity-50"
          >
            {t("society.chat.proposal_reject")}
          </button>
          <span className="text-muted-foreground">{t("society.chat.proposal_pending")}</span>
        </div>
      )}
      {error ? <p className="text-destructive">{error}</p> : null}
    </div>
  );
}

/** What the person typed, without the routing hints the composer added. */
function visibleUserText(text: string): string {
  return text
    .split("\n")
    .filter((line) => !line.trimStart().startsWith(DELEGATE_MARK))
    .filter((line) => !line.trimStart().startsWith(MENTION_MARK))
    .filter((line) => !line.trimStart().startsWith(TOOL_PIN_MARK))
    .join("\n")
    .trimEnd();
}

export function UserBubble({ item }: { item: UserItem }) {
  const choices = messageChoices(item);
  const text = visibleUserText(item.text);
  return (
    <div className="flex max-w-[85%] flex-col items-end gap-1 self-end">
      <div className="rounded-2xl rounded-br-md bg-secondary px-3.5 py-2 text-sm leading-relaxed text-foreground">
        <MessageWithChips text={text} choices={choices} />
      </div>
      {item.attachments.length > 0 ? (
        <div className="flex flex-wrap justify-end gap-1">
          {item.attachments.map((a) => (
            a.url && (a.kind === "image" || mediaKind(a.url)) ? <MediaPreview key={a.name} src={a.url} label={a.name} kind={mediaKind(a.url) ?? "image"} /> : <span key={a.name} className="rounded-full border border-border px-2 py-0.5 text-xs text-muted-foreground">
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
  onDecide: (approvalId: string, decision: ApprovalDecision) => Promise<void>;
}) {
  return <TurnTrace turn={item} onDecide={onDecide} renderText={(text) => <Prose text={text} />} />;
}

/**
 * Markdown as prose, not as marks.
 *
 * Models write in Markdown — an answer or a thought rendered without a
 * typography scale shows raw `**like this**` and runs its lists together.
 * One scale serves both here: the answer inherits the bubble's ink, the
 * thought passes `muted`, and nothing else differs.
 */
function Prose({ text, muted }: { text: string; muted?: boolean }) {
  return (
    <div
      className={cn(
        "prose prose-neutral max-w-none dark:prose-invert [overflow-wrap:anywhere]",
        muted ? "text-xs leading-relaxed text-muted-foreground" : "text-sm leading-relaxed text-foreground",
        "prose-p:my-1.5 first:prose-p:mt-0 last:prose-p:mb-0",
        "prose-headings:my-2 prose-headings:text-[1em] prose-headings:font-semibold prose-headings:text-foreground",
        "prose-strong:font-semibold prose-strong:text-foreground",
        "prose-ul:my-1.5 prose-ol:my-1.5 prose-li:my-0.5",
        "prose-a:text-foreground prose-a:underline prose-a:underline-offset-2",
        "prose-code:rounded prose-code:bg-secondary prose-code:px-1 prose-code:py-0.5 prose-code:font-mono",
        "prose-code:text-[0.9em] prose-code:font-normal prose-code:before:hidden prose-code:after:hidden",
        "prose-pre:my-2 prose-pre:rounded-xl prose-pre:bg-secondary prose-pre:p-3 prose-pre:text-xs",
        "prose-hr:my-3 prose-blockquote:border-l-2 prose-blockquote:pl-3 prose-blockquote:not-italic",
      )}
    >
      <ChatMarkdown text={text} />
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

export function Composer({ agent, mentionable, busy, sessionId, cwd, provider, surface = "jarvis", onSend, onCancel }: ComposerProps) {
  const t = useT();
  const [modelSaving, setModelSaving] = useState(false);
  const [value, setValue] = useState("");
  const [plusOpen, setPlusOpen] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [mention, setMention] = useState<{ query: string; start: number } | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [selectedTools, setSelectedTools] = useState<MentionItem[]>([]);
  useEffect(() => { setSelectedTools([]); }, [sessionId, agent.agentId]);
  const fieldRef = useRef<ComposerChipFieldHandle>(null);
  const composerRef = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const attachments = useChatAttachments({ sessionId, cwd, provider, surface }, (message) => setProblem(message));
  const dictation = useComposerDictation(value, (next) => {
    const text = typeof next === "function" ? next(value) : next;
    setValue(text);
    fieldRef.current?.setText(text);
  });

  // "@" completes teammates AND the capability catalog — plugins, MCP
  // servers, CLIs, skills, Jarvis tools — on every agent card, including
  // Jarvis'. Naming one pins it for the turn (see `submit`).
  const capabilities = useSocietyCapabilities();
  const catalog = useMemo(
    () => buildMentionCatalog(mentionable, capabilities.data ?? []),
    [mentionable, capabilities.data],
  );
  const matches = useMemo(
    () => (mention ? filterMentions(catalog, mention.query) : []),
    [mention, catalog],
  );

  useEffect(() => {
    setActiveIndex(0);
  }, [mention?.query, mention?.start]);
  useEffect(() => {
    if (activeIndex >= matches.length) setActiveIndex(Math.max(0, matches.length - 1));
  }, [matches.length, activeIndex]);

  const onDraftChange = (draft: { text: string; caret: number }) => {
    setValue(draft.text);
    setMention(mentionToken(draft.text, draft.caret));
  };

  const insertMention = (item: MentionItem) => {
    const field = fieldRef.current;
    const draft = field?.getDraft();
    const start = mention?.start ?? draft?.caret ?? value.length;
    const caret = draft?.caret ?? value.length;
    const before = (draft?.text ?? value).slice(0, start);
    const after = (draft?.text ?? value).slice(caret);
    if (item.agent) {
      const next = `${before}@${item.value} ${after}`;
      field?.hydrate(next, draft?.choices ?? []);
    } else {
      const row = mentionChoice(item);
      const next = `${before}${choiceToken(row)} ${after}`;
      field?.hydrate(next, [...(draft?.choices ?? []), row]);
      setSelectedTools((items) => (items.some((row) => row.key === item.key) ? items : [...items, item]));
    }
    setMention(null);
  };

  const submit = async () => {
    const draft = fieldRef.current?.getDraft();
    const draftText = (draft?.text ?? value).trim();
    const selected = selectedTools;
    const text = draftText;
    if (!text || busy || modelSaving) return;
    const named = mentionsInText(text, catalog);
    const lines: string[] = [];
    if (named.agents.length > 0 && surface === "society") {
      const teammates = named.agents.map((a) => `${JSON.stringify(a.name)} (id ${JSON.stringify(a.agentId)})`).join(", ");
      lines.push(
        `${MENTION_MARK} Current sender: the user. Current recipient: ${JSON.stringify(agent.name)} (id ${JSON.stringify(agent.agentId)}). ` +
        `Mentioned teammates: ${teammates}. For teammate contact requested by the user, use society_message_agent; ` +
        "use kind 'query' when asking for information. If a work assignment is needed, contact Jarvis or an orchestrator with that tool. " +
        "Reply to the user here. An @mention or prose addressed to a teammate does not deliver a message.",
      );
    } else if (surface === "jarvis") {
      lines.push(...named.agents.map(
        (a) => `${DELEGATE_MARK} ${t("society.chat.delegate_line").replace("{0}", a.name).replace("{1}", a.agentId)}`,
      ));
    }
    if (named.pinIds.length > 0) lines.push(`${TOOL_PIN_MARK} ${named.pinIds.join(", ")}]`);
    const hint = lines.join("\n");
    setValue("");
    fieldRef.current?.clear();
    setMention(null);
    setProblem(null);
    try {
      await onSend(hint ? `${text}\n\n${hint}` : text, attachments.attachments);
      setSelectedTools([]);
      attachments.clear();
    } catch (err) {
      setValue(draftText);
      fieldRef.current?.hydrate(draftText, draft?.choices ?? []);
      setSelectedTools(selected);
      setProblem(err instanceof Error ? err.message : String(err));
    }
  };

  const pickerOpen = Boolean(mention) && (matches.length > 0 || (mention?.query.length ?? 0) > 0 || capabilities.isLoading);

  return (
    <div className="shrink-0 border-t border-border px-3 pb-3 pt-2">
      {problem ? <p className="mb-1 px-1 text-xs text-destructive">{problem}</p> : null}
      <div className={CHAT_MEASURE}>
        <ChatAttachmentStrip attachments={attachments.attachments} analyzing={attachments.analyzing} onRemove={attachments.remove} />
      </div>
      <DictationStatus onStop={dictation.stop} className={cn(CHAT_MEASURE, "mb-1.5")} />
      <MentionPicker
        anchorRef={composerRef}
        open={pickerOpen}
        items={matches}
        loading={capabilities.isLoading}
        activeIndex={activeIndex}
        onHover={setActiveIndex}
        onPick={insertMention}
      />
      <div
        ref={composerRef}
        className={cn(
          CHAT_MEASURE,
          "relative flex items-end gap-1 rounded-[22px] border border-border bg-background px-1.5 py-1",
          attachments.dragging && "border-border-strong",
          // The whole composer reads as armed while the mic is open, not just
          // the 32px button someone has to go looking for.
          dictation.dictating && "border-success/40 ring-1 ring-success/25",
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
              <button type="button" onClick={() => {
                setPlusOpen(false);
                fieldRef.current?.insertText("@");
                fieldRef.current?.focus();
              }} className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-foreground hover:bg-secondary">
                <Plus className="h-3.5 w-3.5" aria-hidden />{t("chat_tools.all")}
              </button>
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
        <ComposerChipField
          ref={fieldRef}
          placeholder={t("society.chat.placeholder").replace("{0}", agent.name)}
          disabled={busy}
          onSubmit={() => void submit()}
          onDraftChange={onDraftChange}
          onPasteFiles={attachments.onPaste}
          onKeyDown={(e) => {
            if (!pickerOpen) return false;
            if (e.key === "ArrowDown") {
              e.preventDefault();
              if (matches.length) setActiveIndex((i) => (i + 1) % matches.length);
              return true;
            }
            if (e.key === "ArrowUp") {
              e.preventDefault();
              if (matches.length) setActiveIndex((i) => (i - 1 + matches.length) % matches.length);
              return true;
            }
            if (e.key === "Escape") {
              e.preventDefault();
              setMention(null);
              return true;
            }
            if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
              const item = matches[activeIndex] ?? matches[0];
              if (item) {
                e.preventDefault();
                insertMention(item);
                return true;
              }
              if (e.key === "Tab") return true;
            }
            return false;
          }}
          className="max-h-[180px]"
        />
        {surface === "society" ? <div className="flex h-8 min-w-0 max-w-[40%] shrink-0 items-center">
          <AgentModelPicker key={agent.agentId} agent={agent} busy={busy} onSavingChange={setModelSaving} />
        </div> : null}
        <button
          type="button"
          onClick={dictation.toggle}
          aria-label={dictation.dictating ? t("society.chat.stop_recording") : t("society.chat.record")}
          aria-pressed={dictation.dictating}
          className={cn(
            "flex h-8 w-8 items-center justify-center rounded-full transition-colors",
            dictation.dictating
              ? "bg-secondary text-success motion-safe:animate-jarvis-pulse"
              : "text-muted-foreground hover:bg-secondary hover:text-foreground",
          )}
        >
          {dictation.dictating ? <Square className="h-4 w-4" aria-hidden /> : <Mic className="h-4 w-4" aria-hidden />}
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
            disabled={modelSaving || (!value.trim() && selectedTools.length === 0)}
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
