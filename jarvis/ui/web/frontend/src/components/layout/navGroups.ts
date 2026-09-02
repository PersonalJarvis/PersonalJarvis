/**
 * The app's section list — the ONE source of truth for what sections exist.
 *
 * Extracted from `Sidebar.tsx` so the mission deck can show every section at
 * once without pulling the sidebar's own dependency tree (voice hooks, the
 * realtime control, provider health) into the entry chunk with it. The deck
 * ships in that chunk, and `MainView` keeps it deliberately small.
 *
 * A second hand-written list anywhere would be the classic drift trap (AP-4):
 * a section added here would silently never appear on the deck.
 */
import {
  BookOpen,
  Boxes,
  Contact,
  Gauge,
  KeyRound,
  MessageSquare,
  MessageSquareWarning,
  MessagesSquare,
  Mic,
  Notebook,
  ScrollText,
  Settings,
  Shapes,
  Share2,
  Sparkles,
  Store,
  Terminal,
  UserCircle2,
  Users,
  Wallet,
  Workflow,
  Image as ImageIcon,
  type LucideIcon,
} from "lucide-react";
import { OllamaIcon } from "@/components/icons/OllamaIcon";
import type { SectionId } from "@/store/events";
import type { HomeSurface } from "@/lib/homeSurface";

// Resolve a nav row's label, preferring the active-locale translation and
// falling back to the English `fallbackLabel` when the key is not yet present
// (the i18n resolver returns the key itself on a miss).
export function resolveNavLabel(t: (key: string) => string, item: NavItem): string {
  const resolved = t(item.labelKey);
  return resolved === item.labelKey && item.fallbackLabel ? item.fallbackLabel : resolved;
}

/**
 * The front page is ONE section ("chats") with two faces — the voice stage
 * and the typed chat — picked by the `Voice | Chat` switch at the top of the
 * sidebar. Its nav row says which face it currently is: Mic + "Voice" or
 * bubble + "Chat", the same two words the switch uses. A fixed "Chats" label
 * under a switch that says "Voice" read as a contradiction (maintainer,
 * 2026-08-23). Every other row passes through unchanged. Pure, so the
 * sidebar and the rail present the row identically.
 */
export function presentNavItem(item: NavItem, surface: HomeSurface): NavItem {
  if (item.id !== "chats") return item;
  return surface === "voice"
    ? { ...item, labelKey: "sidebar.surface_voice", icon: Mic, fallbackLabel: "Voice" }
    : { ...item, labelKey: "sidebar.surface_chat", icon: MessageSquare, fallbackLabel: "Chat" };
}

export interface NavItem {
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


/**
 * The sidebar's group labels, one per entry of `NAV_GROUPS`, by position.
 *
 * The first group (the front page) carries no label and is never collapsed.
 * Every other group is collapsible; `defaultOpen` is what a fresh install
 * shows, and the user's choice is remembered per group in localStorage.
 * "Tools" and "You" start folded so the column fits 1080 px without a
 * scrollbar with the defaults.
 */
export interface NavGroupMeta {
  id: string;
  labelKey?: string;
  fallbackLabel?: string;
  defaultOpen: boolean;
}

export const NAV_GROUP_META: readonly NavGroupMeta[] = [
  { id: "home", defaultOpen: true },
  {
    id: "workspace",
    labelKey: "nav.group_workspace",
    fallbackLabel: "Workspace",
    defaultOpen: true,
  },
  { id: "tools", labelKey: "nav.group_tools", fallbackLabel: "Tools", defaultOpen: false },
  { id: "you", labelKey: "nav.group_you", fallbackLabel: "You", defaultOpen: false },
  { id: "system", labelKey: "nav.group_system", fallbackLabel: "System", defaultOpen: true },
];

// Sidebar nav in four labelled groups (v4, 2026-09-02):
//   Workspace · Tools · You · System — preceded by the front page's own row.
// The render walks the groups in order, so the order below IS the on-screen
// order. Every section id is unchanged, so routing, deep links and the
// navigate parity tests do not move.
//
// Exported because the mission deck shows every section at once and jumps to
// them. A second hand-written list there would be the classic drift trap
// (AP-4): a section added here would silently never appear on the deck.
export const NAV_GROUPS: NavItem[][] = [
  // 0) The front page — Voice or Chat, named after the face the switch picked.
  [{ id: "chats", labelKey: "nav.chats", icon: MessageSquare }],
  // 1) Workspace — what the user builds with and reads back.
  [
    { id: "agents", labelKey: "nav.agents", icon: Users },
    // Skills & Plugins — Skills + Plugins + MCPs behind one tab switch. The id
    // "skills" is the default landing (Skills tab); matchIds keeps the row
    // highlighted for any of the fronted sections.
    {
      id: "skills",
      labelKey: "nav.extensions",
      icon: Boxes,
      matchIds: ["skills", "plugins", "mcps"],
    },
    // The marketplace fills those lists: a plugin, a skill or a wallpaper
    // published there ends up in one of them once installed.
    {
      id: "marketplace",
      labelKey: "nav.marketplace",
      icon: Store,
      fallbackLabel: "Marketplace",
    },
    // Automations — the recurring agent tasks and their catalogue. The id stays
    // "tasks" (navigate parity, deep links); only the label and glyph changed.
    { id: "tasks", labelKey: "nav.tasks", icon: Workflow, fallbackLabel: "Automations" },
    // Artifacts — everything a run produced. The id stays "visualization"
    // because it crosses the navigate parity test, the detachable-view
    // registry and deep links.
    {
      id: "visualization",
      labelKey: "nav.visualization",
      icon: Shapes,
      fallbackLabel: "Artifacts",
    },
    { id: "board", labelKey: "nav.board", icon: Sparkles },
    { id: "memory", labelKey: "nav.wiki", icon: Notebook },
    { id: "docs", labelKey: "nav.docs", icon: BookOpen },
  ],
  // 2) Tools — the instruments: transcription, the run inspector, the CLIs
  // and the Agentic IDE (which puts real coding agents to work in a folder,
  // so its row says "Beta" up front).
  [
    { id: "sessions", labelKey: "nav.sessions", icon: Mic },
    { id: "run_inspector", labelKey: "nav.run_inspector", icon: Gauge },
    // CLIs — the CLIs list + the CLI Test Hub behind one tab switch (CLIs first).
    { id: "clis", labelKey: "nav.clis_hub", icon: Terminal, matchIds: ["clis", "cli-test-hub"] },
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
  // 3) You — what the assistant knows about the user, and the user's own
  // ledgers.
  [
    { id: "profile", labelKey: "nav.profile", icon: UserCircle2 },
    {
      id: "agent-instructions",
      labelKey: "nav.agent_instructions",
      icon: ScrollText,
      fallbackLabel: "Instructions",
    },
    { id: "contacts", labelKey: "nav.contacts", icon: Contact },
    // Spend & Tokens — every token the app spent, priced per provider, model
    // and role. It reports, it does not configure.
    { id: "costs", labelKey: "nav.costs", icon: Wallet, fallbackLabel: "Spend" },
    { id: "socials", labelKey: "nav.socials", icon: Share2 },
  ],
  // 4) System. API Keys also fronts the former "Telephony" screen — the
  // telephony status/credentials/scripts/calls live as a section inside the
  // API-Keys view, so matchIds keeps this row highlighted when a "geh zur
  // Telefonie" voice command lands on the "telephony" id. Settings likewise
  // fronts the former "Taskbar" + "Languages" sections.
  [
    {
      id: "apikeys",
      labelKey: "nav.apikeys",
      icon: KeyRound,
      matchIds: ["apikeys", "telephony", "telephony-setup"],
    },
    // Local models sit directly under API Keys: the same "which brain" question,
    // answered for the machine itself instead of a hosted account.
    {
      id: "local-models",
      labelKey: "nav.local_models",
      icon: OllamaIcon,
      fallbackLabel: "Local models",
    },
    {
      id: "settings",
      labelKey: "nav.settings",
      icon: Settings,
      matchIds: ["settings", "taskbar", "languages"],
    },
    // The voice section — dictation, the custom vocabulary, the keys that start
    // it, the dictation language and the speech-to-text providers — behind one
    // tab switch. "dictation" is the default landing.
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
    {
      id: "wallpaper",
      labelKey: "nav.wallpaper",
      icon: ImageIcon,
      fallbackLabel: "Wallpaper",
    },
  ],
];

/**
 * Feedback lives in the sidebar's footer beside the brain card, not in a
 * group: it is a door out of the product, not a section of it. Kept as a
 * `NavItem` so the deck and the rail can still list it from one definition.
 */
export const NAV_FOOTER_ITEMS: NavItem[] = [
  { id: "feedback", labelKey: "nav.feedback", icon: MessageSquareWarning },
];
