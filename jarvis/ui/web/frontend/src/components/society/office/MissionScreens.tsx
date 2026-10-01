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
import { Folder } from "lucide-react";
import { WorkspaceTerminalHeader } from "@/components/agentic/WorkspaceTerminalHeader";
import type { PaneEdgeState } from "@/components/agentic/terminalThemes";
import { RosterRail } from "@/components/society/roster/RosterRail";
import { useSocietyRoster, type SocietyAgent } from "../data";
import { useSocietyChatGroups } from "@/lib/societyChatGroups";
import { paneLabel, paneOccupants, type PaneOccupant } from "./codingFloor";
import { PaneScreenView } from "./PaneLiveScreen";
import { screenChanged } from "./terminalScreen";
import type { MonitorSection } from "./officeStore";
import { useBlendingCanvasLayer } from "./blendingLayer";
import "./paneCommand.css";
import "./missionScreens.css";

const loadCosts = () => import("@/views/CostsView");
const loadChat = () => import("../chat/AgentChatPanel");
const CostsView = lazy(() => loadCosts().then((m) => ({ default: m.CostsView })));
const AgentChatPanel = lazy(() => loadChat().then((m) => ({ default: m.AgentChatPanel })));

/**
 * Fetch the sections' code before the camera gets close, so walking up to the
 * desk shows the screens at once instead of a blank glass while chunks load.
 */
export function preloadMissionScreens(): void {
  void loadCosts().catch((err) => console.warn("Mission Control: Spend preload failed", err));
  void loadChat().catch((err) => console.warn("Mission Control: chat preload failed", err));
}

/** The sections are laid out at this window size, then scaled onto the monitor. */
export const MISSION_SCREEN_PX = { w: 1240, h: 723 } as const;

const SCREEN_Z_RANGE: [number, number] = [10, 0];

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

/** Columns × rows for `n` tiles, the way the IDE splits a workspace: side by side up to three, then two rows. Pure. */
export function ideGrid(n: number): { cols: number; rows: number } {
  if (n <= 3) return { cols: Math.max(1, n), rows: 1 };
  if (n === 4) return { cols: 2, rows: 2 };
  return { cols: 3, rows: 2 };
}

/** The IDE sidebar's tree: each project folder with its workspaces, their pane counts and whether one works. Pure. */
export function ideTree(occupants: readonly PaneOccupant[]): { folder: string; name: string; workspaces: { id: string; name: string; count: number; working: boolean }[] }[] {
  const projects = new Map<string, { folder: string; name: string; workspaces: Map<string, { id: string; name: string; count: number; working: boolean }> }>();
  for (const { pane, agent } of occupants) {
    const folder = pane.folder || pane.workspace_name;
    const project = projects.get(folder) ?? { folder, name: folder.split(/[\\/]/).filter(Boolean).pop() ?? folder, workspaces: new Map() };
    const ws = project.workspaces.get(pane.workspace_id) ?? { id: pane.workspace_id, name: pane.workspace_name, count: 0, working: false };
    ws.count += 1;
    ws.working ||= agent.state === "working";
    project.workspaces.set(pane.workspace_id, ws);
    projects.set(folder, project);
  }
  return [...projects.values()].map((p) => ({ folder: p.folder, name: p.name, workspaces: [...p.workspaces.values()] }));
}

const EDGE: Record<string, PaneEdgeState> = { pending: "connecting", live: "live", exited: "exited", error: "error" };
const noop = () => undefined;

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
  const tree = useMemo(() => ideTree(occupants), [occupants]);
  const screens = usePaneScreenBatch(tiles.map((o) => ({ workspaceId: o.pane.workspace_id, key: o.pane.key })));
  const appTheme = useThemeValue();
  // The IDE's pane appearance: the reader's stored choice, else the app's theme.
  const appearance = storedTerminalAppearance() ?? appTheme;
  const brand = PANE_BRAND[appearance], chrome = PANE_CHROME[appearance], ansi = themeFor(appearance);
  const vars = {
    "--pane-ink": brand.ink, "--pane-ink-muted": brand.inkMuted, "--pane-ink-faint": brand.inkFaint,
    "--pane-chip": brand.chip, "--pane-rule": chrome.border,
    "--pane-ground": PANE_SOLID[appearance], "--pane-caret": ansi.cursor ?? brand.ink,
  } as CSSProperties;
  const { cols, rows } = ideGrid(tiles.length);
  return (
    <div className="office-ide-monitor jarvis-nav-surface" style={vars}>
      {/* The IDE's sidebar: every project folder with its workspaces, the front one highlighted. */}
      <aside className="office-ide-side">
        <div className="office-ide-side-title">{t("nav.agentic_ide")}</div>
        {tree.map((project) => (
          <div key={project.folder}>
            <div className="office-ide-project"><Folder aria-hidden />{project.name}</div>
            {project.workspaces.map((ws) => (
              <div key={ws.id} className="office-ide-ws" data-active={ws.id === workspace || undefined}>
                <i style={{ background: ws.working ? ansi.green : brand.inkFaint }} />
                <span>{ws.name}</span>
                <b>{ws.count}</b>
              </div>
            ))}
          </div>
        ))}
      </aside>
      {tiles.length === 0 ? (
        <p className="office-ide-empty">{t("society.office.mission_screen_none")}</p>
      ) : (
        <div className="office-ide-grid" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`, gridTemplateRows: `repeat(${rows}, minmax(0, 1fr))` }}>
          {tiles.map((o) => {
            const key = `${o.pane.workspace_id}:${o.pane.key}`;
            const edge = EDGE[o.pane.status] ?? "live";
            return (
              // The IDE's own pane: its header component, its edge colour, the live terminal under it.
              <article key={key} className="office-ide-tile" style={{ borderColor: chrome.edge[edge === "connecting" ? "live" : edge] }}>
                <WorkspaceTerminalHeader name={o.pane.name} workspaceId={o.pane.workspace_id} agent={o.pane.agent}
                  displayName={o.pane.display_name || o.pane.agent} status={edge} appearance={appearance}
                  onToggleMaximize={noop} onAdd={noop} onClose={noop} onFork={noop} />
                <PaneScreenView screen={screens ? (screens.get(key) ?? null) : undefined} label={paneLabel(o.pane)}
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
  // Another <Html> mounting later would drop the canvas under this screen (see blendingLayer).
  useBlendingCanvasLayer(SCREEN_Z_RANGE);
  return (
    <Html transform occlude="blending" position={position} distanceFactor={distanceFactor} zIndexRange={SCREEN_Z_RANGE}
      style={{ width: MISSION_SCREEN_PX.w, height: MISSION_SCREEN_PX.h, pointerEvents: "none" }}>
      <div className="office-mission-screen" data-office-ui>
        <QueryClientProvider client={client}>
          <Suspense fallback={null}>{SECTIONS[section]()}</Suspense>
        </QueryClientProvider>
      </div>
    </Html>
  );
}
