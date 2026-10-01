/**
 * The agent panel in the office: a walkie-talkie to that agent.
 *
 * Speak (hold T, or hold / tap the microphone) or type; the words go into the
 * agent's own chat and it answers over its head. The panel keeps the last few
 * exchanges readable, and the map actions (walk there, call over, show,
 * team up, open the full chat) sit in one icon row underneath.
 *
 * Voice uses the app's existing in-app dictation (`stt_dictate`): the final
 * transcript is delivered into the focused field (lib/dictationTarget.ts), so
 * the panel focuses its own box before listening and sends whatever lands
 * there once the microphone is closed. The global `dictating` flag is left
 * alone on purpose — composers elsewhere mirror every partial while it is
 * set, and the Agents workspace keeps one mounted behind the map.
 */
import { useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowUp, Footprints, Hand, LocateFixed, Maximize2, Mic, Square, UserPlus } from "lucide-react";
import { useT } from "@/i18n";
import { getWSClient } from "@/hooks/useWebSocket";
import { useEventStore } from "@/store/events";
import type { TimelineItem } from "@/components/agentchat/reduce";
import type { SocietyAgent } from "../data";
import { player, useOfficeStore } from "./officeStore";
import { agentPositions } from "./walkerRegistry";
import { ownsKeyboard } from "./OfficePlayer";
import { useBubbleLabels } from "./OfficeBubbles";
import { bubbleText, itemsFor, talkStoreFor, turnBubble, useOfficeTalk } from "./officeTalk";
import "./officeTalk.css";

/** How long called or gathered agents stay before they drift back to their day. */
export const CALL_MS = 25_000;
/** A press on the microphone longer than this is push-to-talk: releasing sends. */
const HOLD_MS = 350;
/** After the microphone closes, the transcript usually lands within a few seconds. */
const FINAL_WAIT_MS = 15_000;
/** Quiet time after the transcript lands before it is sent on its own. */
const SETTLE_MS = 350;
/** Exchanges the panel keeps on screen; the full chat has the rest. */
const RECENT_ITEMS = 6;

export interface TalkActions {
  onOpenAgent: (id: string) => void;
}

async function bindAgentChat(agentId: string): Promise<void> {
  const res = await fetch(`/api/society/agents/${encodeURIComponent(agentId)}/chat`, { method: "POST" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

function sendDictation(mode: "start" | "stop") {
  getWSClient()?.send({ type: "command", action: "stt_dictate", payload: { mode } });
}

function StateDot({ state }: { state: SocietyAgent["state"] }) {
  return <i className="office-dot" data-state={state} aria-hidden />;
}

function LogEntry({ item }: { item: TimelineItem }) {
  const labels = useBubbleLabels();
  if (item.type === "user") return <li className="office-talk-msg" data-from="you"><p>{item.text}</p></li>;
  if (item.type === "error") return <li className="office-talk-msg" data-from="error"><p>{bubbleText(item.text, 300)}</p></li>;
  if (item.type !== "turn") return null;
  if (item.status === "running") {
    const live = turnBubble(item, labels);
    return (
      <li className="office-talk-msg" data-from="agent" data-live>
        <p>{live.text}<span className="office-bubble-dots" aria-hidden><i /><i /><i /></span></p>
      </li>
    );
  }
  const text = [...item.blocks].reverse().find((b) => b.kind === "text" && b.text.trim());
  const body = text && text.kind === "text" ? bubbleText(text.text, 900) : turnBubble(item, labels).text;
  return <li className="office-talk-msg" data-from={item.status === "error" ? "error" : "agent"}><p>{body}</p></li>;
}

export function AgentTalkPanel({ agent, actions, onClose }: { agent: SocietyAgent; actions: TalkActions; onClose: () => void }) {
  const t = useT();
  const headingId = useId();
  const client = useQueryClient();
  const office = useOfficeStore();
  const useChat = talkStoreFor(agent);
  const timeline = useChat((s) => s.timeline);
  const activeSessionId = useChat((s) => s.activeSessionId);
  const busy = useChat((s) => s.busy);
  const lastError = useChat((s) => s.lastError);
  const connected = useEventStore((s) => s.connected);
  const listening = useOfficeTalk((s) => s.listening);
  const interim = useEventStore((s) => (listening ? s.dictationText : ""));
  const [value, setValue] = useState("");
  const [bindError, setBindError] = useState<string | null>(null);
  const [called, setCalled] = useState(false);
  const panel = useRef<HTMLElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const log = useRef<HTMLOListElement>(null);

  const isLead = agent.tier === "lead";
  const ready = isLead || (!!agent.chatSessionId && agent.chatSessionId === activeSessionId);
  const items = itemsFor(agent, activeSessionId, timeline);
  const recent = useMemo(() => items.filter((i) => i.type === "user" || i.type === "turn" || i.type === "error").slice(-RECENT_ITEMS), [items]);
  const last = recent[recent.length - 1];
  const running = last?.type === "user" || (last?.type === "turn" && last.status === "running");
  const approvals = ready ? timeline.pendingApprovals.length : 0;
  const inDraft = office.teamDraft.includes(agent.agentId);
  const where = agentPositions.get(agent.agentId);

  // This agent is the one the office listens to now; its bubble follows this chat.
  useEffect(() => { useOfficeTalk.getState().setAgent(agent.agentId); setCalled(false); setValue(""); }, [agent.agentId]);
  useEffect(() => { panel.current?.focus({ preventScroll: true }); }, [agent.agentId]);

  // Open the agent's own chat. Bind is idempotent; it creates the session a
  // brand-new agent does not have yet, and the roster then carries its id.
  useEffect(() => {
    const chat = useChat.getState();
    if (isLead) { if (!chat.activeSessionId) void chat.loadSessions(); return; }
    let alive = true;
    setBindError(null);
    if (agent.chatSessionId) chat.openSession(agent.chatSessionId);
    void bindAgentChat(agent.agentId)
      .then(() => { if (alive && !agent.chatSessionId) void client.invalidateQueries({ queryKey: ["society", "roster"] }); })
      .catch((err) => { if (alive) setBindError(err instanceof Error ? err.message : String(err)); });
    return () => { alive = false; };
  }, [agent.agentId, agent.chatSessionId, isLead, useChat, client]);

  // Newest exchange in view.
  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight }); }, [recent]);

  const submit = useCallback(async (text: string) => {
    const content = text.trim();
    if (!content || !ready) return;
    setValue("");
    useOfficeTalk.getState().say(bubbleText(content, 160));
    await useChat.getState().send(content);
  }, [ready, useChat]);

  // --- voice -------------------------------------------------------------
  const awaiting = useRef(false);
  const awaitTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const settleTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const startVoice = useCallback(() => {
    if (!connected || !ready || useOfficeTalk.getState().listening) return;
    clearTimeout(settleTimer.current);
    awaiting.current = false;
    useEventStore.getState().setDictationInterim("");
    // The final transcript goes to whatever field has focus: make that this box.
    input.current?.focus({ preventScroll: true });
    useOfficeTalk.getState().setListening(true);
    sendDictation("start");
  }, [connected, ready]);
  const stopVoice = useCallback(() => {
    if (!useOfficeTalk.getState().listening) return;
    sendDictation("stop");
    useOfficeTalk.getState().setListening(false);
    input.current?.focus({ preventScroll: true });
    awaiting.current = true;
    clearTimeout(awaitTimer.current);
    awaitTimer.current = setTimeout(() => { awaiting.current = false; }, FINAL_WAIT_MS);
  }, []);
  const voice = useRef({ startVoice, stopVoice });
  voice.current = { startVoice, stopVoice };

  // Closing the panel (or switching agents) never leaves the microphone open.
  useEffect(() => () => {
    clearTimeout(awaitTimer.current);
    clearTimeout(settleTimer.current);
    if (useOfficeTalk.getState().listening) { sendDictation("stop"); useOfficeTalk.getState().setListening(false); }
  }, [agent.agentId]);

  // Hold T to talk, anywhere on the map (a text field keeps its own T).
  useEffect(() => {
    let held = false;
    const down = (event: globalThis.KeyboardEvent) => {
      if (event.code !== "KeyT" || event.ctrlKey || event.metaKey || event.altKey) return;
      // Key repeats while held land in the focused box; they must not type there.
      if (held) { event.preventDefault(); return; }
      if (event.repeat || ownsKeyboard(event.target)) return;
      event.preventDefault();
      held = true;
      voice.current.startVoice();
    };
    const up = (event: globalThis.KeyboardEvent) => {
      if (event.code !== "KeyT" || !held) return;
      held = false;
      event.preventDefault();
      voice.current.stopVoice();
    };
    const release = () => { if (held) { held = false; voice.current.stopVoice(); } };
    window.addEventListener("keydown", down, true);
    window.addEventListener("keyup", up, true);
    window.addEventListener("blur", release);
    return () => {
      window.removeEventListener("keydown", down, true);
      window.removeEventListener("keyup", up, true);
      window.removeEventListener("blur", release);
    };
  }, []);

  // The microphone button: a long press is push-to-talk, a tap toggles.
  const press = useRef<{ at: number; wasListening: boolean } | null>(null);
  const onMicDown = (event: PointerEvent<HTMLButtonElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    press.current = { at: Date.now(), wasListening: useOfficeTalk.getState().listening };
    if (!press.current.wasListening) startVoice();
  };
  const onMicUp = () => {
    const p = press.current;
    press.current = null;
    if (!p) return;
    if (p.wasListening || Date.now() - p.at > HOLD_MS) stopVoice();
  };
  const onMicKey = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    if (useOfficeTalk.getState().listening) stopVoice(); else startVoice();
  };

  const onChange = (next: string) => {
    setValue(next);
    if (!awaiting.current) return;
    // The spoken words just landed: send them once the box stops changing.
    clearTimeout(settleTimer.current);
    settleTimer.current = setTimeout(() => {
      awaiting.current = false;
      clearTimeout(awaitTimer.current);
      void submit(input.current?.value ?? next);
      panel.current?.focus({ preventScroll: true });
    }, SETTLE_MS);
  };

  const status = !ready
    ? bindError ? `${t("society.card.chat_bind_failed")} (${bindError})` : t("society.office.talk_connecting")
    : listening ? (interim || t("society.office.talk_listening")) : t("society.office.talk_hint");

  return (
    <aside ref={panel} className="office-card office-panel office-talk" data-office-ui aria-labelledby={headingId} tabIndex={-1}>
      <header className="office-talk-head">
        <span className="office-talk-avatar" style={{ background: agent.palette.primary }} aria-hidden>
          {isLead ? "★" : agent.name.slice(0, 1).toUpperCase()}
        </span>
        <div className="office-talk-who">
          <h2 id={headingId}>{agent.name}</h2>
          <span><StateDot state={agent.state} />{t(`society.office.state_${agent.state}`)}{agent.title || agent.providerLabel ? ` · ${agent.title || agent.providerLabel}` : ""}</span>
        </div>
        <button type="button" className="office-icon-button" onClick={onClose} aria-label={t("society.office.close")}>×</button>
      </header>

      <ol ref={log} className="office-talk-log" aria-live="polite" aria-label={t("society.office.talk_log").replace("{0}", agent.name)}>
        {recent.length === 0
          ? <li className="office-talk-empty">{t("society.office.talk_empty").replace("{0}", agent.name)}</li>
          : recent.map((item) => <LogEntry key={item.id} item={item} />)}
      </ol>

      {approvals > 0 && (
        <p className="office-talk-alert" role="status">
          {t("society.office.talk_approval").replace("{0}", agent.name)}
          <button type="button" className="office-link" onClick={() => actions.onOpenAgent(agent.agentId)}>{t("society.office.talk_review")}</button>
        </p>
      )}
      {lastError && ready && <p role="alert" className="office-talk-error">{lastError}</p>}

      <div className="office-talk-composer" data-listening={listening || undefined}>
        <button type="button" className="office-talk-mic" data-jarvis-dictation-trigger aria-pressed={listening}
          disabled={!connected || !ready}
          aria-label={listening ? t("society.office.talk_mic_stop") : t("society.office.talk_mic_start")}
          title={listening ? t("society.office.talk_mic_stop") : t("society.office.talk_mic_start")}
          onPointerDown={onMicDown} onPointerUp={onMicUp} onPointerLeave={() => { if (press.current && Date.now() - press.current.at > HOLD_MS) onMicUp(); }}
          onKeyDown={onMicKey}>
          {listening ? <Square aria-hidden /> : <Mic aria-hidden />}
        </button>
        <textarea ref={input} rows={1} value={value} maxLength={4000}
          placeholder={t("society.office.talk_placeholder").replace("{0}", agent.name)}
          aria-label={t("society.office.talk_placeholder").replace("{0}", agent.name)}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void submit(value); }
            // Escape leaves the box so the arrow keys walk again; a second Escape closes the panel.
            else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); panel.current?.focus({ preventScroll: true }); }
          }} />
        {running && !value.trim() && !listening
          ? <button type="button" className="office-talk-send" data-stop onClick={() => void useChat.getState().cancel()}
              aria-label={t("society.office.talk_cancel")} title={t("society.office.talk_cancel")}><Square aria-hidden /></button>
          : <button type="button" className="office-talk-send" disabled={!ready || busy || !value.trim()} onClick={() => void submit(value)}
              aria-label={t("society.office.talk_send")} title={t("society.office.talk_send")}><ArrowUp aria-hidden /></button>}
      </div>
      <p className="office-talk-status" data-listening={listening || undefined} aria-live="polite">{status}</p>

      <div className="office-talk-tools" role="toolbar" aria-label={t("society.office.talk_tools")}>
        <button type="button" disabled={!where} onClick={() => where && office.requestWalk(where)} title={t("society.office.action_walk")}>
          <Footprints aria-hidden /><span>{t("society.office.tool_walk")}</span>
        </button>
        <button type="button" onClick={() => { office.summon([agent.agentId], { x: player.x, z: player.z }, CALL_MS); setCalled(true); }} title={t("society.office.action_call")}>
          <Hand aria-hidden /><span>{t("society.office.tool_call")}</span>
        </button>
        <button type="button" disabled={!where} onClick={() => where && office.focusOn(where)} title={t("society.office.action_focus")}>
          <LocateFixed aria-hidden /><span>{t("society.office.tool_focus")}</span>
        </button>
        <button type="button" aria-pressed={inDraft} onClick={() => office.toggleDraft(agent.agentId)}
          title={t(inDraft ? "society.office.action_undraft" : "society.office.action_draft")}>
          <UserPlus aria-hidden /><span>{t("society.office.tool_team")}</span>
        </button>
        <button type="button" onClick={() => actions.onOpenAgent(agent.agentId)} title={t("society.office.action_chat")}>
          <Maximize2 aria-hidden /><span>{t("society.office.tool_chat")}</span>
        </button>
      </div>
      {called && <p className="office-hint office-talk-note" role="status">{t("society.office.call_sent").replace("{0}", agent.name)}</p>}
      {office.teamDraft.length > 0 && (
        <button type="button" className="office-link office-talk-note" onClick={() => office.select({ kind: "checkpoint", id: "team" })}>
          {t("society.office.draft_count").replace("{0}", String(office.teamDraft.length))}
        </button>
      )}
    </aside>
  );
}
