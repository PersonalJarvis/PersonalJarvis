/**
 * The model card's chat column, kept deliberately plain (maintainer,
 * 2026-09-02): bubbles, a time stamp, one pill-shaped composer with a "+"
 * for files and voice, the model and the thinking effort — and nothing else.
 *
 * For Jarvis the column speaks to the SAME store the front page and the
 * voice stage use (`useAgentChatStore`, the "jarvis" surface): one history,
 * whatever a person said or typed anywhere. A "@Name" in the message hands
 * the task to that agent — Jarvis delegates through its router tool and
 * reports back here. The reasoning trail stays readable: a centred
 * "Thought for 4s" pill above the answer, never a second wall of text.
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
import { Brain, ChevronRight, MessageSquare, Mic, Paperclip, Plus, RotateCcw, Send, Square } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { AgentChatStoreProvider, useAgentChat } from "@/components/agentchat/AgentChatStoreContext";
import { ChatAttachmentStrip } from "@/components/agentchat/ChatAttachmentStrip";
import { useChatAttachments } from "@/components/agentchat/useChatAttachments";
import { DictationStatus } from "@/components/agentchat/DictationStatus";
import { useComposerDictation } from "@/components/agentchat/useComposerDictation";
import type {
  NoticeItem,
  ReasoningBlock,
  TimelineItem,
  ToolBlock,
  TurnItem,
  UserItem,
} from "@/components/agentchat/reduce";
import { formatThoughtDuration } from "@/components/home/TurnSteps";
import { VoiceStage } from "@/components/home/VoiceStage";
import { LiveCore } from "@/components/LiveCore";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { createAgentChatStore, useAgentChatStore } from "@/store/agentChat";
import type { AgentChatSurface, ApprovalDecision } from "@/lib/agentChatApi";

import { AgentSwatch } from "../AgentSwatch";
import { useResolveProposal, type SocietyAgent } from "../data";

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
  onDecide: (approvalId: string, decision: ApprovalDecision) => Promise<void>;
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
      <div className={cn(CHAT_MEASURE, "flex flex-col gap-2")}>
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
      {item.text ? <p className="whitespace-pre-wrap leading-relaxed text-muted-foreground">{item.text}</p> : null}
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
  const decide = async (approve: boolean) => {
    setBusy(true);
    setError("");
    try {
      await resolve(proposalId, approve);
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
      <p className="whitespace-pre-wrap leading-relaxed text-foreground">{detail || summary}</p>
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
  onDecide: (approvalId: string, decision: ApprovalDecision) => Promise<void>;
}) {
  const t = useT();
  const running = item.status === "running";
  return (
    <div className="flex w-full flex-col gap-1.5">
      {item.blocks.map((block) => {
        if (block.kind === "reasoning") return <ReasoningTrace key={block.id} block={block} turnLive={running} />;
        if (block.kind === "tool") return <ToolLine key={block.callId} block={block} onDecide={onDecide} />;
        if (!block.text.trim()) return null;
        return (
          <div
            key={block.id}
            className="max-w-[88%] self-start rounded-2xl rounded-bl-md bg-popover px-3.5 py-2 text-foreground"
          >
            <Prose text={block.text} />
          </div>
        );
      })}
      {running && item.blocks.every((b) => b.kind !== "text") ? (
        <div
          className="flex items-center gap-1 self-start rounded-2xl rounded-bl-md bg-popover px-3.5 py-2.5"
          aria-label={t("society.chat.thinking")}
        >
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-muted-foreground" />
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-muted-foreground [animation-delay:150ms]" />
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-muted-foreground [animation-delay:300ms]" />
        </div>
      ) : null}
      {item.error ? <p className="self-start text-xs text-destructive">{item.error}</p> : null}
    </div>
  );
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
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

/** Live elapsed readout for a running thought; 1 s is fine — the label shows whole seconds. */
function useElapsedMs(startedMs: number | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (startedMs === null) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [startedMs]);
  return startedMs === null ? 0 : Math.max(0, now - startedMs);
}

/**
 * The reasoning trace — the model's thinking, centred above the answer.
 *
 * Modelled on the Claude and ChatGPT desktop apps (maintainer, 2026-09-02:
 * the old `<details>` row was a bare summary with a wall of pre-wrapped text
 * under it). Three states, one shape:
 *
 * WHILE IT THINKS a centred live line — the product's one live core, a
 * shimmering "Thinking for 8s" and a ticking clock — sits over an open card
 * whose text is anchored to its NEWEST lines, so the reader watches the
 * thought arrive instead of chasing a scrollbar. Older lines fade out at the
 * top rather than being cut.
 *
 * ONCE IT CLOSES the line becomes a centred pill: a brain mark, "Thought for
 * 8s", a chevron. It stays open while the turn still works — watching the
 * work is the point of a running turn — and folds itself the moment the turn
 * lands, because a finished conversation should read as its answer.
 *
 * A REDACTED thought (the vendor hides its reasoning) is the same pill
 * without a chevron and without a card: it still says the thinking happened.
 *
 * The trace is centred on purpose: it is not the agent speaking, it is the
 * turn's own state, and the column already places its meta rows — the time
 * stamps — down the middle.
 */
export function ReasoningTrace({ block, turnLive }: { block: ReasoningBlock; turnLive: boolean }) {
  const t = useT();
  // `null` until the person decides; until then the turn's own state decides.
  const [manual, setManual] = useState<boolean | null>(null);
  const text = block.text.trim();
  const live = block.live;
  const elapsed = useElapsedMs(live ? block.startedMs : null);
  const open = live || (text !== "" && (manual ?? turnLive));

  const label = live
    ? t("society.chat.thinking_for").replace("{0}", formatThoughtDuration(elapsed))
    : block.durationMs !== null && block.durationMs > 0
      ? t("society.chat.thought_for").replace("{0}", formatThoughtDuration(block.durationMs))
      : t("society.chat.thought");

  return (
    <div
      className="my-0.5 flex flex-col items-center gap-1.5"
      data-testid="society-reasoning"
      data-state={live ? "live" : text === "" ? "silent" : open ? "open" : "folded"}
    >
      {live ? (
        <div className="flex items-center gap-2 text-xs" role="status" aria-live="polite">
          <LiveCore />
          <span className="thinking-shimmer font-medium tabular-nums">{label}</span>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setManual(!open)}
          aria-expanded={open}
          disabled={text === ""}
          title={text === "" ? t("society.chat.thought_hidden") : undefined}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border border-border/60 bg-secondary/40 px-2.5 py-1",
            "text-xs text-muted-foreground transition-colors",
            text === "" ? "cursor-default" : "hover:border-border hover:bg-secondary hover:text-foreground",
          )}
        >
          <Brain className="h-3.5 w-3.5 shrink-0 opacity-70" aria-hidden />
          <span className="tabular-nums">{label}</span>
          {text === "" ? null : (
            <ChevronRight className={cn("h-3.5 w-3.5 shrink-0 transition-transform", open && "rotate-90")} aria-hidden />
          )}
        </button>
      )}

      {open ? (
        <div className="w-full rounded-2xl border border-border/60 bg-secondary/25 px-4 py-3">
          {live ? <ThoughtTail text={text} /> : <Prose text={text} muted />}
        </div>
      ) : null}
    </div>
  );
}

/**
 * The streaming thought, pinned to its newest lines.
 *
 * A fixed window with the text against its bottom edge: new words push the
 * old ones up and out. Once more has been written than fits, the top fades
 * so the clipping reads as depth rather than as a cut; a thought that fits
 * shows whole and unfaded.
 */
function ThoughtTail({ text }: { text: string }) {
  const outer = useRef<HTMLDivElement>(null);
  const inner = useRef<HTMLDivElement>(null);
  const [clipped, setClipped] = useState(false);
  useLayoutEffect(() => {
    const box = outer.current;
    const body = inner.current;
    if (!box || !body) return;
    setClipped(body.offsetHeight > box.clientHeight + 1);
  }, [text]);
  return (
    <div
      ref={outer}
      className={cn(
        "flex max-h-40 flex-col justify-end overflow-hidden",
        clipped && "[mask-image:linear-gradient(to_bottom,transparent,black_2.5rem)]",
      )}
      data-testid="society-reasoning-tail"
      data-clipped={clipped ? "true" : undefined}
    >
      <div ref={inner}>
        {text ? <Prose text={text} muted /> : null}
      </div>
    </div>
  );
}

function ToolLine({
  block,
  onDecide,
}: {
  block: ToolBlock;
  onDecide: (approvalId: string, decision: ApprovalDecision) => Promise<void>;
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
            onClick={() => void onDecide(block.approval!.approvalId, "allow_always")}
            title={t("society.chat.allow_always")}
            className="rounded-full border border-border px-2 py-0.5 text-foreground hover:bg-secondary"
          >
            {t("society.chat.allow_always")}
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
      <div className={CHAT_MEASURE}>
        <ChatAttachmentStrip attachments={attachments.attachments} analyzing={attachments.analyzing} onRemove={attachments.remove} />
      </div>
      {matches.length > 0 ? (
        <ul className={cn(CHAT_MEASURE, "mb-1 flex flex-wrap gap-1 px-1")} role="listbox" aria-label={t("society.chat.mention_hint")}>
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
      <DictationStatus onStop={dictation.stop} className={cn(CHAT_MEASURE, "mb-1.5")} />
      <div
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
