/**
 * A coding session on the coding floor, as a command panel: a small live
 * window onto its terminal, a box to prompt the agent directly, one-tap orders, the recent
 * prompts, and the session actions (open, stop, fork, close) next to the map
 * actions (walk there, call over, show).
 *
 * Everything goes through the Agentic IDE's existing routes — the same
 * `/terminals/{name}/prompt` the spoken path uses — so a prompt sent here is
 * recorded, receipted and visible in the pane exactly like one typed there.
 */
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ArrowUp, Footprints, GitFork, Hand, LocateFixed, Maximize2, Square, Trash2 } from "lucide-react";
import { useT } from "@/i18n";
import {
  closeTerminal, fetchPromptHistory, forkTerminal, interruptTerminal, promptTerminal, type PromptHistoryItem,
} from "@/lib/agenticIdeApi";
import { useWorkspacePanesStore } from "@/store/workspacePanes";
import type { PaneOccupant } from "./codingFloor";
import { player, useOfficeStore } from "./officeStore";
import { agentPositions } from "./walkerRegistry";
import { CALL_MS } from "./AgentTalkPanel";
import { PaneLiveScreen } from "./PaneLiveScreen";
import "./officeTalk.css";
import "./missionControl.css";

/** Recent prompts offered for re-use. */
const HISTORY_ITEMS = 4;
/** A second press within this window confirms closing the session. */
const CONFIRM_MS = 4000;

/** One-tap orders; the label is the chip, the text is what the agent receives. */
export const QUICK_ORDERS = ["continue", "status", "tests", "commit"] as const;

function sinceLabel(at: number | null, t: (key: string) => string): string {
  if (!at) return "";
  const seconds = Math.max(0, Date.now() / 1000 - at);
  if (seconds < 60) return t("society.office.since_now");
  if (seconds < 3600) return t("society.office.since_minutes").replace("{0}", String(Math.floor(seconds / 60)));
  if (seconds < 86400) return t("society.office.since_hours").replace("{0}", String(Math.floor(seconds / 3600)));
  return t("society.office.since_days").replace("{0}", String(Math.floor(seconds / 86400)));
}

type Note = { tone: "ok" | "error"; text: string } | null;

export function PaneCommandPanel({ occupant, onOpen, onClose }: { occupant: PaneOccupant; onOpen: () => void; onClose: () => void }) {
  const t = useT();
  const headingId = useId();
  const office = useOfficeStore();
  const { agent, pane } = occupant;
  const panel = useRef<HTMLElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const [value, setValue] = useState("");
  const [sharpen, setSharpen] = useState(false);
  const [sending, setSending] = useState(false);
  const [note, setNote] = useState<Note>(null);
  const [confirmClose, setConfirmClose] = useState(false);
  const [history, setHistory] = useState<PromptHistoryItem[]>([]);
  const where = agentPositions.get(agent.agentId);
  const live = pane.status === "live";
  const canPrompt = live && pane.accepts_prompts;
  const working = occupant.dot === "working";
  const since = sinceLabel(pane.activity_since || null, t);
  const recap = pane.recap.trim();

  useEffect(() => { panel.current?.focus({ preventScroll: true }); setValue(""); setNote(null); setConfirmClose(false); }, [agent.agentId]);
  useEffect(() => {
    if (!confirmClose) return;
    const timer = setTimeout(() => setConfirmClose(false), CONFIRM_MS);
    return () => clearTimeout(timer);
  }, [confirmClose]);

  const loadHistory = useCallback(() => {
    fetchPromptHistory(pane.name, pane.workspace_id)
      .then((res) => setHistory(res.items.slice(0, HISTORY_ITEMS)))
      .catch((err) => console.warn("Prompt history unavailable", err));
  }, [pane.name, pane.workspace_id]);
  useEffect(() => { loadHistory(); }, [loadHistory]);

  const refresh = () => { void useWorkspacePanesStore.getState().load(); };

  const send = useCallback(async (text: string) => {
    const content = text.trim();
    if (!content || !canPrompt || sending) return;
    setSending(true);
    setNote(null);
    try {
      const result = await promptTerminal(pane.name, content, { compose: sharpen, workspaceId: pane.workspace_id });
      setValue("");
      setNote(result.submitted === true
        ? { tone: "ok", text: t("society.office.cmd_sent").replace("{0}", agent.name) }
        : { tone: "error", text: result.detail || t("society.office.cmd_unconfirmed") });
      loadHistory();
      refresh();
    } catch (err) {
      setNote({ tone: "error", text: err instanceof Error ? err.message : String(err) });
    } finally {
      setSending(false);
    }
  }, [agent.name, canPrompt, loadHistory, pane.name, pane.workspace_id, sending, sharpen, t]);

  /** Run one session action; it answers with the line the panel shows once it is done. */
  const run = async (action: () => Promise<string>) => {
    setNote(null);
    try {
      setNote({ tone: "ok", text: await action() });
      refresh();
    } catch (err) {
      setNote({ tone: "error", text: err instanceof Error ? err.message : String(err) });
    }
  };

  const stop = () => void run(async () => {
    await interruptTerminal(pane.name, pane.workspace_id);
    return t("society.office.cmd_stopped").replace("{0}", agent.name);
  });
  const fork = () => void run(async () => {
    const { terminal } = await forkTerminal(pane.name, { workspaceId: pane.workspace_id, worktree: false });
    return t("society.office.cmd_forked").replace("{0}", terminal.name);
  });
  const close = () => {
    if (!confirmClose) { setConfirmClose(true); return; }
    setConfirmClose(false);
    void run(async () => {
      await closeTerminal(pane.name, pane.workspace_id);
      return t("society.office.cmd_closed").replace("{0}", agent.name);
    });
  };

  const status = !pane.accepts_prompts
    ? t("society.office.cmd_no_prompts")
    : !live ? t("society.office.cmd_not_running") : t("society.office.cmd_hint");

  return (
    <aside ref={panel} className="office-card office-panel office-talk office-cmd" data-office-ui aria-labelledby={headingId} tabIndex={-1}>
      <header className="office-talk-head">
        <span className="office-talk-avatar" style={{ background: agent.palette.primary }} aria-hidden>
          {agent.name.slice(0, 1).toUpperCase()}
        </span>
        <div className="office-talk-who">
          <h2 id={headingId}>{agent.name}</h2>
          <span>
            <i className="office-dot" data-state={agent.state} data-dot={occupant.dot} aria-hidden />
            {t(`society.office.pane_state_${occupant.stateKey}`)}{since ? ` · ${since}` : ""} · {pane.display_name || pane.agent} · {agent.providerLabel}
          </span>
        </div>
        <button type="button" className="office-icon-button" onClick={onClose} aria-label={t("society.office.close")}>×</button>
      </header>

      <div className="office-cmd-body">
        <PaneLiveScreen workspaceId={pane.workspace_id} paneKey={pane.key} title={pane.display_name || pane.name}
          dot={occupant.dot} stateLabel={t(`society.office.pane_state_${occupant.stateKey}`)} agentName={agent.name} onOpen={onOpen} />

        <div className="office-cmd-side">
          {recap && <p className="office-cmd-topic" title={recap}><strong>{t("society.office.pane_recap")}</strong> {recap}</p>}

          <div className="office-cmd-quick" role="group" aria-label={t("society.office.cmd_quick")}>
            {QUICK_ORDERS.map((order) => (
              <button key={order} type="button" disabled={!canPrompt || sending} title={t(`society.office.cmd_order_${order}_text`)}
                onClick={() => void send(t(`society.office.cmd_order_${order}_text`))}>
                {t(`society.office.cmd_order_${order}`)}
              </button>
            ))}
          </div>

          <div className="office-talk-composer">
            <textarea ref={input} rows={1} value={value} maxLength={8000} disabled={!canPrompt}
              placeholder={t("society.office.cmd_placeholder").replace("{0}", agent.name)}
              aria-label={t("society.office.cmd_placeholder").replace("{0}", agent.name)}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(value); }
                // Escape leaves the box so the arrow keys walk again; a second Escape closes the panel.
                else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); panel.current?.focus({ preventScroll: true }); }
              }} />
            {working && !value.trim()
              ? <button type="button" className="office-talk-send" data-stop onClick={stop}
                  aria-label={t("society.office.cmd_stop")} title={t("society.office.cmd_stop")}><Square aria-hidden /></button>
              : <button type="button" className="office-talk-send" disabled={!canPrompt || sending || !value.trim()} onClick={() => void send(value)}
                  aria-label={t("society.office.cmd_send")} title={t("society.office.cmd_send")}><ArrowUp aria-hidden /></button>}
          </div>
          <div className="office-cmd-row">
            <label className="office-cmd-check">
              <input type="checkbox" checked={sharpen} onChange={(e) => setSharpen(e.target.checked)} />
              <span>{t("society.office.cmd_sharpen")}</span>
            </label>
            <span className="office-talk-status" role="status" data-tone={note?.tone}>{sending ? t("society.office.cmd_sending") : note?.text ?? status}</span>
          </div>

          {history.length > 0 && (
            <details className="office-cmd-history">
              <summary>{t("society.office.cmd_history").replace("{0}", String(history.length))}</summary>
              <ul>
                {history.map((item) => (
                  <li key={item.id}>
                    <button type="button" title={t("society.office.cmd_reuse")} onClick={() => { setValue(item.text); input.current?.focus(); }}>
                      {item.text}
                    </button>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      </div>

      <div className="office-talk-tools office-cmd-tools" role="toolbar" aria-label={t("society.office.talk_tools")}>
        <button type="button" onClick={onOpen} title={t("society.office.pane_open")}>
          <Maximize2 aria-hidden /><span>{t("society.office.tool_open")}</span>
        </button>
        <button type="button" disabled={!live || !working} onClick={stop} title={t("society.office.cmd_stop")}>
          <Square aria-hidden /><span>{t("society.office.tool_stop")}</span>
        </button>
        <button type="button" disabled={!live} onClick={fork} title={t("society.office.cmd_fork")}>
          <GitFork aria-hidden /><span>{t("society.office.tool_fork")}</span>
        </button>
        <button type="button" disabled={!where} onClick={() => where && office.requestWalk(where)} title={t("society.office.action_walk")}>
          <Footprints aria-hidden /><span>{t("society.office.tool_walk")}</span>
        </button>
        <button type="button" onClick={() => office.summon([agent.agentId], { x: player.x, z: player.z }, CALL_MS)} title={t("society.office.action_call")}>
          <Hand aria-hidden /><span>{t("society.office.tool_call")}</span>
        </button>
        <button type="button" disabled={!where} onClick={() => where && office.focusOn(where)} title={t("society.office.action_focus")}>
          <LocateFixed aria-hidden /><span>{t("society.office.tool_focus")}</span>
        </button>
        <button type="button" data-danger aria-pressed={confirmClose} onClick={close}
          title={t(confirmClose ? "society.office.cmd_close_confirm" : "society.office.cmd_close")}>
          <Trash2 aria-hidden /><span>{t(confirmClose ? "society.office.tool_close_confirm" : "society.office.tool_close")}</span>
        </button>
      </div>
    </aside>
  );
}
