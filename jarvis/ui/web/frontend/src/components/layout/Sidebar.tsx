import {
  Loader2,
  ChevronDown,
  ChevronRight,
  Folder,
  MoreHorizontal,
  Store,
  UserCircle2,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
} from "lucide-react";
import {
  NAV_FOOTER_ITEMS,
  NAV_GROUPS,
  presentNavItem,
  resolveNavLabel,
  type NavItem,
} from "@/components/layout/navGroups";
import { useEventStore } from "@/store/events";
import { useVoiceReadiness } from "@/hooks/useVoiceReadiness";
import { useVoiceMode } from "@/hooks/useVoiceMode";
import { useSectionHealth } from "@/hooks/useProviders";
import { usePluginAttention } from "@/hooks/usePluginAttention";
import { useVoiceEngineDisplay } from "@/hooks/useVoiceEngineDisplay";
import { clsx } from "clsx";
import { cn } from "@/lib/utils";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useT } from "@/i18n";
import { BrowserRealtimeControl } from "@/components/voice/BrowserRealtimeControl";
import { SurfaceSwitch } from "@/components/home/SurfaceSwitch";
import { RecentChats } from "@/components/home/RecentChats";
import { useConversations } from "@/hooks/useConversations";
import { useHomeStore } from "@/store/home";
import { useAgentChatStore } from "@/store/agentChat";
import { useIdeChatStore } from "@/store/ideChat";
import { WorkspaceChats } from "@/components/agentic/WorkspaceChats";
import { useAppInstance } from "@/hooks/useAppInstance";
import { usePublishIdentity } from "@/components/marketplace/PublishIdentity";
import { GigiMark } from "@/components/GigiMark";

/*
 * Why `clsx` and not `cn` on the rows below.
 *
 * `cn` runs tailwind-merge, which decides that anything matching `text-*` that
 * is not one of ITS known size names is a text COLOUR. The design system's
 * scale — `text-body`, `text-meta`, `text-title`, `text-micro` — is not in that
 * list, so a class list holding both a size and a colour ends up with only the
 * colour: `cn("text-body", "text-muted-foreground")` returns
 * `"text-muted-foreground"` and the row silently falls back to the inherited
 * 16 px. Font size and colour are different CSS properties and never conflict
 * in the stylesheet, so plain concatenation is the correct behaviour here.
 *
 * The real fix is one line in `lib/utils.ts` — teach tailwind-merge the scale
 * via `extendTailwindMerge({ extend: { classGroups: { "font-size": [{ text:
 * ["display","page","title","reading","body","meta","micro"] }] } } })`. That
 * file is outside this change; once it lands these can go back to `cn`.
 */

/**
 * The section ids the Agentic IDE answers to.
 *
 * Mirrors the nav row's own `matchIds` (see ./navGroups): the section has been
 * renamed twice and the older ids are still what some entry points set.
 */
const IDE_SECTIONS: readonly string[] = [
  "agentic-ide",
  "chat-workspace",
  "agentic-ide-classic",
];

/**
 * The voice status dot, in the three colours a status is allowed to have.
 *
 * It used to run on four shades of grey — `bg-muted-foreground/50`,
 * `bg-muted-foreground`, `bg-foreground/70`, `bg-foreground` — which is a ramp
 * nobody can read: "listening" and "idle" differed by an opacity, and the
 * loudest value on the ramp was "paused". Colour carries exactly three
 * meanings in this product, so the dot carries them too: green while the voice
 * path is actually doing something, red when it broke, amber while it is only
 * half up, and neutral ink when it is simply at rest.
 */
const VOICE_STATE_STYLE: Record<string, { dot: string; pulse: boolean }> = {
  // "Ready" IS a life state: the stack is up and waiting for the wake word.
  // A grey dot beside the word "Ready" told the reader "off".
  idle: { dot: "bg-success", pulse: false },
  listening: { dot: "bg-success", pulse: true },
  thinking: { dot: "bg-success", pulse: true },
  speaking: { dot: "bg-success", pulse: true },
  // The user muted or suspended the pipeline: neither working nor broken.
  paused: { dot: "bg-muted-foreground", pulse: false },
  error: { dot: "bg-destructive", pulse: false },
  // Not a supervisor state — the surface's own "a realtime transport is
  // negotiating" phase, which no backend state covers. Half up, so amber.
  connecting: { dot: "bg-warning", pulse: true },
};

export interface SidebarProps {
  /**
   * Rendered width in px. Owned by the app shell, because the seam that changes
   * it lives BETWEEN the sidebar and the main area — see `PaneResizer`. Left
   * optional so the sidebar still renders standalone (tests, storybook-style
   * one-offs) at its designed width.
   */
  width?: number;
  /**
   * Is the sidebar deliberately collapsed to its icon rail?
   *
   * Separate from `width` because the two answer different questions. The width
   * is a drag preference and survives a collapse — expanding restores the
   * column the user sized, not the designed default. Collapsing is a STATE, and
   * the app opens in it: the sidebar is navigation, and navigation is not what
   * the window is for. Left optional so the sidebar still renders standalone.
   */
  collapsed?: boolean;
  /** Toggle `collapsed`. Absent = the toggle button is not offered. */
  onToggleCollapsed?: () => void;
}

/** Width the sidebar was designed at, and the one a double-click restores.
 *  240 since 2026-09-02 (v4 redesign): the Cursor / Grok column. The
 *  navigation is a single 14 px list now, so it fits; the Agentic IDE's
 *  chat list, which drove the older 400, is still one drag away and the
 *  dragged width is remembered. */
export const SIDEBAR_DEFAULT_WIDTH = 240;

/**
 * localStorage key the dragged width is remembered under.
 *
 * `v2` since 2026-08-27. The seam used to stop at 200 px, and a column dragged
 * to that floor back then stayed there through every default since — on the
 * maintainer's desktop the Agentic IDE's session list was still 200 px wide
 * when its rows were redesigned around a two-line title that holds a whole
 * 48-character recap at the designed 320 (`WorkspaceChats`). At 200 the same
 * row holds twelve characters a line, so nothing about the redesign reached
 * the one box it was asked for. A new key seeds every column at the designed
 * width once; a dragged width is remembered again from there.
 *
 * `v3` with the 400 px default (same day): the wider column is the point of
 * the change, so every existing column is re-seeded at it once more.
 *
 * `v4` with the 240 px default (2026-09-02): the redesign's column.
 */
export const SIDEBAR_WIDTH_STORAGE_KEY = "jarvis.sidebar.width.v4";

/**
 * Narrowest the sidebar goes: the nav icons, and nothing else.
 *
 * The seam used to stop at 200 px, which is wide enough to still read every
 * label — so in the Agentic IDE, where a workspace of a dozen terminals wants
 * every pixel, a fifth of the window stayed spent on a list nobody was reading.
 * The rail keeps navigation one click away (the icons are still there, each
 * with its label on hover) while giving that space back to the panes. The
 * rail IS the deck's dock (`DockRail`) — same icons, same magnification, same
 * signals — so leaving the deck never drops the navigation back to a plainer
 * list.
 */
export const SIDEBAR_RAIL_WIDTH = 64;

/**
 * Below this dragged width the sidebar SNAPS to the rail rather than clipping.
 *
 * Between the two there is no useful layout: a 120 px sidebar shows half a word
 * per row, which reads as a broken column rather than a deliberate one. So the
 * band is skipped — pull past it and you get icons, pull back and you get text.
 */
export const SIDEBAR_RAIL_AT_WIDTH = 168;

export function Sidebar({
  width = SIDEBAR_DEFAULT_WIDTH,
  collapsed = false,
  onToggleCollapsed,
}: SidebarProps = {}) {
  const t = useT();
  const active = useEventStore((s) => s.activeSection);
  const setActive = useEventStore((s) => s.setActiveSection);
  const voiceState = useEventStore((s) => s.voiceState);
  const assistantName = useEventStore((s) => s.assistantName);
  // The dev instance (a second, restartable app beside the live one — see
  // jarvis.core.instance) shows a small tag so the two windows are never
  // confused; the default app shows nothing here.
  const appInstance = useAppInstance();
  const devTag = appInstance?.isDev ? appInstance.name.toUpperCase() : null;
  // "+ New" starts a new conversation of the KIND you are looking at: on the
  // chat surface an empty agent chat, on the voice stage a fresh voice run.
  // Sending someone standing in Voice to the chat page is what the one button
  // used to do, and it read as the button being broken.
  const { newChat, newVoiceRun } = useConversations();
  const newAgentChat = useAgentChatStore((s) => s.newChat);
  const setSurface = useHomeStore((s) => s.setSurface);
  // The front page's nav row names the face the switch picked (Voice / Chat),
  // see `presentNavItem`.
  const surface = useHomeStore((s) => s.surface);
  /*
   * The Agentic IDE in chat mode puts ITS chats at the top of this column.
   *
   * Chat mode is a surface, not a layout — the conversations you are having in
   * the workspace you opened, grouped by folder — and a chat surface with its
   * history two clicks away is a chat surface nobody uses. So while it is on,
   * `WorkspaceChats` leads the column and the sections follow underneath.
   *
   * It used to TAKE the column instead, with a "Sections" button swapping the
   * two faces. That made the two halves of the navigation mutually exclusive:
   * asking for a section threw the sessions away, and there was no state that
   * showed both (maintainer report 2026-08-27). Stacked, nothing is ever a
   * click away from being lost — the sessions stay put while a section is
   * picked, and the sections stay reachable while the sessions are read.
   * Only while the IDE is the section on screen: every other section gets the
   * plain navigation, with no workspace list bolted on top of it.
   */
  const ideView = useIdeChatStore((s) => s.view);
  /*
   * Is there anything for this column to list?
   *
   * Measured on the OPEN WORKSPACES, not on the active one. The two differ in
   * exactly one state and it is a state the user reaches on purpose: opening
   * one more workspace deactivates the front tab while the launcher asks for
   * a folder. Gated on the active workspace, the whole chat navigation
   * vanished at that moment and came back when the new workspace started —
   * so asking for a second project threw away the list of the first, which
   * reads as the sidebar breaking rather than a wizard opening.
   *
   * The old reading ("no workspace open means the wizard, not a chat") was
   * written when this column was headed "This workspace". It lists every open
   * workspace as its own band now, so it has something true to say for as
   * long as any of them is running.
   */
  const ideWorkspaceOpen = useIdeChatStore((s) => s.workspaces.length > 0);
  const onIdeSection = IDE_SECTIONS.includes(active);
  const chatFace = onIdeSection && ideWorkspaceOpen && ideView === "chat";
  const [toolsOpen, setToolsOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const profileRef = useRef<HTMLDivElement>(null);
  const profileButtonRef = useRef<HTMLButtonElement>(null);
  const identity = usePublishIdentity();
  useEffect(() => {
    setProfileOpen(false);
  }, [active]);
  useEffect(() => {
    if (!profileOpen) return;
    const dismiss = (event: PointerEvent) => {
      if (!profileRef.current?.contains(event.target as Node)) setProfileOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setProfileOpen(false);
        profileButtonRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, [profileOpen]);
  const resetTranscript = useHomeStore((s) => s.resetTranscript);
  // On the voice stage: clear the lane, drop the open voice thread and let the
  // backend forget the one it was seeded with. We stay on Voice and the mic
  // stays shut — the next wake word (or orb click) opens the new session.
  const startNewVoice = () => {
    resetTranscript();
    void newVoiceRun();
    setActive("chats");
  };
  // On the chat surface: an empty agent chat. The voice thread is cleared as
  // well so a reopened voice session does not linger behind the fresh page.
  const startNewChat = () => {
    newChat();
    newAgentChat();
    setSurface("chat");
    setActive("chats");
  };
  const onVoiceSurface = surface === "voice";
  // Shared readiness derivation (same source the banner + chat empty-state use).
  const { connected, voiceWarming, bootWarming, warming } = useVoiceReadiness();

  // Per-section provider health (same source as the API-Keys tab dots). The
  // sidebar surfaces only a hard "error" — a provider that is set up but failing
  // — so a broken key is visible from anywhere without opening the page. The
  // amber "needs setup" state is intentionally NOT shown here: on a fresh install
  // every unconfigured section would light up and the bar would never be calm.
  const { health: sectionHealth } = useSectionHealth();
  const apikeysHasError = useMemo(
    () => Object.values(sectionHealth).some((h) => h?.status === "error"),
    [sectionHealth],
  );
  // The footer card IS the button that opens API Keys, so its dot carries that
  // page's verdict rather than a decorative grey mark. Three honest states:
  // something is failing, something has answered, or nothing has reported yet
  // — a fresh install must not claim green before a single provider replied.
  const providersAnswering = useMemo(
    () => Object.values(sectionHealth).some((h) => h?.status === "ok"),
    [sectionHealth],
  );
  // A connected marketplace plugin whose token was revoked/expired (needs_reauth)
  // — surfaced as an amber dot on the row that fronts Plugins ("Skills & Tools"),
  // so a dead connection is visible app-wide, not only on the Plugins page. The
  // names let the tooltip say WHICH plugin, not just "something is off".
  const pluginAttention = usePluginAttention();
  const pluginsNeedReconnect = pluginAttention.count > 0;
  // The Local models health monitor (D7) writes a `local_models` record; a
  // failing or half-configured local setup gets the same amber dot — badge
  // only, never a toast.
  const localModelsHealth = sectionHealth.local_models;
  const localModelsNeedAttention =
    localModelsHealth?.status === "error" || localModelsHealth?.status === "needs_setup";
  // Name the culprit(s) in the hover text so the dot stops being cryptic; the
  // full plain-language banner + jump button live in the Plugins view itself.
  const pluginWarnTitle = pluginAttention.names.length
    ? `${t("sidebar.plugins_reconnect_alert")}: ${pluginAttention.names.join(", ")}`
    : t("sidebar.plugins_reconnect_alert");
  const agentsCount = useEventStore((s) =>
    s.events.filter((e) => e.name === "AgentStateChange").length > 0 ? undefined : 0,
  );

  // Read before the status line because BOTH depend on it now: the footer card
  // follows the VOICE MODE rather than the pipeline brain (in realtime mode the
  // pipeline brain is dormant, and showing it there misled the user —
  // "OpenRouter" while Gemini Live was doing all the talking), and the status
  // line needs its connecting phase. Same resolver as the mission-deck header
  // and orb (`useVoiceEngineDisplay`): a live session outranks the configured
  // pick so a mid-call cross-family fallback is visible (AP-22).
  const voiceMode = useVoiceMode();
  const engine = useVoiceEngineDisplay();

  // The window connects in ~1s but the voice feature warms up ~20s in the
  // background. During that gap show a "Voice starting…" spinner instead of the
  // normal idle "Ready" dot (which would falsely imply the mic already works).
  // Disconnected outranks warmup — "Offline" is the honest state with no socket.
  // voiceWarming / bootWarming / warming come from the shared useVoiceReadiness
  // hook so the sidebar dot, the banner and the chat empty-state never disagree.
  const showSpinner = warming || voiceMode.connecting;
  const vs = voiceMode.connecting
    ? VOICE_STATE_STYLE.connecting
    : VOICE_STATE_STYLE[voiceState] ?? VOICE_STATE_STYLE.idle;
  // A negotiating realtime transport outranks the pipeline's own state: the
  // subscription route needs 15-45 s before it can hear anything, and showing
  // the stale pre-call state there is what made a live handshake look frozen.
  const voiceLabel = !connected
    ? bootWarming
      ? t("voice_state.booting")
      : t("voice_state.offline")
    : voiceWarming
      ? t("voice_state.starting")
      : voiceMode.connecting
        ? t("voice_state.connecting")
        : t(`voice_state.${voiceState}`);

  const realtimeFooter = engine.tier === "realtime";
  const footerLabel = realtimeFooter
    ? t("sidebar.realtime_label")
    : t("sidebar.brain_label");
  const footerTooltip = realtimeFooter
    ? t("sidebar.realtime_tooltip")
    : t("sidebar.brain_tooltip");
  const footerProvider = engine.providerLabel;
  const footerModel = engine.model;

  // Dragged past the snap point the sidebar becomes a rail of icons. Everything
  // that only makes sense with a label beside it — the wake-word hint, the
  // realtime control, the brain card's provider and model — steps aside; the
  // navigation itself never does, because losing it would make the rail a dead
  // end rather than a narrow sidebar.
  // Two independent ways into the rail: the explicit toggle, and dragging the
  // seam past the snap point. Either one alone is enough — a user who dragged
  // the column narrow gets icons without having to also find the button.
  const railed = collapsed || width < SIDEBAR_RAIL_AT_WIDTH;

  const allItems = NAV_GROUPS.flat();
  const findItem = (id: string) => allItems.find((item) => item.id === id)!;
  const toolIds = ["memory", "board", "docs", "sessions", "run_inspector", "clis", "agentic-ide"];
  const toolItems = toolIds.map(findItem);
  const profileItems = [...NAV_GROUPS[3], ...NAV_GROUPS[4], ...NAV_FOOTER_ITEMS];
  const primaryIds = ["chats", "agents", "visualization", "tasks", "plugins", "marketplace"];
  const assignedIds = new Set([...primaryIds, ...toolIds, ...profileItems.map((item) => item.id)]);
  const moreItems = [...toolItems, ...allItems.filter((item) => !assignedIds.has(item.id))];
  const rowClass = "flex min-h-9 w-full items-center gap-2.5 rounded-md px-3 text-base font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
  const renderRow = (raw: NavItem, compact = railed) => {
    const item = presentNavItem(raw, surface);
    return <NavRow key={item.id} item={item} label={resolveNavLabel(t, item)} compact={compact}
      active={item.matchIds ? item.matchIds.includes(active) : item.id === active}
      badge={item.id === "agents" ? agentsCount : undefined}
      betaLabel={item.beta ? t("nav.agentic_ide_beta") : undefined}
      alert={item.id === "apikeys" && apikeysHasError} alertTitle={t("sidebar.apikeys_alert")}
      warn={item.id === "plugins" ? pluginsNeedReconnect : item.id === "local-models" && localModelsNeedAttention}
      warnTitle={item.id === "local-models" ? localModelsHealth?.detail || localModelsHealth?.reason || undefined : pluginWarnTitle}
      onClick={() => { setActive(item.id); setProfileOpen(false); }} />;
  };

  return (
    // No right border: the draggable seam beside it draws that line now, and
    // two 1px lines three pixels apart read as a rendering fault.
    // z-20: above the stage column, which carries no z-index of its own so
    // that the overlays inside it are not trapped below this one (App.tsx).
    // That same open stage means a section's own z-20 layer (the IDE's pane
    // chat, say) ties with this column and wins on DOM order — so the rail's
    // hover label, which flies out past the sidebar's edge, does NOT rely on
    // this z-index: `DockRail` portals it to <body> at the tooltip level. The
    // app-wide layers (toasts, docks, dialogs) all sit at z-40 and above and
    // really do cover this column.
    <aside
      style={{ width: railed ? SIDEBAR_RAIL_WIDTH : width }}
      data-testid="sidebar"
      data-railed={railed ? "true" : "false"}
      className="jarvis-nav-surface relative isolate z-20 flex h-full shrink-0 flex-col"
    >
      {/* One 8px gutter down the whole column — header, navigation and footer
          share it, so the rows, the "+ New" button and the brain card all line
          up on the same left edge. */}
      <div className={cn("px-3", railed ? "py-2.5" : "pb-2 pt-3")}>
        <div
          className={cn(
            "flex items-center gap-2.5",
            railed && "flex-col justify-center gap-1.5",
          )}
        >
          <span
            data-testid="sidebar-style-avatar"
            data-variant="logo"
            title={railed ? `${assistantName} — ${voiceLabel}` : undefined}
            className={cn("relative shrink-0", railed ? "h-9 w-9" : "h-7 w-7")}
          >
            {railed && devTag && (
              <span
                data-testid="sidebar-instance-tag"
                title={t("sidebar.instance_dev_hint")}
                className="absolute -bottom-1 -right-1 z-10 rounded-sm bg-primary px-1 text-xs font-medium leading-none text-primary-foreground"
              >
                {devTag}
              </span>
            )}
            <GigiMark size={railed ? 36 : 28} />
          </span>
          {!railed && (
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="flex min-w-0 items-center gap-2 text-base font-medium text-foreground-strong">
                <span className="truncate">{assistantName}</span>
                {devTag && (
                  // A mark, not a status: the fill is the neutral accent, so it
                  // never competes with the green/amber/red the voice dot
                  // beside it uses to mean something.
                  <span
                    data-testid="sidebar-instance-tag"
                    title={t("sidebar.instance_dev_hint")}
                    className="shrink-0 rounded-sm bg-primary px-1.5 text-xs font-medium leading-none text-primary-foreground"
                  >
                    {devTag}
                  </span>
                )}
              </span>
              {/* The state: a 6 px dot in the status colour, then the word. */}
              <span className="flex min-w-0 items-center gap-1.5 text-sm text-muted-foreground">
                {showSpinner ? (
                  <Loader2
                    className="h-3 w-3 shrink-0 animate-spin"
                    data-testid="voice-starting-spinner"
                    aria-hidden
                  />
                ) : (
                  <span
                    className={cn(
                      "h-1.5 w-1.5 shrink-0 rounded-full",
                      vs.dot,
                      vs.pulse && "animate-jarvis-pulse",
                    )}
                    aria-hidden
                  />
                )}
                <span className="truncate">{voiceLabel}</span>
              </span>
            </div>
          )}
          {railed &&
            (showSpinner ? (
              <Loader2
                className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground"
                data-testid="voice-starting-spinner"
                aria-hidden
              />
            ) : (
              <span
                className={cn(
                  "h-2 w-2 rounded-full",
                  vs.dot,
                  vs.pulse && "animate-jarvis-pulse",
                )}
                aria-hidden
              />
            ))}
          {onToggleCollapsed && (
            <button
              type="button"
              data-testid="sidebar-collapse-toggle"
              onClick={onToggleCollapsed}
              aria-expanded={!railed}
              title={railed ? t("sidebar.expand") : t("sidebar.collapse")}
              aria-label={railed ? t("sidebar.expand") : t("sidebar.collapse")}
              className={cn(
                "flex shrink-0 items-center justify-center rounded-md text-muted-foreground",
                // Hover goes UP the surface ladder. It used to be
                // `hover:bg-background/20`, which composites the PAGE colour
                // over the rail — on near-black that is darker than rest, so
                // the control dimmed under the pointer.
                "transition-colors hover:bg-secondary hover:text-foreground",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                railed ? "h-7 w-7" : "-mr-1 h-7 w-7",
              )}
            >
              {railed ? (
                <PanelLeftOpen className="h-4 w-4" aria-hidden />
              ) : (
                <PanelLeftClose className="h-4 w-4" aria-hidden />
              )}
            </button>
          )}
        </div>
        {!railed && !chatFace && (
          <>
            {/* The front page's one switch (maintainer sketch, 2026-08-23):
                Voice or Chat. The live transcript that used to sit here moved
                onto the voice stage itself, where it has the room to be read.
                Hidden while the IDE's chats own the column: two switches with
                "Chat" on both halves are two questions nobody asked. */}
            <SurfaceSwitch className="mt-3" />
            {/* A full-width outline button: the one action this column offers. */}
            <button
              type="button"
              onClick={onVoiceSurface ? startNewVoice : startNewChat}
              data-testid="sidebar-new-chat"
              className="mt-2 flex h-9 w-full items-center justify-center gap-2 rounded-md border border-border-strong px-3 text-base font-medium text-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-sidebar"
            >
              <Plus aria-hidden className="h-4 w-4 shrink-0" />
              {onVoiceSurface ? t("sidebar.new_voice_chat") : t("sidebar.new_chat")}
            </button>
            <BrowserRealtimeControl />
          </>
        )}
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto scrollbar-jarvis">
        <nav aria-label={t("sidebar.sections")} className="space-y-1 px-2 py-2">
          <ul className="space-y-1">
            {renderRow(findItem("chats"))}
            {renderRow(findItem("agents"))}
            {renderRow(findItem("visualization"))}
          </ul>
          <button type="button" onClick={() => { setToolsOpen(!toolsOpen); setMoreOpen(false); }}
            aria-expanded={toolsOpen} aria-controls="sidebar-tools" title={t("sidebar.jarvis_tools")}
            data-testid="sidebar-tools-toggle" className={cn(rowClass, toolItems.some((item) => (item.matchIds ?? [item.id]).includes(active)) && "jarvis-nav-active bg-secondary text-foreground")}>
            <Folder aria-hidden className="h-4 w-4 shrink-0" />
            {!railed && <><span className="flex-1 text-left">{t("sidebar.jarvis_tools")}</span><ChevronDown className={cn("h-3.5 w-3.5", toolsOpen && "rotate-180")} /></>}
          </button>
          {toolsOpen && <ul id="sidebar-tools" className={cn("space-y-1", !railed && "ml-3 border-l border-border pl-1")}>
            {toolItems.map((item) => renderRow(item))}
          </ul>}
          <ul className="space-y-1">
            {renderRow({ ...findItem("tasks"), labelKey: "sidebar.scheduled" })}
            {renderRow({ ...findItem("plugins"), labelKey: "nav.plugins" })}
          </ul>
          <button type="button" onClick={() => { setMoreOpen(!moreOpen); setToolsOpen(false); }} aria-expanded={moreOpen}
            aria-controls="sidebar-more" title={t("sidebar.more")} data-testid="sidebar-more-toggle" className={rowClass}>
            <MoreHorizontal aria-hidden className="h-4 w-4 shrink-0" />
            {!railed && <span>{t(moreOpen ? "sidebar.show_less" : "sidebar.more")}</span>}
          </button>
          {moreOpen && <ul id="sidebar-more" className="space-y-1">{moreItems.map((item) => renderRow(item))}</ul>}
        </nav>
        {!railed && <section className="mt-4 px-2 pb-3" aria-label={t("sidebar.recent_chats")}>
          <h2 className="px-3 pb-2 text-sm font-medium text-muted-foreground">{t("sidebar.recent_chats")}</h2>
          {chatFace ? <WorkspaceChats /> : <RecentChats />}
        </section>}
      </div>

      <div ref={profileRef} className="relative shrink-0 border-t border-border p-2"
        onBlur={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node)) setProfileOpen(false);
        }}>
        <div className={cn("flex items-center gap-1", railed && "flex-col")}>
          <button ref={profileButtonRef} type="button" onClick={() => setProfileOpen(!profileOpen)}
            aria-expanded={profileOpen} aria-controls="sidebar-profile-panel" title={t("nav.profile")}
            data-testid="sidebar-profile-toggle" className={cn(rowClass, "min-w-0 flex-1")}>
            <span className="relative shrink-0">
              <UserCircle2 aria-hidden className="h-7 w-7" />
              {(apikeysHasError || localModelsNeedAttention) && <span data-testid="sidebar-profile-attention"
                role="status" aria-label={t("sidebar.apikeys_alert")}
                className={cn("absolute bottom-0 right-0 h-2 w-2 rounded-full", apikeysHasError ? "bg-destructive" : "bg-warning")} />}
            </span>
            {!railed && <span className="min-w-0 flex-1 truncate text-left">{identity.data?.signed_in ? identity.data.login || t("nav.profile") : t("nav.profile")}</span>}
          </button>
          <button type="button" onClick={() => setActive("marketplace")} title={t("nav.marketplace")}
            aria-label={t("nav.marketplace")} data-testid="nav-row-marketplace"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <Store aria-hidden className="h-5 w-5" />
          </button>
        </div>
        {profileOpen && <div id="sidebar-profile-panel" data-testid="sidebar-profile-panel"
          className="absolute bottom-full left-2 z-50 mb-2 w-72 max-w-[calc(100vw-1rem)] rounded-xl border border-border bg-card p-2 shadow-xl">
          <div className="max-h-[65vh] overflow-y-auto scrollbar-jarvis">
            <ul className="space-y-0.5">{profileItems.map((item) => renderRow(item, false))}</ul>
          </div>
        <button
          type="button"
          onClick={() => setActive("apikeys")}
          data-testid="sidebar-brain-card"
          className={cn(
            "group flex w-full items-center rounded-lg border border-border bg-card text-left transition-colors hover:border-border-strong",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-sidebar",
            "mt-2 gap-3 p-3",
          )}
          // On the rail the card shrinks to its status dot, so everything it
          // would have said moves into the hover text — otherwise the dot is a
          // button with no stated purpose.
          title={
            railed
              ? `${footerLabel}: ${footerProvider}${footerModel ? ` · ${footerModel}` : ""} — ${footerTooltip}`
              : footerTooltip
          }
        >
          {/* Not decoration: the dot is this button's destination reporting in
              — red when a configured provider is failing, green once one has
              actually answered, neutral until any of them has. On the rail it
              is the whole card, which is why it has to mean something. */}
          <div
            data-testid="sidebar-footer-health"
            className={cn(
              "h-2 w-2 shrink-0 rounded-full",
              apikeysHasError
                ? "bg-destructive"
                : providersAnswering
                  ? "bg-success"
                  : "bg-muted-foreground",
            )}
          />
          {(
          <div className="flex-1 min-w-0">
            <div
              className="text-xs uppercase tracking-wide text-foreground-faint"
              data-testid="sidebar-footer-tier"
            >
              {footerLabel}
            </div>
            <div className="truncate text-base font-medium text-foreground-strong">
              {footerProvider}
            </div>
            {/* The model id actually in use (e.g. "claude-opus-4-8", or the
                realtime model in realtime mode) — the user asked to see WHICH
                model is in use, not just the provider. */}
            {footerModel && (
              <div
                className="truncate text-sm text-muted-foreground"
                title={footerModel}
                data-testid="sidebar-brain-model"
              >
                {footerModel}
              </div>
            )}
          </div>
          )}
          {(
            <ChevronRight className="h-4 w-4 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-foreground" />
          )}
        </button>
        </div>}
      </div>
    </aside>
  );
}

function NavRow({
  compact = false,
  item,
  label,
  active,
  badge,
  betaLabel,
  alert = false,
  alertTitle,
  warn = false,
  warnTitle,
  expand,
  onClick,
  children,
}: {
  compact?: boolean;
  item: NavItem;
  label: string;
  active: boolean;
  badge?: number;
  /** A chevron at the row's end that folds `children` out under the row.
   *  It is a second button beside the row, not a part of it: the row keeps
   *  opening its section, the chevron only opens the list. */
  expand?: { open: boolean; onToggle: () => void; label: string };
  /** What hangs under the row while `expand.open` — the Chat row's history. */
  children?: ReactNode;
  /** Small pill rendered right after the label (e.g. "Beta") — set from
   *  `item.beta`, translated by the caller so this component stays i18n-free. */
  betaLabel?: string;
  /** Draw a red status dot on the row — a section this row fronts has a provider
   *  that is set up but failing, so the problem is visible app-wide. */
  alert?: boolean;
  /** Plain-language hover text for the alert dot. */
  alertTitle?: string;
  /** Draw an amber status dot — a softer "needs attention" than `alert` (e.g. a
   *  connected plugin whose token was revoked and needs a one-click reconnect). */
  warn?: boolean;
  /** Plain-language hover text for the warn dot. */
  warnTitle?: string;
  onClick: () => void;
}) {
  const Icon = item.icon;
  const hint = alert ? alertTitle : warn ? warnTitle : undefined;
  /*
   * Selection is drawn on the WHOLE ROW.
   *
   * It used to be a `bg-foreground/10` fill on the 24×24 icon box while the
   * ~200×40 row it belongs to stayed at the rail's own ground — a correct lift
   * spent on 3.5 % of the thing it was meant to mark, which is why "you are
   * here" was the hardest question to answer in this app. The row takes the
   * fill now (one step up the ladder, inset from the column edge by the nav's
   * own padding) plus the strong ink, and the icon box is gone entirely: it
   * existed only to hold that fill.
   *
   * Hover stops one rung BELOW selection — `bg-card`, not `bg-secondary` — so
   * a hovered row and the selected row never look alike. That difference is
   * the whole point of a fill-based selection; matching them would give the
   * fill with one hand and take the meaning away with the other.
   */
  return (
    <li>
      {/* The chevron is positioned against THIS box, not the <li>. If the
          open list lived in the same relative parent, `top-1/2` walked to
          the middle of the chats and the rows painted over the only control
          that folds them away (maintainer, 2026-09-02). */}
      <div className={expand ? "relative" : undefined}>
        <button
          type="button"
          data-testid={`nav-row-${item.id}`}
          onClick={onClick}
          title={compact ? `${label}${hint ? ` — ${hint}` : ""}` : hint}
          aria-label={compact ? label : undefined}
          className={clsx(
            "group relative flex h-9 w-full items-center gap-2.5 rounded-md px-3 text-base font-medium transition-colors",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            // Leave the chevron its own column so the two buttons never overlap.
            expand && "pr-9",
            // Rest is muted ink; hover AND active are the same lift with body
            // ink, and only the active row carries the 2 px accent bar at the
            // left edge (`.jarvis-nav-active`).
            active
              ? "jarvis-nav-active bg-secondary text-foreground"
              : "text-muted-foreground hover:bg-secondary hover:text-foreground",
          )}
        >
          <Icon
            aria-hidden
            className={cn(
              "h-4 w-4 shrink-0 transition-colors",
              active ? "text-foreground" : "text-muted-foreground group-hover:text-foreground",
            )}
          />
          <span className={cn("flex min-w-0 flex-1 items-center gap-2 text-left", compact && "hidden")}>
            {/* The row is a fixed 40px now, so a long label has to be cut rather
                than allowed to wrap out of it. */}
            <span className="truncate">{label}</span>
            {betaLabel && (
              <span
                data-testid={`nav-beta-${item.id}`}
                className="shrink-0 rounded-sm border border-border bg-secondary px-1 text-xs font-medium text-muted-foreground"
              >
                {betaLabel}
              </span>
            )}
          </span>
          {/* No ring around these: they sit in clear space at the row's end, and
              a rim on something that already has a fill is one device too many. */}
          {alert && (
            <span
              data-testid={`nav-alert-${item.id}`}
              role="status"
              aria-label={alertTitle}
              className="h-2 w-2 shrink-0 rounded-full bg-destructive"
            />
          )}
          {!alert && warn && (
            <span
              data-testid={`nav-warn-${item.id}`}
              role="status"
              aria-label={warnTitle}
              className="h-2 w-2 shrink-0 rounded-full bg-warning"
            />
          )}
          {badge !== undefined && badge > 0 && (
            <span className="shrink-0 rounded-sm bg-secondary px-1 text-xs tabular-nums text-muted-foreground">
              {badge}
            </span>
          )}
        </button>
        {expand && (
          <button
            type="button"
            onClick={expand.onToggle}
            aria-expanded={expand.open}
            aria-label={expand.label}
            title={expand.label}
            data-testid={`nav-expand-${item.id}`}
            className={cn(
              "absolute right-2 top-1/2 z-10 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-md transition-colors",
              // `hover:bg-background/60` painted the PAGE over the rail here,
              // i.e. it went darker under the pointer. Up the ladder instead.
              "text-muted-foreground hover:bg-surface-raised hover:text-foreground",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            )}
          >
            <ChevronDown
              aria-hidden
              className={cn("h-3.5 w-3.5 transition-transform", expand.open && "rotate-180")}
            />
          </button>
        )}
      </div>
      {expand?.open && children}
    </li>
  );
}
