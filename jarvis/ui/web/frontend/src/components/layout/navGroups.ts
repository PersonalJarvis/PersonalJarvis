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
import { KeyRound, type LucideIcon } from "lucide-react";
import {
  AgentsIcon,
  ArtifactsIcon,
  BoardIcon,
  CaptureIcon,
  ChatIcon,
  CodeIcon,
  ComputersIcon,
  ContactsIcon,
  DocsIcon,
  ExtensionsIcon,
  FeedbackIcon,
  AssistantIcon,
  MarketplaceIcon,
  MicrophoneIcon,
  PermissionsIcon,
  PetsIcon,
  ProfileIcon,
  SettingsIcon,
  ShortcutsIcon,
  SocialsIcon,
  SpeechIcon,
  SpendIcon,
  TerminalIcon,
  WikiIcon,
} from "@/components/icons/sectionIcons";
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
 * The front page is ONE section ("chats"): a chat with a voice mode inside it
 * (2026-10-01; before that, a `Voice | Chat` switch picked between two faces
 * and this row was renamed after the face). The row is "Chat" whatever mode
 * the chat is in — voice mode is a state of the chat, not another place.
 * Every other row passes through unchanged. Pure, so the sidebar and the
 * rail present the row identically; the surface argument stays so callers
 * need not change.
 */
export function presentNavItem(item: NavItem, _surface?: HomeSurface): NavItem {
  if (item.id !== "chats") return item;
  return { ...item, labelKey: "sidebar.surface_chat", icon: ChatIcon, fallbackLabel: "Chat" };
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
  [{ id: "chats", labelKey: "nav.chats", icon: ChatIcon }],
  // 1) Workspace — what the user builds with and reads back.
  [
    { id: "agents", labelKey: "nav.agents", icon: AgentsIcon },
    // The compact catalog opens on Plugins; direct section navigation selects
    // its corresponding tab and keeps this shared row highlighted.
    {
      id: "plugins",
      labelKey: "nav.extensions",
      icon: ExtensionsIcon,
      matchIds: ["skills", "plugins", "mcps"],
    },
    // The marketplace fills those lists: a plugin or a skill published there
    // ends up in one of them once installed.
    {
      id: "marketplace",
      labelKey: "nav.marketplace",
      icon: MarketplaceIcon,
      fallbackLabel: "Marketplace",
    },
    // Artifacts — everything a run produced. The id stays "visualization"
    // because it crosses the navigate parity test, the detachable-view
    // registry and deep links.
    {
      id: "visualization",
      labelKey: "nav.visualization",
      icon: ArtifactsIcon,
      fallbackLabel: "Artifacts",
    },
    { id: "board", labelKey: "nav.board", icon: BoardIcon },
    { id: "memory", labelKey: "nav.wiki", icon: WikiIcon },
    { id: "docs", labelKey: "nav.docs", icon: DocsIcon },
  ],
  // 2) Tools — the instruments: transcription, the run inspector, the CLIs
  // and the Agentic IDE (which puts real coding agents to work in a folder,
  // so its row says "Beta" up front).
  [
    { id: "sessions", labelKey: "nav.sessions", icon: MicrophoneIcon },
    // CLIs — the CLIs list + the CLI Test Hub behind one tab switch (CLIs first).
    { id: "clis", labelKey: "nav.clis_hub", icon: TerminalIcon, matchIds: ["clis", "cli-test-hub"] },
    {
      id: "agentic-ide",
      labelKey: "nav.agentic_ide",
      icon: CodeIcon,
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
    { id: "profile", labelKey: "nav.profile", icon: ProfileIcon },
    {
      id: "agent-instructions",
      labelKey: "nav.agent_instructions",
      icon: AssistantIcon,
      fallbackLabel: "Assistant",
    },
    { id: "contacts", labelKey: "nav.contacts", icon: ContactsIcon },
    // Spend & Tokens — every token the app spent, priced per provider, model
    // and role. It reports, it does not configure.
    { id: "costs", labelKey: "nav.costs", icon: SpendIcon, fallbackLabel: "Spend" },
    { id: "socials", labelKey: "nav.socials", icon: SocialsIcon },
  ],
  // 4) System. API Keys also fronts the former "Telephony" screen — the
  // telephony status/credentials/scripts/calls live as a section inside the
  // API-Keys view, so matchIds keeps this row highlighted when a "geh zur
  // Telefonie" voice command lands on the "telephony" id. Settings likewise
  // fronts the former "Taskbar" + "Languages" sections.
  [
    // Computers: the servers and virtual machines the assistant and its agents
    // can work on besides this one (a rented VPS, a hosting-account import, a
    // local VM). A Settings-hub entry, first under System.
    {
      id: "computers",
      labelKey: "nav.computers",
      icon: ComputersIcon,
      fallbackLabel: "Computers",
    },
    {
      id: "apikeys",
      labelKey: "nav.apikeys",
      icon: KeyRound,
      matchIds: ["apikeys", "telephony", "telephony-setup"],
    },
    {
      id: "settings",
      labelKey: "nav.settings",
      icon: SettingsIcon,
      matchIds: ["settings", "taskbar", "languages"],
    },
    // The voice section — dictation, the custom vocabulary, the keys that start
    // it, the dictation language and the speech-to-text providers — behind one
    // tab switch. "dictation" is the default landing.
    {
      id: "dictation",
      labelKey: "nav.voice",
      icon: SpeechIcon,
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
  ],
];

/**
 * Feedback lives in the sidebar's footer beside the brain card, not in a
 * group: it is a door out of the product, not a section of it. Kept as a
 * `NavItem` so the deck and the rail can still list it from one definition.
 */
export const NAV_FOOTER_ITEMS: NavItem[] = [
  { id: "feedback", labelKey: "nav.feedback", icon: FeedbackIcon },
];

/**
 * Rows that exist only inside the Settings hub's own navigation, not in the
 * app sidebar: settings for one feature, reached through Settings the way the
 * Appshots page is.
 */
export const SETTINGS_HUB_ONLY_ITEMS: NavItem[] = [
  {
    id: "shortcuts",
    labelKey: "nav.shortcuts",
    icon: ShortcutsIcon,
    fallbackLabel: "Keyboard shortcuts",
  },
  { id: "appshots", labelKey: "nav.appshots", icon: CaptureIcon, fallbackLabel: "Appshots" },
  { id: "pets", labelKey: "nav.pets", icon: PetsIcon, fallbackLabel: "My Pets" },
  {
    id: "jarvis-actions",
    labelKey: "nav.jarvis_actions",
    icon: PermissionsIcon,
    fallbackLabel: "Jarvis actions",
  },
];

/**
 * Every section id rendered inside the Settings hub (`SettingsHubView`).
 *
 * The ids keep their meaning — deep links, voice commands, the deck and the
 * detached-window registry all still name them — only the STAGE changed: the
 * router mounts the hub for any of them and the hub selects the matching tab.
 * Imported by the sidebar (which highlights its profile entry while one of
 * these is active and keeps them out of "Show more") and by the hub itself,
 * so the set is named exactly once.
 */
export const SETTINGS_HUB_IDS: readonly SectionId[] = [
  "settings",
  "taskbar",
  "languages",
  "profile",
  "agent-instructions",
  "contacts",
  "socials",
  "apikeys",
  "telephony",
  "telephony-setup",
  "local-models",
  "computers",
  "appshots",
  "shortcuts",
  "pets",
  "jarvis-actions",
  "costs",
  "feedback",
];
