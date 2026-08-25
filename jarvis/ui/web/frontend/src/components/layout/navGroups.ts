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


// Sidebar nav, clustered into logical groups separated by a thin divider:
//   1) daily tools   2) content & data   3) configuration   4) social links.
// The render walks the groups in order and draws a separator between them, so
// the order below IS the on-screen order.
//
// Exported because the mission deck shows every section at once and jumps to
// them. A second hand-written list there would be the classic drift trap
// (AP-4): a section added here would silently never appear on the deck.
export const NAV_GROUPS: NavItem[][] = [
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
    // The marketplace. Sits right under Skills & Tools because that is what
    // fills those lists: a plugin, a skill or a wallpaper published there ends
    // up in one of them once installed. Same Store icon as the badge that
    // marks an installed entry, so the mark and its origin read as one thing.
    {
      id: "marketplace",
      labelKey: "nav.marketplace",
      icon: Store,
      fallbackLabel: "Marketplace",
    },
  ],
  // 2) Content & data — things the user reads, edits, or browses.
  [
    // Automations — the recurring agent tasks and their catalogue. The id stays
    // "tasks" (navigate parity, deep links); only the label and glyph changed.
    { id: "tasks", labelKey: "nav.tasks", icon: Workflow, fallbackLabel: "Automations" },
    { id: "sessions", labelKey: "nav.sessions", icon: Mic },
    { id: "run_inspector", labelKey: "nav.run_inspector", icon: Gauge },
    // Spend & Tokens — every token the app spent, priced per provider,
    // model and role. Sits with the other things the user reads back
    // rather than with the settings: it reports, it does not configure.
    { id: "costs", labelKey: "nav.costs", icon: Wallet, fallbackLabel: "Spend" },
    // Artifacts — everything a run produced: the pages and pictures on a
    // full-size stage, and every other run (its files, status and controls)
    // in the same rail. The Outputs section that used to list the runs
    // folded into this one (2026-08-23); the id stays "visualization" because
    // it crosses the navigate parity test, the detachable-view registry and
    // deep links.
    {
      id: "visualization",
      labelKey: "nav.visualization",
      icon: Shapes,
      fallbackLabel: "Artifacts",
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
