import {
  MessageSquare,
  Users,
  Boxes,
  BookOpen,
  KeyRound,
  Settings,
  Activity,
  UserCircle2,
  ListTodo,
  FolderOpen,
  Notebook,
  Sparkles,
  Mic,
  Terminal,
  Share2,
  Contact,
  Loader2,
  type LucideIcon,
  ChevronRight,
} from "lucide-react";
import { useEventStore, type SectionId } from "@/store/events";
import { cn } from "@/lib/utils";
import { useMemo } from "react";
import { MascotGigi } from "@/components/MascotGigi";
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
    { id: "board", labelKey: "nav.board", icon: Sparkles },
    { id: "memory", labelKey: "nav.wiki", icon: Notebook },
    { id: "contacts", labelKey: "nav.contacts", icon: Contact },
    { id: "profile", labelKey: "nav.profile", icon: UserCircle2 },
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
    { id: "debug", labelKey: "nav.debug", icon: Activity },
    { id: "outputs", labelKey: "nav.outputs", icon: FolderOpen },
  ],
  // 4) Social links — pinned to the bottom group.
  [{ id: "socials", labelKey: "nav.socials", icon: Share2 }],
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
  const transcription = useEventStore((s) => s.transcription);
  const transcriptionFinal = useEventStore((s) => s.transcriptionFinal);
  const connected = useEventStore((s) => s.connected);
  const voiceReady = useEventStore((s) => s.voiceReady);
  const brainProvider = useEventStore((s) => s.brainProvider);
  const agentsCount = useEventStore((s) =>
    s.events.filter((e) => e.name === "AgentStateChange").length > 0 ? undefined : 0,
  );

  // The window connects in ~1s but the voice feature warms up ~20s in the
  // background. During that gap show a "Voice starting…" spinner instead of the
  // normal idle "Ready" dot (which would falsely imply the mic already works).
  // Disconnected outranks warmup — "Offline" is the honest state with no socket.
  const voiceWarming = connected && !voiceReady;
  const vs = VOICE_STATE_STYLE[voiceState] ?? VOICE_STATE_STYLE.idle;
  const voiceLabel = !connected
    ? t("voice_state.offline")
    : voiceWarming
      ? t("voice_state.starting")
      : t(`voice_state.${voiceState}`);

  const providerLabel = useMemo(() => prettyProviderName(brainProvider), [brainProvider]);

  return (
    <aside className="flex h-full w-[280px] shrink-0 flex-col border-r border-border bg-card/40 backdrop-blur">
      <div className="border-b border-border px-4 py-4">
        <div className="flex items-center gap-3">
          {/* enableComments={false}: the mascot's floating speech-bubble is
              anchored beside a free-standing mascot, but here the mascot hugs
              the window edge — the listening bubble slid off-screen and only
              its yellow border + glow bled back in (the spurious "yellow
              frame"). The sidebar already surfaces voice status + the live
              transcript in its own boxes below, so the bubble is redundant. */}
          <MascotGigi size={44} enableComments={false} />
          <div className="flex min-w-0 flex-1 flex-col">
            <span className="font-display text-sm font-semibold tracking-tight">
              Jarvis
            </span>
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
              {voiceLabel}
            </span>
          </div>
          {voiceWarming ? (
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
                label={t(item.labelKey)}
                active={item.matchIds ? item.matchIds.includes(active) : item.id === active}
                badge={item.id === "agents" ? agentsCount : undefined}
                onClick={() => setActive(item.id)}
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
  onClick,
}: {
  item: NavItem;
  label: string;
  active: boolean;
  badge?: number;
  onClick: () => void;
}) {
  const Icon = item.icon;
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
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
    "grok": "Grok",
    "mock": "Mock-Brain",
    "unknown": "—",
  };
  return map[id] ?? id;
}
