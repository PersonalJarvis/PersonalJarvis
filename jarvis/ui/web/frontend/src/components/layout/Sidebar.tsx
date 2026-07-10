import {
  MessageSquare,
  Users,
  Boxes,
  BookA,
  BookOpen,
  KeyRound,
  Settings,
  UserCircle2,
  ListTodo,
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
  Loader2,
  type LucideIcon,
  ChevronRight,
} from "lucide-react";
import { useEventStore, type SectionId } from "@/store/events";
import { useVoiceReadiness } from "@/hooks/useVoiceReadiness";
import { useSectionHealth } from "@/hooks/useProviders";
import { usePluginAttention } from "@/hooks/usePluginAttention";
import { cn } from "@/lib/utils";
import { useMemo } from "react";
import { useT } from "@/i18n";

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
}

// Resolve a nav row's label, preferring the active-locale translation and
// falling back to the English `fallbackLabel` when the key is not yet present
// (the i18n resolver returns the key itself on a miss).
function resolveNavLabel(t: (key: string) => string, item: NavItem): string {
  const resolved = t(item.labelKey);
  return resolved === item.labelKey && item.fallbackLabel ? item.fallbackLabel : resolved;
}

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
    {
      id: "dictionary",
      labelKey: "nav.dictionary",
      icon: BookA,
      fallbackLabel: "Dictionary",
    },
    { id: "outputs", labelKey: "nav.outputs", icon: FolderOpen },
  ],
  // 4) Social links + in-app feedback — pinned to the bottom group.
  [
    { id: "socials", labelKey: "nav.socials", icon: Share2 },
    { id: "feedback", labelKey: "nav.feedback", icon: MessageSquareWarning },
  ],
];

const VOICE_STATE_STYLE: Record<string, { dot: string; pulse: boolean }> = {
  idle: { dot: "bg-muted-foreground/50", pulse: false },
  listening: { dot: "bg-emerald-400", pulse: true },
  thinking: { dot: "bg-primary", pulse: true },
  speaking: { dot: "bg-primary", pulse: true },
  error: { dot: "bg-destructive", pulse: false },
};

export function Sidebar() {
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

  // The window connects in ~1s but the voice feature warms up ~20s in the
  // background. During that gap show a "Voice starting…" spinner instead of the
  // normal idle "Ready" dot (which would falsely imply the mic already works).
  // Disconnected outranks warmup — "Offline" is the honest state with no socket.
  // voiceWarming / bootWarming / warming come from the shared useVoiceReadiness
  // hook so the sidebar dot, the banner and the chat empty-state never disagree.
  const showSpinner = warming;
  const vs = VOICE_STATE_STYLE[voiceState] ?? VOICE_STATE_STYLE.idle;
  const voiceLabel = !connected
    ? bootWarming
      ? t("voice_state.booting")
      : t("voice_state.offline")
    : voiceWarming
      ? t("voice_state.starting")
      : t(`voice_state.${voiceState}`);

  const providerLabel = useMemo(() => prettyProviderName(brainProvider), [brainProvider]);

  return (
    <aside className="flex h-full w-[280px] shrink-0 flex-col border-r border-border bg-card/40 backdrop-blur">
      <div className="border-b border-border px-4 py-4">
        <div className="flex items-center gap-3">
          {/* The original Personal Jarvis logo — the ghost mascot. A snapshot
              had swapped the header avatar for a bar glyph / gold-spark mark;
              this is the canonical brand identity (jarvis-gigi). */}
          <span
            data-testid="sidebar-style-avatar"
            data-variant="logo"
            className="flex h-11 w-11 shrink-0 items-center justify-center"
          >
            <img
              src="/jarvis-logo.png"
              width={40}
              height={40}
              alt="Personal Jarvis"
              className="shrink-0"
            />
          </span>
          <div className="flex min-w-0 flex-1 flex-col">
            <span className="font-display text-sm font-semibold tracking-tight">
              {assistantName}
            </span>
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
              {voiceLabel}
            </span>
          </div>
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
        </div>
        <div className="mt-3 min-h-[20px] rounded-md bg-background/40 px-2 py-1.5 text-xs text-muted-foreground">
          {transcription ? (
            <span className={cn("font-mono", !transcriptionFinal && "italic")}>
              {truncate(transcription, 48)}
            </span>
          ) : (
            <span className="text-muted-foreground/50">{t("sidebar.wake_hint")}</span>
          )}
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto scrollbar-jarvis p-2">
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
                label={resolveNavLabel(t, item)}
                active={item.matchIds ? item.matchIds.includes(active) : item.id === active}
                badge={item.id === "agents" ? agentsCount : undefined}
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

      <div className="border-t border-border p-3">
        <button
          type="button"
          onClick={() => setActive("apikeys")}
          className="group flex w-full items-center gap-3 rounded-lg border border-border bg-background/40 px-3 py-2 text-left transition-colors hover:border-primary/40 hover:bg-background/60"
          title={t("sidebar.brain_tooltip")}
        >
          <div className="h-2 w-2 rounded-full bg-primary shadow-[0_0_8px_rgba(255,214,10,0.7)]" />
          <div className="flex-1 min-w-0">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
              {t("sidebar.brain_label")}
            </div>
            <div className="text-xs font-medium truncate">{providerLabel}</div>
            {/* The configured model id (e.g. "claude-opus-4-8") — the user asked
                to see WHICH model is actually in use, not just the provider. */}
            {brainModel && (
              <div
                className="text-[10px] text-muted-foreground/70 truncate"
                title={brainModel}
                data-testid="sidebar-brain-model"
              >
                {brainModel}
              </div>
            )}
          </div>
          <ChevronRight className="h-4 w-4 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-primary" />
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
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        title={alert ? alertTitle : warn ? warnTitle : undefined}
        className={cn(
          "group relative flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm transition-all",
          "hover:bg-background/60",
          active
            ? "bg-background text-foreground shadow-[inset_2px_0_0_hsl(var(--primary))]"
            : "text-muted-foreground hover:text-foreground",
        )}
      >
        <Icon
          className={cn(
            "h-4 w-4 shrink-0 transition-colors",
            active ? "text-primary" : "text-muted-foreground group-hover:text-foreground",
          )}
        />
        <span className="flex-1 text-left">{label}</span>
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

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + "…";
}

function prettyProviderName(id: string): string {
  const map: Record<string, string> = {
    "claude-api": "Claude (API)",
    "openrouter": "OpenRouter",
    "ollama-local": "Ollama (lokal)",
    "ollama-cloud": "Ollama (Cloud)",
    "gemini": "Gemini",
    "openai": "OpenAI",
    "codex": "Codex",
    "mock": "Mock-Brain",
    "unknown": "—",
  };
  return map[id] ?? id;
}
