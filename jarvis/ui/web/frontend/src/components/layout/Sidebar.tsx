import {
  MessageSquare,
  Users,
  Boxes,
  BookOpen,
  KeyRound,
  Settings,
  UserCircle2,
  ListTodo,
  Drama,
  FolderOpen,
  Gauge,
  Notebook,
  Sparkles,
  Mic,
  Terminal,
  Share2,
  Contact,
  MessageSquareWarning,
  ScrollText,
  MessagesSquare,
  Frame,
  Image as ImageIcon,
  Loader2,
  type LucideIcon,
  ChevronRight,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";
import { useEventStore, type SectionId } from "@/store/events";
import { useVoiceReadiness } from "@/hooks/useVoiceReadiness";
import { useVoiceMode } from "@/hooks/useVoiceMode";
import { useSectionHealth } from "@/hooks/useProviders";
import { usePluginAttention } from "@/hooks/usePluginAttention";
import { cn } from "@/lib/utils";
import { useMemo, useState } from "react";
import { useT } from "@/i18n";
import { BrowserRealtimeControl } from "@/components/voice/BrowserRealtimeControl";
import { PRODUCT_NAME } from "@/lib/branding";

interface NavItem {
  id: SectionId;
  labelKey: string;
  icon: LucideIcon;
  // When set, the row is highlighted while the active section is any of these
  // ids — used by the merged section entries ("Skills & Tools" fronting
  // skills/plugins/mcps, "CLIs" fronting clis/cli-test-hub); the active id
  // doubles as the tab state.
  matchIds?: SectionId[];
  // English fallback shown when `labelKey` has no translation yet in the active
  // locale (the i18n resolver returns the key itself on a miss).
  fallbackLabel?: string;
  // Draws a small "Beta" pill after the label — the Agentic IDE runs real
  // coding-agent CLIs against the user's own filesystem, which is a step
  // riskier than the rest of the app, so the row says so up front.
  beta?: boolean;
}

// Resolve a nav row's label, preferring the active-locale translation and
// falling back to the English `fallbackLabel` when the key is not yet present
// (the i18n resolver returns the key itself on a miss).
function resolveNavLabel(t: (key: string) => string, item: NavItem): string {
  const resolved = t(item.labelKey);
  return resolved === item.labelKey && item.fallbackLabel ? item.fallbackLabel : resolved;
}

// A logo request that fails once (backend restarting, dist mid-rebuild) would
// otherwise stick as the browser's broken-image glyph forever — an <img> never
// retries on its own. Bounded cache-busted retries let it heal itself.
const LOGO_RETRY_MAX = 5;
const LOGO_RETRY_BASE_MS = 1500;

// Sidebar nav, clustered into logical groups separated by a thin divider:
//   1) daily tools   2) content & data   3) configuration   4) social links.
// The render walks the groups in order and draws a separator between them, so
// the order below IS the on-screen order.
const NAV_GROUPS: NavItem[][] = [
  // 1) Daily tools — what the user reaches for most often.
  [
    { id: "chats", labelKey: "nav.chats", icon: MessageSquare },
    { id: "agents", labelKey: "nav.agents", icon: Users },
    // Skills & Tools — Skills + Plugins + MCPs behind one tab switch. The id
    // "skills" is the default landing (Skills tab); matchIds keeps the row
    // highlighted for any of the fronted sections.
    {
      id: "skills",
      labelKey: "nav.extensions",
      icon: Boxes,
      matchIds: ["skills", "plugins", "mcps"],
    },
    // CLIs — the CLIs list + the CLI Test Hub behind one tab switch (CLIs first).
    { id: "clis", labelKey: "nav.clis_hub", icon: Terminal, matchIds: ["clis", "cli-test-hub"] },
  ],
  // 2) Content & data — things the user reads, edits, or browses.
  [
    { id: "tasks", labelKey: "nav.tasks", icon: ListTodo },
    { id: "sessions", labelKey: "nav.sessions", icon: Mic },
    { id: "run_inspector", labelKey: "nav.run_inspector", icon: Gauge },
    // The visual stage. Sits with the content group next to Outputs on
    // purpose: it reads the same run archive, it just shows the pictures in it
    // instead of listing the files.
    {
      id: "visualization",
      labelKey: "nav.visualization",
      icon: Frame,
      fallbackLabel: "Visualization",
    },
    { id: "board", labelKey: "nav.board", icon: Sparkles },
    { id: "memory", labelKey: "nav.wiki", icon: Notebook },
    { id: "contacts", labelKey: "nav.contacts", icon: Contact },
    { id: "profile", labelKey: "nav.profile", icon: UserCircle2 },
    {
      id: "agent-instructions",
      labelKey: "nav.agent_instructions",
      icon: ScrollText,
      fallbackLabel: "Instructions",
    },
    { id: "docs", labelKey: "nav.docs", icon: BookOpen },
  ],
  // 3) Configuration. API Keys now also fronts the former "Telephony" screen —
  // the telephony status/credentials/scripts/calls live as a section inside the
  // API-Keys view, so matchIds keeps this row highlighted when a "geh zur
  // Telefonie" voice command lands on the "telephony" id. Settings likewise
  // fronts the former "Taskbar" + "Languages" sections (overlay/dictation
  // controls live in OverlayTaskbarGroup, language selectors in LanguagesGroup).
  [
    {
      id: "apikeys",
      labelKey: "nav.apikeys",
      icon: KeyRound,
      matchIds: ["apikeys", "telephony", "telephony-setup"],
    },
    {
      id: "settings",
      labelKey: "nav.settings",
      icon: Settings,
      matchIds: ["settings", "taskbar", "languages"],
    },
    // The voice section — dictation, the custom vocabulary, the keys that start
    // it, the dictation language and the speech-to-text providers — behind one
    // tab switch. "dictation" is the default landing; matchIds keeps the row
    // highlighted for any of the fronted tabs. The label carries the {name}
    // token, so the row reads as the user's own wake-word brand.
    {
      id: "dictation",
      labelKey: "nav.voice",
      icon: Mic,
      matchIds: [
        "dictation",
        "dictionary",
        "voice-shortcuts",
        "voice-language",
        "voice-api-keys",
      ],
      // Name-FREE on purpose: the fallback is rendered verbatim when the key is
      // missing from a locale, and it is NOT interpolated.
      fallbackLabel: "Voice",
    },
    { id: "outputs", labelKey: "nav.outputs", icon: FolderOpen },
    // The assistant's character. Sits with the configuration group because it
    // changes how the assistant behaves everywhere, not what any one page holds.
    {
      id: "modes",
      labelKey: "nav.modes",
      icon: Drama,
      fallbackLabel: "Modes",
    },
    // Appearance. Sits with the configuration group rather than with the
    // content views: it changes how the app looks, not what it holds.
    {
      id: "wallpaper",
      labelKey: "nav.wallpaper",
      icon: ImageIcon,
      fallbackLabel: "Wallpaper",
    },
  ],
  // 4) Social links + in-app feedback.
  [
    { id: "socials", labelKey: "nav.socials", icon: Share2 },
    { id: "feedback", labelKey: "nav.feedback", icon: MessageSquareWarning },
  ],
  // 5) The Agentic IDE — its own bottom group on purpose. It is not one more
  // page among the tools above: opening it puts real coding agents to work in a
  // folder and can narrow the assistant to that workspace, so it sits apart
  // with its own divider rather than blending into the list.
  [
    {
      id: "agentic-ide",
      labelKey: "nav.agentic_ide",
      icon: MessagesSquare,
      fallbackLabel: "Agentic IDE",
      // The classic grid is the same destination as far as the row is
      // concerned: someone who stepped back into it should still see where
      // they are in the navigation.
      matchIds: ["agentic-ide", "chat-workspace", "agentic-ide-classic"],
      beta: true,
    },
  ],
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

/** Width the sidebar was designed at, and the one a double-click restores. */
export const SIDEBAR_DEFAULT_WIDTH = 280;

/**
 * Narrowest the sidebar goes: the nav icons, and nothing else.
 *
 * The seam used to stop at 200 px, which is wide enough to still read every
 * label — so in the Agentic IDE, where a workspace of a dozen terminals wants
 * every pixel, a fifth of the window stayed spent on a list nobody was reading.
 * The rail keeps navigation one click away (the icons are still there, each
 * with its label on hover) while giving that space back to the panes.
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
  const transcription = useEventStore((s) => s.transcription);
  const transcriptionFinal = useEventStore((s) => s.transcriptionFinal);
  // Shared readiness derivation (same source the banner + chat empty-state use).
  const { connected, voiceWarming, bootWarming, warming } = useVoiceReadiness();
  const brainProvider = useEventStore((s) => s.brainProvider);
  const brainModel = useEventStore((s) => s.brainModel);
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
  // line needs its connecting phase. A RUNNING realtime session's live
  // provider/model outrank the configured pick (a mid-call cross-family
  // fallback must be visible, AP-22); when idle the resolved provider + its
  // pinned/default model are shown.
  const voiceMode = useVoiceMode();

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

  const providerLabel = useMemo(() => prettyProviderName(brainProvider), [brainProvider]);

  const realtimeFooter = voiceMode.mode === "realtime";
  const liveRealtimeSession =
    realtimeFooter &&
    voiceMode.sessionActive &&
    voiceMode.activeSessionMode === "realtime";
  const footerLabel = realtimeFooter
    ? t("sidebar.realtime_label")
    : t("sidebar.brain_label");
  const footerTooltip = realtimeFooter
    ? t("sidebar.realtime_tooltip")
    : t("sidebar.brain_tooltip");
  const footerProvider = realtimeFooter
    ? liveRealtimeSession && voiceMode.activeSessionProvider
      ? prettyProviderName(voiceMode.activeSessionProvider)
      : voiceMode.activeProviderLabel ??
        prettyProviderName(voiceMode.activeProvider ?? "unknown")
    : providerLabel;
  const footerModel = realtimeFooter
    ? liveRealtimeSession && voiceMode.activeSessionModel
      ? voiceMode.activeSessionModel
      : voiceMode.activeModel ?? ""
    : brainModel;

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
    <aside
      style={{ width: railed ? SIDEBAR_RAIL_WIDTH : width }}
      data-testid="sidebar"
      data-railed={railed ? "true" : "false"}
      className="jarvis-nav-surface relative isolate flex h-full shrink-0 flex-col"
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
              "flex shrink-0 items-center justify-center",
              railed ? "h-9 w-9" : "h-11 w-11",
            )}
          >
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
              <span className="font-display text-sm font-semibold tracking-tight">
                {assistantName}
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
        {!railed && (
          <>
            <div className="jarvis-input-surface mt-3 min-h-[20px] rounded-md px-2 py-1.5 text-xs text-muted-foreground">
              {transcription ? (
                <span
                  className={cn(
                    "font-mono",
                    !transcriptionFinal && "italic text-foreground/70",
                  )}
                >
                  {tailTruncate(transcription, 48)}
                  {!transcriptionFinal && (
                    <span
                      className="ml-px animate-pulse motion-reduce:animate-none"
                      aria-hidden="true"
                    >
                      ▍
                    </span>
                  )}
                </span>
              ) : (
                <span className="text-muted-foreground/50">
                  {t("sidebar.wake_hint")}
                </span>
              )}
            </div>
            <BrowserRealtimeControl />
          </>
        )}
      </div>

      <nav
        className={cn(
          "flex-1 overflow-y-auto scrollbar-jarvis",
          railed ? "px-1.5 py-2" : "p-2",
        )}
      >
        {NAV_GROUPS.map((group, groupIndex) => (
          <ul
            key={groupIndex}
            className={cn(
              "space-y-0.5",
              // Thin divider + breathing room above every group after the first.
              groupIndex > 0 && "mt-2 border-t border-border/40 pt-2",
            )}
          >
            {group.map((item) => (
              <NavRow
                key={item.id}
                item={item}
                railed={railed}
                label={resolveNavLabel(t, item)}
                active={item.matchIds ? item.matchIds.includes(active) : item.id === active}
                badge={item.id === "agents" ? agentsCount : undefined}
                betaLabel={item.beta ? t("nav.agentic_ide_beta") : undefined}
                alert={item.id === "apikeys" ? apikeysHasError : false}
                alertTitle={t("sidebar.apikeys_alert")}
                warn={item.id === "skills" ? pluginsNeedReconnect : false}
                warnTitle={pluginWarnTitle}
                // A plugin problem sends the "Skills & Tools" row straight into
                // the Plugins tab (where the banner + jump button are), so one
                // click lands on the fix instead of the default Skills tab.
                onClick={() =>
                  setActive(
                    item.id === "skills" && pluginsNeedReconnect ? "plugins" : item.id,
                  )
                }
              />
            ))}
          </ul>
        ))}
      </nav>

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
  railed = false,
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
  /** Icon-only row — the sidebar has been dragged down to its rail. */
  railed?: boolean;
  onClick: () => void;
}) {
  const Icon = item.icon;
  // On the rail the label is gone from the screen, so it has to survive
  // somewhere: as the hover text and as the accessible name. Without both, the
  // narrow sidebar would be a column of unlabelled glyphs. The beta pill has no
  // room on the rail either, so it folds into the same hover text.
  const hint = alert ? alertTitle : warn ? warnTitle : undefined;
  const fullLabel = betaLabel ? `${label} (${betaLabel})` : label;
  return (
    <li>
      <button
        type="button"
        data-testid={`nav-row-${item.id}`}
        onClick={onClick}
        aria-label={railed ? fullLabel : undefined}
        title={railed ? (hint ? `${fullLabel} — ${hint}` : fullLabel) : hint}
        className={cn(
          "group relative flex w-full items-center rounded-lg text-sm transition-all",
          railed ? "justify-center px-0 py-2" : "gap-3 px-3 py-2",
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
        {!railed && (
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
        )}
        {/* On the rail the status dots ride ON the icon rather than after the
            label there is no room for — the signal is the point, not the row. */}
        {alert && (
          <span
            data-testid={`nav-alert-${item.id}`}
            role="status"
            aria-label={alertTitle}
            className={cn(
              "h-2 w-2 shrink-0 rounded-full bg-destructive ring-2 ring-background",
              railed && "absolute right-1.5 top-1.5",
            )}
          />
        )}
        {!alert && warn && (
          <span
            data-testid={`nav-warn-${item.id}`}
            role="status"
            aria-label={warnTitle}
            className={cn(
              "h-2 w-2 shrink-0 rounded-full bg-amber-500 ring-2 ring-background",
              railed && "absolute right-1.5 top-1.5",
            )}
          />
        )}
        {badge !== undefined && badge > 0 && !railed && (
          <span className="rounded-full bg-primary/20 px-1.5 py-0.5 text-[10px] font-semibold text-primary">
            {badge}
          </span>
        )}
      </button>
    </li>
  );
}

/** Keep the NEWEST words of a growing transcript visible.
 *
 * Head truncation froze the line on the first 48 characters the moment a
 * sentence outgrew the box: the user kept talking and the display stopped
 * moving, which reads as "it stopped hearing me". Dropping the oldest end
 * instead makes the line scroll along with the speech. */
function tailTruncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return "…" + s.slice(s.length - (n - 1));
}

function prettyProviderName(id: string): string {
  const map: Record<string, string> = {
    "claude-api": "Claude (API)",
    "openrouter": "OpenRouter",
    "ollama-local": "Ollama (local)",
    "ollama-cloud": "Ollama (Cloud)",
    "gemini": "Gemini",
    "openai": "OpenAI",
    "codex": "Codex",
    "mock": "Mock-Brain",
    // Realtime tier — used by the footer card in realtime voice mode when the
    // backend's pretty label is unavailable (cosmetic fallback only).
    "openai-realtime": "OpenAI Realtime",
    "gemini-live": "Gemini Live",
    "local-realtime": "Self-hosted realtime",
    "unknown": "—",
  };
  return map[id] ?? id;
}
