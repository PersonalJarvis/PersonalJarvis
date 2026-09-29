/**
 * Mission Control's desk monitors run three REAL app sections, live, as they
 * would run in the app: Spend (left), the Agents view (centre) and the Agentic
 * IDE (right). Each is the section's own components laid out at a normal
 * window size and drawn onto the screen plane with drei's <Html>.
 *
 * Display only: clicks fall through to the monitor underneath, which dives
 * the camera into the glass and then opens that section.
 *
 * Two sections are reduced to what can run twice without side effects:
 *  - Agents: the real roster rail and one agent's real chat on its OWN chat
 *    store (never the lead's: its chat is the app's own voice chat, and a
 *    second panel on it would disconnect the app when the monitor unmounts).
 *  - Agentic IDE: the real terminals, read from the same read-only screen feed
 *    the desk monitors use. Mounting the real IDE would attach a second xterm
 *    to every PTY and resize the user's live terminals to monitor size.
 */
import { Suspense, lazy, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { Html } from "@react-three/drei";
import { QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { useT } from "@/i18n";
import { useThemeValue } from "@/hooks/useTheme";
import { createAgentChatStore } from "@/store/agentChat";
import { useWorkspacePanesStore } from "@/store/workspacePanes";
import { fetchPaneScreens, MAX_PANE_SCREENS, type PaneScreen } from "@/lib/paneScreensApi";
import { PANE_BRAND, PANE_CHROME, PANE_SOLID, storedTerminalAppearance, themeFor } from "@/components/agentic/terminalThemes";
import { AgentMark } from "@/components/agentic/AgentMark";
import { RosterRail } from "@/components/society/roster/RosterRail";
import { useSocietyRoster, type SocietyAgent } from "../data";
import { useSocietyChatGroups } from "@/lib/societyChatGroups";
import { paneLabel, paneOccupants, type PaneOccupant } from "./codingFloor";
import { PaneScreenView } from "./PaneLiveScreen";
import { screenChanged } from "./terminalScreen";
import type { MonitorSection } from "./officeStore";
import "./paneCommand.css";
import "./missionScreens.css";

const CostsView = lazy(() => import("@/views/CostsView").then((m) => ({ default: m.CostsView })));
const AgentChatPanel = lazy(() => import("../chat/AgentChatPanel").then((m) => ({ default: m.AgentChatPanel })));

/** The sections are laid out at this window size, then scaled onto the monitor. */
export const MISSION_SCREEN_PX = { w: 1240, h: 723 } as const;

/** One terminal poll for every tile at once, jittered (AP-33). */
const POLL_MIN_MS = 900;
const POLL_JITTER_MS = 300;
/** The IDE monitor shows at most this many panes of the front workspace. */
const MAX_TILES = 6;

// ---------------------------------------------------------------------------
// Agents
// ---------------------------------------------------------------------------

/** Whose chat the Agents monitor shows: a working agent first, never the lead. Pure. */
export function pickShownAgent(agents: readonly SocietyAgent[]): SocietyAgent | null {
  const chatty = agents.filter((a) => a.tier !== "lead" && a.chatSessionId);
  return chatty.find((a) => a.state === "working") ?? chatty.find((a) => a.state === "waiting") ?? chatty[0] ?? null;
}

function AgentsScreen() {
  const t = useT();
  const roster = useSocietyRoster();
  const agents = useMemo(() => roster.data?.agents ?? [], [roster.data]);
  const sample = roster.data?.sample ?? true;
  const groups = useSocietyChatGroups(!sample).data ?? [];
  const shown = pickShownAgent(agents);
  const shownId = shown?.agentId ?? null;
  const store = useMemo(() => (shownId ? createAgentChatStore("society", `office-mission:${shownId}`) : null), [shownId]);
  // Leaving the monitor closes its socket; the chat itself keeps running on the server.
  useEffect(() => () => store?.getState().disconnect(), [store]);
  return (
    <div className="grid h-full min-h-0 grid-cols-[300px_minmax(0,1fr)] bg-card">
      <RosterRail agents={agents} groups={groups} loading={roster.isLoading} sample={sample}
        activeAgentId={shownId} onOpen={() => undefined} onCreate={() => undefined}
        side="left" className="w-full border-0 jarvis-nav-surface" />
      <section className="flex min-h-0 flex-col overflow-hidden rounded-tl-[12px] border-l border-border bg-background">
        {shown && store ? (
          <Suspense fallback={null}>
            <AgentChatPanel key={shown.agentId} agent={shown} roster={agents} chatStore={store} />
          </Suspense>
        ) : (
          <p className="m-auto text-sm text-muted-foreground">{t("society.office.mission_screen_none")}</p>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Agentic IDE
// ---------------------------------------------------------------------------

/** The panes the IDE monitor shows: the front workspace's coding panes, in grid order. Pure. */
export function idePanes(occupants: readonly PaneOccupant[]): { workspace: string; tiles: PaneOccupant[] } {
  const front = occupants.find((o) => o.pane.workspace_active) ?? occupants[0];
  if (!front) return { workspace: "", tiles: [] };
  const tiles = occupants.filter((o) => o.pane.workspace_id === front.pane.workspace_id).slice(0, MAX_TILES);
  return { workspace: front.pane.workspace_id, tiles };
}

/** Columns × rows for `n` tiles, the way the IDE grid splits a workspace. Pure. */
export function ideGrid(n: number): { cols: number; rows: number } {
  if (n <= 1) return { cols: 1, rows: 1 };
  if (n === 2) return { cols: 2, rows: 1 };
  if (n <= 4) return { cols: 2, rows: 2 };
  return { cols: 3, rows: 2 };
}

function usePaneScreenBatch(panes: { workspaceId: string; key: string }[]): Map<string, PaneScreen> | null {
  const [screens, setScreens] = useState<Map<string, PaneScreen> | null>(null);
  const signature = panes.map((p) => `${p.workspaceId}:${p.key}`).join("|");
  useEffect(() => {
    const wanted = panes.slice(0, MAX_PANE_SCREENS);
    let alive = true;
    let failing = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const next = await fetchPaneScreens(wanted);
        if (!alive) return;
        failing = false;
        setScreens((prev) => {
          const map = new Map(next.map((s) => [`${s.workspace_id}:${s.key}`, s]));
          if (prev && map.size === prev.size && [...map].every(([k, s]) => !screenChanged(prev.get(k), s))) return prev;
          return map;
        });
      } catch (err) {
        // The last good screens stay up; only the first failure of a streak is worth a line.
        if (!failing) console.warn("Mission Control: IDE screens unavailable", err);
        failing = true;
      }
      if (alive) timer = setTimeout(() => void tick(), POLL_MIN_MS + Math.random() * POLL_JITTER_MS);
    };
    timer = setTimeout(() => void tick(), Math.random() * POLL_JITTER_MS);
    return () => { alive = false; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);
  return screens;
}

function IdeScreen() {
  const t = useT();
  const panes = useWorkspacePanesStore((s) => s.panes);
  const occupants = useMemo(() => paneOccupants(panes), [panes]);
  const { workspace, tiles } = idePanes(occupants);
  const workspaces = useMemo(() => {
    const seen = new Map<string, string>();
    for (const o of occupants) if (!seen.has(o.pane.workspace_id)) seen.set(o.pane.workspace_id, o.pane.workspace_name);
    return [...seen];
  }, [occupants]);
  const screens = usePaneScreenBatch(tiles.map((o) => ({ workspaceId: o.pane.workspace_id, key: o.pane.key })));
  const appTheme = useThemeValue();
  // The IDE's pane appearance: the reader's stored choice, else the app's theme.
  const appearance = storedTerminalAppearance() ?? appTheme;
  const brand = PANE_BRAND[appearance], chrome = PANE_CHROME[appearance], ansi = themeFor(appearance);
  const vars = {
    "--pane-ink": brand.ink, "--pane-ink-muted": brand.inkMuted, "--pane-ink-faint": brand.inkFaint,
    "--pane-chip": brand.chip, "--pane-rule": chrome.border, "--pane-edge": chrome.edge.live,
    "--pane-ground": PANE_SOLID[appearance], "--pane-caret": ansi.cursor ?? brand.ink,
  } as CSSProperties;
  const { cols, rows } = ideGrid(tiles.length);
  return (
    <div className="office-ide-monitor" style={vars}>
      <header className="office-ide-tabs">
        {workspaces.map(([id, name]) => <span key={id} data-active={id === workspace || undefined}>{name}</span>)}
      </header>
      {tiles.length === 0 ? (
        <p className="office-ide-empty">{t("society.office.mission_screen_none")}</p>
      ) : (
        <div className="office-ide-grid" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`, gridTemplateRows: `repeat(${rows}, minmax(0, 1fr))` }}>
          {tiles.map((o) => {
            const dot = o.dot === "working" ? ansi.green : o.dot === "waiting" ? ansi.yellow : o.dot === "error" ? ansi.red : brand.inkFaint;
            const key = `${o.pane.workspace_id}:${o.pane.key}`;
            const title = o.pane.recap.trim() || o.pane.last_prompt.trim() || paneLabel(o.pane);
            return (
              <article key={key} className="office-ide-tile">
                <header className="office-pane-head">
                  <span className="office-pane-dot" style={{ background: dot }} />
                  <AgentMark agent={o.pane.agent} label={o.pane.display_name || o.pane.agent} variant="plain" size="sm"
                    className="!text-[color:var(--pane-ink)] [&>.bg-foreground]:!bg-[color:var(--pane-ink)]" />
                  <h2>{title}</h2>
                  <span className="office-pane-meta">{t(`society.office.pane_state_${o.stateKey}`)}</span>
                </header>
                <PaneScreenView screen={screens ? (screens.get(key) ?? null) : undefined} label={title}
                  loadingText={t("society.office.cmd_screen_loading")} emptyText={t("society.office.cmd_screen_empty")}
                  className="office-pane-screen office-ide-screen" />
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// The screen
// ---------------------------------------------------------------------------

const SECTIONS: Record<MonitorSection, () => ReactNode> = {
  costs: () => <CostsView />,
  agents: () => <AgentsScreen />,
  "agentic-ide": () => <IdeScreen />,
};

/** `section`, live, on a monitor `widthM` metres wide whose glass sits at `position`. */
export function MissionLiveScreen({ section, widthM, position }: {
  section: MonitorSection; widthM: number; position: [number, number, number];
}) {
  // drei renders <Html> content in a separate React root: the app's data client
  // must be handed over, or the sections' queries throw and the screen stays blank.
  const client = useQueryClient();
  // drei maps one CSS pixel to distanceFactor / 400 metres in transform mode.
  const distanceFactor = (widthM * 400) / MISSION_SCREEN_PX.w;
  return (
    <Html transform occlude="blending" position={position} distanceFactor={distanceFactor} zIndexRange={[10, 0]}
      style={{ width: MISSION_SCREEN_PX.w, height: MISSION_SCREEN_PX.h, pointerEvents: "none" }}>
      <div className="office-mission-screen" data-office-ui>
        <QueryClientProvider client={client}>
          <Suspense fallback={null}>{SECTIONS[section]()}</Suspense>
        </QueryClientProvider>
      </div>
    </Html>
  );
}
