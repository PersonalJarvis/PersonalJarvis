import {
  Loader2,
  ChevronRight,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
} from "lucide-react";
import {
  NAV_GROUPS,
  presentNavItem,
  resolveNavLabel,
  type NavItem,
} from "@/components/layout/navGroups";
import { DockRail } from "@/components/layout/DockRail";
import { useEventStore } from "@/store/events";
import { useVoiceReadiness } from "@/hooks/useVoiceReadiness";
import { useVoiceMode } from "@/hooks/useVoiceMode";
import { useSectionHealth } from "@/hooks/useProviders";
import { usePluginAttention } from "@/hooks/usePluginAttention";
import { useVoiceEngineDisplay } from "@/hooks/useVoiceEngineDisplay";
import { cn } from "@/lib/utils";
import { useMemo, useState } from "react";
import { useT } from "@/i18n";
import { BrowserRealtimeControl } from "@/components/voice/BrowserRealtimeControl";
import { SurfaceSwitch } from "@/components/home/SurfaceSwitch";
import { RecentChats } from "@/components/home/RecentChats";
import { useConversations } from "@/hooks/useConversations";
import { useHomeStore } from "@/store/home";
import { useAgentChatStore } from "@/store/agentChat";
import { useIdeChatStore } from "@/store/ideChat";
import { WorkspaceChats } from "@/components/agentic/WorkspaceChats";
import { PRODUCT_NAME } from "@/lib/branding";
import { useAppInstance } from "@/hooks/useAppInstance";

// A logo request that fails once (backend restarting, dist mid-rebuild) would
// otherwise stick as the browser's broken-image glyph forever — an <img> never
// retries on its own. Bounded cache-busted retries let it heal itself.
const LOGO_RETRY_MAX = 5;
const LOGO_RETRY_BASE_MS = 1500;

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

const VOICE_STATE_STYLE: Record<string, { dot: string; pulse: boolean }> = {
  idle: { dot: "bg-muted-foreground/50", pulse: false },
  listening: { dot: "bg-emerald-400", pulse: true },
  thinking: { dot: "bg-primary", pulse: true },
  speaking: { dot: "bg-primary", pulse: true },
  // The user muted or suspended the pipeline: neither working nor broken.
  paused: { dot: "bg-amber-400", pulse: false },
  error: { dot: "bg-destructive", pulse: false },
  // Not a supervisor state — the surface's own "a realtime transport is
  // negotiating" phase, which no backend state covers.
  connecting: { dot: "bg-amber-400", pulse: true },
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
 *  320 since 2026-08-23: the front page's controls live in this column now
 *  and the maintainer dragged it wider on first use — that is the default. */
export const SIDEBAR_DEFAULT_WIDTH = 320;

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
 */
export const SIDEBAR_WIDTH_STORAGE_KEY = "jarvis.sidebar.width.v2";

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

  const [logoRetry, setLogoRetry] = useState(0);

  // Dragged past the snap point the sidebar becomes a rail of icons. Everything
  // that only makes sense with a label beside it — the wake-word hint, the
  // realtime control, the brain card's provider and model — steps aside; the
  // navigation itself never does, because losing it would make the rail a dead
  // end rather than a narrow sidebar.
  // Two independent ways into the rail: the explicit toggle, and dragging the
  // seam past the snap point. Either one alone is enough — a user who dragged
  // the column narrow gets icons without having to also find the button.
  const railed = collapsed || width < SIDEBAR_RAIL_AT_WIDTH;

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
      <div className={cn("border-b border-border", railed ? "px-2 py-3" : "px-4 py-4")}>
        <div
          className={cn(
            "flex items-center gap-3",
            railed && "flex-col justify-center gap-1.5",
          )}
        >
          {/* The original Personal Jarvis logo — the ghost mascot. A snapshot
              had swapped the header avatar for a bar glyph / gold-spark mark;
              this is the canonical brand identity (jarvis-gigi). */}
          <span
            data-testid="sidebar-style-avatar"
            data-variant="logo"
            title={railed ? `${assistantName} — ${voiceLabel}` : undefined}
            className={cn(
              "relative flex shrink-0 items-center justify-center",
              railed ? "h-9 w-9" : "h-11 w-11",
            )}
          >
            {railed && devTag && (
              <span
                data-testid="sidebar-instance-tag"
                title={t("sidebar.instance_dev_hint")}
                className="absolute -bottom-1 -right-1 rounded-[3px] bg-primary px-[3px] py-px font-mono text-[7px] font-bold leading-none tracking-wider text-primary-foreground"
              >
                {devTag}
              </span>
            )}
            <img
              src={
                logoRetry === 0
                  ? "/jarvis-logo.png"
                  : `/jarvis-logo.png?retry=${logoRetry}`
              }
              width={railed ? 32 : 40}
              height={railed ? 32 : 40}
              alt={PRODUCT_NAME}
              className="shrink-0"
              onError={() => {
                if (logoRetry < LOGO_RETRY_MAX) {
                  window.setTimeout(
                    () => setLogoRetry((n) => n + 1),
                    (logoRetry + 1) * LOGO_RETRY_BASE_MS,
                  );
                }
              }}
            />
          </span>
          {!railed && (
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="flex min-w-0 items-center gap-1.5 font-display text-sm font-semibold tracking-tight">
                <span className="truncate">{assistantName}</span>
                {devTag && (
                  <span
                    data-testid="sidebar-instance-tag"
                    title={t("sidebar.instance_dev_hint")}
                    className="shrink-0 rounded-[4px] bg-primary px-1 py-px font-mono text-[9px] font-bold leading-none tracking-wider text-primary-foreground"
                  >
                    {devTag}
                  </span>
                )}
              </span>
              <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {voiceLabel}
              </span>
            </div>
          )}
          {showSpinner ? (
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
          )}
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
                "transition-colors hover:bg-background/20 hover:text-foreground",
                "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
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
            <button
              type="button"
              onClick={onVoiceSurface ? startNewVoice : startNewChat}
              data-testid="sidebar-new-chat"
              className="mt-2 flex w-full items-center gap-2 rounded-lg border border-border bg-card px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:border-primary/40 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            >
              <span className="flex h-4 w-4 items-center justify-center rounded bg-primary text-primary-foreground">
                <Plus aria-hidden className="h-3 w-3" />
              </span>
              {onVoiceSurface ? t("sidebar.new_voice_chat") : t("sidebar.new_chat")}
            </button>
            <BrowserRealtimeControl />
          </>
        )}
      </div>

      {railed ? (
        // The rail is the app-wide icon dock: it carries the same signals (the
        // API-Keys error pip, the plugin reconnect pip, the Skills → Plugins
        // shortcut) from its own sources, so nothing here has to be threaded in.
        <DockRail className="min-h-0 flex-1" />
      ) : (
        // ONE scrolling body, whatever is in it. While the IDE's chat mode is
        // on, the workspace's sessions lead it and the sections follow; the
        // two used to be alternatives and the maintainer could reach only one
        // of them at a time. Both lists scroll together on purpose: two
        // scrollbars three pixels apart in a 320 px column read as a fault,
        // and a fixed session list with its own scrollbar would put a hard
        // ceiling on how many sessions a workspace may show.
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto scrollbar-jarvis">
          {chatFace && (
            <>
              <WorkspaceChats />
              <div className="mx-3 border-t border-border/60" aria-hidden />
            </>
          )}
          <nav className="p-2">
            {/* One list, not two: a voice session IS a run, and showing it as
                "recent run" and "recent chat" read as a duplicate (maintainer,
                2026-08-23). The Run Inspector keeps the run view.
                Safe to stand under the workspace bands: this lists the front
                page's own conversations, never the IDE's coding sessions (see
                RecentChats) — the two never name the same thing twice. */}
            <RecentChats />
            <div className="mx-1 mb-1 mt-2 border-t border-border/60" aria-hidden />
            {NAV_GROUPS.map((group, groupIndex) => (
              <ul
                key={groupIndex}
                className={cn(
                  "space-y-0.5",
                  // Thin divider + breathing room above every group after the first.
                  groupIndex > 0 && "mt-2 border-t border-border/40 pt-2",
                )}
              >
                {group.map((raw) => {
                  const item = presentNavItem(raw, surface);
                  return (
                    <NavRow
                      key={item.id}
                      item={item}
                      label={resolveNavLabel(t, item)}
                      active={item.matchIds ? item.matchIds.includes(active) : item.id === active}
                      badge={item.id === "agents" ? agentsCount : undefined}
                      betaLabel={item.beta ? t("nav.agentic_ide_beta") : undefined}
                      alert={item.id === "apikeys" ? apikeysHasError : false}
                      alertTitle={t("sidebar.apikeys_alert")}
                      warn={
                        item.id === "skills"
                          ? pluginsNeedReconnect
                          : item.id === "local-models"
                            ? localModelsNeedAttention
                            : false
                      }
                      warnTitle={
                        item.id === "local-models"
                          ? localModelsHealth?.detail || localModelsHealth?.reason || undefined
                          : pluginWarnTitle
                      }
                      // A plugin problem sends the "Skills & Tools" row straight into
                      // the Plugins tab (where the banner + jump button are), so one
                      // click lands on the fix instead of the default Skills tab.
                      onClick={() =>
                        setActive(
                          item.id === "skills" && pluginsNeedReconnect ? "plugins" : item.id,
                        )
                      }
                    />
                  );
                })}
              </ul>
            ))}
          </nav>
        </div>
      )}

      <div className={cn("border-t border-border", railed ? "p-1.5" : "p-3")}>
        <button
          type="button"
          onClick={() => setActive("apikeys")}
          className={cn(
            "jarvis-message-surface group flex w-full items-center rounded-lg border border-border text-left transition-colors hover:border-primary/40",
            railed ? "justify-center px-2 py-2" : "gap-3 px-3 py-2",
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
          <div className="h-2 w-2 shrink-0 rounded-full bg-primary shadow-[0_0_8px_rgba(255,214,10,0.7)]" />
          {!railed && (
          <div className="flex-1 min-w-0">
            <div
              className="text-[10px] uppercase tracking-wider text-muted-foreground"
              data-testid="sidebar-footer-tier"
            >
              {footerLabel}
            </div>
            <div className="text-xs font-medium truncate">{footerProvider}</div>
            {/* The model id actually in use (e.g. "claude-opus-4-8", or the
                realtime model in realtime mode) — the user asked to see WHICH
                model is in use, not just the provider. */}
            {footerModel && (
              <div
                className="text-[10px] text-muted-foreground/70 truncate"
                title={footerModel}
                data-testid="sidebar-brain-model"
              >
                {footerModel}
              </div>
            )}
          </div>
          )}
          {!railed && (
            <ChevronRight className="h-4 w-4 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-primary" />
          )}
        </button>
      </div>
    </aside>
  );
}

function NavRow({
  item,
  label,
  active,
  badge,
  betaLabel,
  alert = false,
  alertTitle,
  warn = false,
  warnTitle,
  onClick,
}: {
  item: NavItem;
  label: string;
  active: boolean;
  badge?: number;
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
  return (
    <li>
      <button
        type="button"
        data-testid={`nav-row-${item.id}`}
        onClick={onClick}
        title={hint}
        className={cn(
          "group relative flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm transition-all",
          "hover:bg-background/20",
          active
            ? "jarvis-message-surface text-foreground shadow-[inset_2px_0_0_hsl(var(--primary))]"
            : "text-muted-foreground hover:text-foreground",
        )}
      >
        <Icon
          className={cn(
            "h-4 w-4 shrink-0 transition-colors",
            active ? "text-primary" : "text-muted-foreground group-hover:text-foreground",
          )}
        />
        <span className="flex flex-1 items-center gap-1.5 text-left">
          {label}
          {betaLabel && (
            <span
              data-testid={`nav-beta-${item.id}`}
              className="shrink-0 rounded-full border border-primary/30 bg-primary/10 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide text-primary"
            >
              {betaLabel}
            </span>
          )}
        </span>
        {alert && (
          <span
            data-testid={`nav-alert-${item.id}`}
            role="status"
            aria-label={alertTitle}
            className="h-2 w-2 shrink-0 rounded-full bg-destructive ring-2 ring-background"
          />
        )}
        {!alert && warn && (
          <span
            data-testid={`nav-warn-${item.id}`}
            role="status"
            aria-label={warnTitle}
            className="h-2 w-2 shrink-0 rounded-full bg-amber-500 ring-2 ring-background"
          />
        )}
        {badge !== undefined && badge > 0 && (
          <span className="rounded-full bg-primary/20 px-1.5 py-0.5 text-[10px] font-semibold text-primary">
            {badge}
          </span>
        )}
      </button>
    </li>
  );
}
