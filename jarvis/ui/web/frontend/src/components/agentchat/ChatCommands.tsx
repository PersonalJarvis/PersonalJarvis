import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Loader2, Pause, Play, X } from "lucide-react";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { createAgentChatSession, type ChatAttachment } from "@/lib/agentChatApi";
import { CHAT_COMMAND_NAMES, fetchChatCommands, parseChatCommand, runChatCommand,
  type ChatCommand, type ChatCommandName, type ChatCommandResult, type ChatControlState, type ChatSearchHit } from "@/lib/chatControlApi";
import { useAgentChat, useAgentChatApi } from "./AgentChatStoreContext";
import { restoreTranscriptView, useTranscriptView } from "../society/chat/useTranscriptView";
import { AgentRoutinesList } from "../society/card/AgentRoutinesList";

interface Options { value: string; setValue: (value: string) => void; onModel: () => void; onClear?: () => void; agentId?: string; attachments?: ChatAttachment[]; attachmentsBusy?: boolean; onAttachmentsSent?: () => void }
const LOCAL = new Set<ChatCommandName>(["help", "clear", "history", "model", "routines"]);

export function useChatCommands({ value, setValue, onModel, onClear, agentId = "jarvis", attachments = [], attachmentsBusy = false, onAttachmentsSent }: Options) {
  const t = useT();
  const localeValue = t("slash.locale");
  const locale = ["en", "de", "es"].includes(localeValue) ? localeValue : "en";
  const store = useAgentChatApi();
  const sid = useAgentChat((s) => s.activeSessionId);
  const surface = useAgentChat((s) => s.surface);
  const items = useAgentChat((s) => s.timeline.items);
  const liveControl = useAgentChat((s) => s.timeline.control);
  const enabled = surface === "jarvis" || surface === "society";
  const view = useTranscriptView(sid, items);
  const [catalog, setCatalog] = useState<ChatCommand[]>([]);
  const [state, setState] = useState<ChatControlState | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [help, setHelp] = useState(false);
  const [panel, setPanel] = useState<"status" | "find" | "routines" | null>(null);
  const [hits, setHits] = useState<ChatSearchHit[]>([]);
  const [result, setResult] = useState<ChatCommandResult | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const sending = useRef(false);
  const pending = useRef(new Map<string, string>());
  const currentSid = useRef(sid);
  currentSid.current = sid;

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    setState(null); setPanel(null); setError(""); setResult(null);
    void fetchChatCommands(sid, controller.signal).then((data) => {
      if (!controller.signal.aborted) {
        setCatalog(Array.isArray(data.commands) ? data.commands : []);
        setState((old) => data.state && (!old || data.state.revision >= old.revision) ? data.state : old);
      }
    }).catch(() => {
      if (!controller.signal.aborted) setCatalog([]);
    });
    return () => controller.abort();
  }, [sid, enabled]);

  useEffect(() => {
    if (liveControl?.session_id === sid) setState((old) => !old || liveControl.revision >= old.revision ? liveControl : old);
  }, [liveControl, sid]);

  const commands = useMemo(() => CHAT_COMMAND_NAMES.map((name) => catalog.find((row) => row.name === name) ?? {
    name, kind: LOCAL.has(name) ? "local" as const : "control" as const,
    example: `/${name}`, available: LOCAL.has(name), reason: LOCAL.has(name) ? "" : t("slash.backend_unavailable"),
  }), [catalog, t]);
  const token = /^\/([a-z]*)$/.exec(value.trimStart());
  const matches = commands.filter((row) => help || row.name.startsWith(token?.[1] ?? ""));
  const open = enabled && (help || Boolean(token && matches.length));
  useEffect(() => { setActiveIndex(0); }, [value, help]);
  useEffect(() => { if (value) setHelp(false); }, [value]);

  async function ensureSession(): Promise<string> {
    const current = store.getState();
    if (current.activeSessionId) return current.activeSessionId;
    if (surface !== "jarvis") throw new Error(t("slash.session_loading"));
    const draft = current.draft;
    const session = await createAgentChatSession({ provider: draft.provider, model: draft.model,
      effort: draft.effort, cwd: draft.cwd, permission_mode: draft.permissionMode, surface });
    if (store.getState().activeSessionId) throw new Error(t("slash.session_changed"));
    store.setState((s) => ({ sessions: [session, ...s.sessions] }));
    store.getState().openSession(session.session_id);
    return session.session_id;
  }

  async function remote(name: ChatCommandName, args: string): Promise<ChatCommandResult> {
    const target = await ensureSession();
    const attached = ["plan", "recap", "review", "remember", "message"].includes(name) || name === "goal" && args && args !== "clear" ? attachments : [];
    const key = JSON.stringify([target, name, args, attached]);
    const id = pending.current.get(key) ?? crypto.randomUUID();
    pending.current.set(key, id);
    const response = await runChatCommand(target, name, args, id, locale, attached);
    pending.current.delete(key);
    if (store.getState().activeSessionId === target) {
      setState(response.state); setResult(response);
      store.setState((s) => ({ draft: { ...s.draft, permissionMode: response.state.permission_mode },
        activeSession: s.activeSession ? { ...s.activeSession, permission_mode: response.state.permission_mode } : null }));
    }
    if (response.status === "failed") throw new Error(response.error);
    if (attached.length) onAttachmentsSent?.();
    return response;
  }

  async function execute(text: string): Promise<boolean> {
    const command = enabled ? parseChatCommand(text) : null;
    if (!command) return false;
    if (attachmentsBusy && ["plan", "review", "recap", "goal"].includes(command.name) && command.args !== "clear") {
      setError(t("slash.attachments_loading")); return true;
    }
    if (sending.current && command.name !== "stop") return true;
    if (LOCAL.has(command.name) && command.args) { setError(t("slash.no_arguments")); return true; }
    sending.current = true; setBusy(true); setError(""); setHelp(false); setResult(null);
    try {
      switch (command.name) {
        case "help": setHelp(true); break;
        case "clear": (onClear ?? view.clear)(); setPanel(null); break;
        case "history": restoreTranscriptView(sid); break;
        case "model":
          if (state?.goal?.status === "active") await remote("stop", "");
          onModel(); break;
        case "routines": setPanel("routines"); break;
        default: {
          const response = await remote(command.name, command.args);
          if (command.name === "find") { setHits((response.data.hits ?? []) as ChatSearchHit[]); setPanel("find"); }
          if (command.name === "status" || command.name === "goal" && !command.args) setPanel("status");
        }
      }
      setValue("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally { sending.current = false; setBusy(false); }
    return true;
  }

  function select(row: ChatCommand) {
    if (!row.available) return;
    setHelp(false); setValue(`/${row.name} `);
  }
  function onKeyDown(event: KeyboardEvent): boolean {
    if (!open || !matches.length) return false;
    if (event.key === "Escape") { event.preventDefault(); setHelp(false); setValue(""); return true; }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault(); setActiveIndex((n) => (n + (event.key === "ArrowDown" ? 1 : -1) + matches.length) % matches.length); return true;
    }
    if (event.key === "Tab" || event.key === "Enter" && value.trim() !== `/${matches[activeIndex]?.name}`) {
      event.preventDefault(); select(matches[activeIndex] ?? matches[0]); return true;
    }
    return false;
  }
  function reveal(hit: ChatSearchHit) {
    restoreTranscriptView(hit.session_id);
    setPanel(null);
    requestAnimationFrame(() => document.querySelector(`[data-chat-item="${CSS.escape(hit.item_id)}"], [data-message-id="${CSS.escape(hit.item_id)}"], [data-turn-id="${CSS.escape(hit.item_id)}"]`)?.scrollIntoView({ block: "center" }));
  }
  return { enabled, open, matches, activeIndex, setActiveIndex, select, execute, onKeyDown,
    isCommand: enabled && parseChatCommand(value) !== null, canSteer: state?.goal?.status === "active",
    state, error, busy, panel, setPanel, hits, reveal, result, agentId };
}

export function ChatCommandPanel({ control }: { control: ReturnType<typeof useChatCommands> }) {
  const t = useT();
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => { list.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView?.({ block: "nearest" }); }, [control.activeIndex, control.open]);
  if (!control.enabled) return null;
  const goal = control.state?.goal;
  return <div className="relative text-xs" data-testid="chat-commands">
    {control.state?.mode === "plan" ? <div className="mb-2 rounded-md border border-border bg-secondary px-3 py-2">{t("slash.plan_active")}</div> : null}
    {control.state?.mode === "build" ? <div className="mb-2 px-1 text-muted-foreground">{t("slash.build_active")}</div> : null}
    {goal && goal.status !== "cleared" ? <div className="mb-2 rounded-lg border border-border bg-card p-3" data-testid="chat-goal">
      <div className="flex items-center gap-2"><span className="font-medium">{t(`slash.goal_${goal.status}`)}</span>
        <span className="ml-auto text-muted-foreground">{goal.steps} {t("slash.steps")}</span>
        {goal.status === "active" ? <button aria-label={t("slash.stop")} onClick={() => void control.execute("/stop")}><Pause className="h-4 w-4" /></button> : null}
        {goal.status === "paused" || goal.status === "blocked" ? <button aria-label={t("slash.continue")} onClick={() => void control.execute("/continue")}><Play className="h-4 w-4" /></button> : null}
        <button aria-label={t("slash.clear_goal")} onClick={() => void control.execute("/goal clear")}><X className="h-4 w-4" /></button>
      </div><button className="mt-1 line-clamp-3 whitespace-pre-wrap text-left [overflow-wrap:anywhere]" title={goal.objective} onClick={() => control.setPanel("status")}>{goal.objective}</button>
      {goal.reason ? <p className="mt-1 line-clamp-3 text-muted-foreground [overflow-wrap:anywhere]">{goal.reason}</p> : null}
    </div> : null}
    {control.error ? <p role="alert" className="mb-2 text-destructive [overflow-wrap:anywhere]">{control.error}</p> : null}
    {control.busy ? <Loader2 aria-label={t("slash.working")} className="mb-2 h-4 w-4 animate-spin" /> : null}
    {control.panel ? <section role="dialog" aria-label={t(`slash.${control.panel}`)} className="mb-2 max-h-[45vh] overflow-auto rounded-lg border border-border bg-card p-3">
      <button aria-label={t("slash.close")} className="float-right" onClick={() => control.setPanel(null)}><X className="h-4 w-4" /></button>
      {control.panel === "routines" ? <AgentRoutinesList agentId={control.agentId} variant="rail" /> : null}
      {control.panel === "status" ? <div className="space-y-2"><p>{t("slash.mode")}: {control.state?.mode}</p>
        <p>{t("slash.task")}: {control.state?.last_request || t("slash.no_task")}</p>
        {goal ? <><p>{t("slash.goal")}: {goal.objective} — {t(`slash.goal_${goal.status}`)}</p><p>{goal.reason}</p></> : null}</div> : null}
      {control.panel === "find" ? control.hits.length ? control.hits.map((hit) => <button key={hit.seq} onClick={() => control.reveal(hit)} className="block w-full border-b border-border p-2 text-left hover:bg-secondary">
        <span className="text-muted-foreground">#{hit.seq}</span> <span className="whitespace-pre-wrap">{hit.text}</span></button>) : <p>{t("slash.no_matches")}</p> : null}
    </section> : null}
    {control.result?.data.result ? <p className="mb-2 text-muted-foreground" role="status">{t("slash.command_done")}: /{control.result.command}</p> : null}
    {control.open ? <div ref={list} role="listbox" aria-label={t("slash.help")} className="absolute bottom-full left-0 right-0 z-40 mb-2 max-h-80 overflow-auto rounded-xl border border-border bg-popover p-1 shadow-lg">
      {control.matches.map((row, index) => <button key={row.name} role="option" aria-selected={index === control.activeIndex} aria-disabled={!row.available}
        onMouseEnter={() => control.setActiveIndex(index)} onMouseDown={(e) => e.preventDefault()} onClick={() => control.select(row)}
        className={cn("block w-full rounded-lg px-3 py-2 text-left", index === control.activeIndex && "bg-secondary", !row.available && "opacity-50")}>
        <span className="font-medium">/{row.name}</span><span className="ml-3 text-muted-foreground">{t(`slash.commands.${row.name}`)}</span>
        <span className="mt-0.5 block text-[11px] text-muted-foreground">{row.available ? row.example : row.reason}</span>
      </button>)}
    </div> : null}
  </div>;
}
