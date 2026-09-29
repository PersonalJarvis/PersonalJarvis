/**
 * A coding session on the coding floor, shown as the pane it is: the Agentic
 * IDE's title bar (state dot, agent mark, the pane's title, the pane actions as
 * quiet icons) over the IDE's own live terminal. Nothing else — no chips, no
 * toolbar, no side notes, no second prompt box.
 *
 * The terminal is the IDE pane's `AgenticTerminal` itself, attached as one more
 * viewer of the same PTY: the same renderer, font and colours, and typed into
 * directly, exactly like the pane in the IDE. Colours come from the pane's own
 * appearance tables (./terminalThemes), so the panel matches the IDE's panes in
 * light and dark, including a light pane in a dark app.
 */
import { useEffect, useId, useRef, useState, type CSSProperties } from "react";
import { Footprints, Hand, LocateFixed, Maximize2, Square, Trash2, X } from "lucide-react";
import { useT } from "@/i18n";
import { useThemeValue } from "@/hooks/useTheme";
import { closeTerminal, forkTerminal, interruptTerminal } from "@/lib/agenticIdeApi";
import { usePaneTitle } from "@/store/paneRecaps";
import { useWorkspacePanesStore } from "@/store/workspacePanes";
import { AgentMark } from "@/components/agentic/AgentMark";
import { AgenticTerminal } from "@/components/agentic/AgenticTerminal";
import { FONT_DEFAULT } from "@/components/agentic/paneFont";
import { BranchIcon } from "@/components/agentic/branchIcon";
import { PANE_BRAND, PANE_CHROME, PANE_SOLID, storedTerminalAppearance, themeFor } from "@/components/agentic/terminalThemes";
import type { PaneOccupant } from "./codingFloor";
import { player, useOfficeStore } from "./officeStore";
import { agentPositions } from "./walkerRegistry";
import { CALL_MS } from "./AgentTalkPanel";
import "./paneCommand.css";

/** A second press within this window confirms closing the session. */
const CONFIRM_MS = 4000;

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
  const [note, setNote] = useState<Note>(null);
  const [confirmClose, setConfirmClose] = useState(false);
  const appTheme = useThemeValue();
  // The IDE's pane appearance: the reader's stored choice, else the app's theme.
  const appearance = storedTerminalAppearance() ?? appTheme;
  const brand = PANE_BRAND[appearance];
  const chrome = PANE_CHROME[appearance];
  const ansi = themeFor(appearance);
  const paneTitle = usePaneTitle(pane.workspace_id, pane.name);
  const where = agentPositions.get(agent.agentId);
  const live = pane.status === "live";
  const working = occupant.dot === "working";
  const since = sinceLabel(pane.activity_since || null, t);
  const title = paneTitle || pane.recap.trim() || agent.name;

  useEffect(() => { panel.current?.focus({ preventScroll: true }); setNote(null); setConfirmClose(false); }, [agent.agentId]);
  useEffect(() => {
    if (!confirmClose) return;
    const timer = setTimeout(() => setConfirmClose(false), CONFIRM_MS);
    return () => clearTimeout(timer);
  }, [confirmClose]);

  const refresh = () => { void useWorkspacePanesStore.getState().load(); };

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

  const dot = occupant.dot === "working" ? ansi.green
    : occupant.dot === "waiting" ? ansi.yellow
      : occupant.dot === "error" ? ansi.red : brand.inkFaint;
  const edge = occupant.dot === "error" ? chrome.edge.error : live ? chrome.edge.live : chrome.edge.exited;
  const vars = {
    "--pane-ink": brand.ink,
    "--pane-ink-muted": brand.inkMuted,
    "--pane-ink-faint": brand.inkFaint,
    "--pane-chip": brand.chip,
    "--pane-rule": chrome.border,
    "--pane-edge": edge,
    "--pane-ground": PANE_SOLID[appearance],
    "--pane-float": chrome.float,
    "--pane-ok": ansi.green,
    "--pane-fault": ansi.red,
    "--pane-caret": ansi.cursor ?? brand.ink,
  } as CSSProperties;

  return (
    <aside ref={panel} className="office-panel office-pane" data-office-ui style={vars} aria-labelledby={headingId} tabIndex={-1}>
      <header className="office-pane-head">
        <span className="office-pane-dot" style={{ background: dot }} role="img"
          aria-label={t(`society.office.pane_state_${occupant.stateKey}`)} />
        <AgentMark agent={pane.agent} label={pane.display_name || pane.agent} variant="plain" size="sm"
          className="!text-[color:var(--pane-ink)] [&>.bg-foreground]:!bg-[color:var(--pane-ink)]" />
        <h2 id={headingId} title={`${title} (${pane.name})`}>{title}</h2>
        <span className="office-pane-meta">
          {t(`society.office.pane_state_${occupant.stateKey}`)}{since ? ` · ${since}` : ""}
        </span>
        <div className="office-pane-actions" role="toolbar" aria-label={t("society.office.talk_tools")}>
          {working && live && (
            <button type="button" onClick={stop} title={t("society.office.cmd_stop")} aria-label={t("society.office.cmd_stop")}>
              <Square aria-hidden />
            </button>
          )}
          <button type="button" disabled={!live} onClick={fork} title={t("society.office.cmd_fork")} aria-label={t("society.office.cmd_fork")}>
            <BranchIcon aria-hidden />
          </button>
          <button type="button" disabled={!where} onClick={() => where && office.requestWalk(where)}
            title={t("society.office.action_walk")} aria-label={t("society.office.action_walk")}>
            <Footprints aria-hidden />
          </button>
          <button type="button" onClick={() => office.summon([agent.agentId], { x: player.x, z: player.z }, CALL_MS)}
            title={t("society.office.action_call")} aria-label={t("society.office.action_call")}>
            <Hand aria-hidden />
          </button>
          <button type="button" disabled={!where} onClick={() => where && office.focusOn(where)}
            title={t("society.office.action_focus")} aria-label={t("society.office.action_focus")}>
            <LocateFixed aria-hidden />
          </button>
          <button type="button" onClick={onOpen} title={t("society.office.pane_open")} aria-label={t("society.office.pane_open")}>
            <Maximize2 aria-hidden />
          </button>
          <button type="button" data-danger aria-pressed={confirmClose} onClick={close}
            title={t(confirmClose ? "society.office.cmd_close_confirm" : "society.office.cmd_close")}
            aria-label={t(confirmClose ? "society.office.cmd_close_confirm" : "society.office.cmd_close")}>
            <Trash2 aria-hidden />
          </button>
          <span className="office-pane-sep" aria-hidden />
          <button type="button" onClick={onClose} title={t("society.office.close")} aria-label={t("society.office.close")}>
            <X aria-hidden />
          </button>
        </div>
      </header>

      {/* Keyed by the pane, so another session is a fresh socket, never this one's screen under a new name. */}
      <div className="office-pane-term">
        <AgenticTerminal key={`${pane.workspace_id}/${pane.name}`} headerMode="none"
          name={pane.name} workspaceId={pane.workspace_id} agent={pane.agent}
          displayName={pane.display_name || pane.agent || agent.name}
          appearance={appearance} fontSize={FONT_DEFAULT}
          onAttachError={(message) => setNote({ tone: "error", text: message })} />
      </div>
      {note && <p className="office-pane-status" role="status" data-tone={note.tone}>{note.text}</p>}
    </aside>
  );
}
