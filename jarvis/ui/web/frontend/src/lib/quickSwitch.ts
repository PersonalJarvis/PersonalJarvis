/**
 * The quick switcher's destinations and ranking — the Spotlight-style "type a
 * section's name, press Enter, be there" launcher.
 *
 * Pure on purpose: what the switcher lists and in which order is the whole
 * feature, and keeping it out of the component makes it testable without
 * mounting a dialog.
 *
 * ## Where the destinations come from
 *
 * The sidebar's own definitions (`NAV_GROUPS`, `NAV_FOOTER_ITEMS`,
 * `SETTINGS_HUB_ONLY_ITEMS`) — never a second hand-written section list (AP-4).
 * Rows that merge several sections (Plugins/MCPs/Skills, CLIs/Test Hub, the
 * voice tabs) are split back into their tabs here, because "skills" should land
 * on Skills, not on whatever tab the merged row opens first.
 *
 * ## Why every locale is searched
 *
 * Someone running the English UI still thinks of the German word for settings, and someone on
 * the German UI still types "settings" — the word in their head is not tied to
 * the interface language. So a destination matches its label in every shipped
 * locale, plus a few English synonyms, while the row shows the label of the
 * active language.
 */
import { KeyRound, type LucideIcon } from "lucide-react";
import {
  ChatIcon,
  ConnectorIcon,
  DictionaryIcon,
  LanguageIcon,
  PaneGridIcon,
  PhoneIcon,
  PluginIcon,
  ShortcutsIcon,
  SkillIcon,
  TestFlaskIcon,
  VoiceIcon,
} from "@/components/icons/sectionIcons";
import en from "@/i18n/locales/en.json";
import de from "@/i18n/locales/de.json";
import es from "@/i18n/locales/es.json";
import {
  NAV_FOOTER_ITEMS,
  NAV_GROUPS,
  SETTINGS_HUB_ONLY_ITEMS,
  type NavItem,
} from "@/components/layout/navGroups";
import { SECTION_LABELS, type SectionId } from "@/store/events";
import type { HomeSurface } from "@/lib/homeSurface";

type LocaleTree = Record<string, unknown>;
const LOCALES: readonly LocaleTree[] = [en, de, es];

export interface QuickSwitchEntry {
  /** Stable React key and test id. */
  key: string;
  section: SectionId;
  /** The front page has two faces; picking one also flips the switch. */
  surface?: HomeSurface;
  labelKey: string;
  fallbackLabel: string;
  icon: LucideIcon;
  /** The parent area, shown as the row's second line ("Voice", "Settings"). */
  parentLabelKey?: string;
  /** Extra English words that should find this entry. */
  aliases: readonly string[];
}

/** English synonyms per destination — words people type that no label holds. */
const ALIASES: Partial<Record<string, readonly string[]>> = {
  voice: ["home", "start", "talk", "speak"],
  chat: ["home", "start", "message", "conversation"],
  "agentic-ide": ["ide", "code", "coding", "terminal", "panes", "workspace"],
  "agentic-ide-classic": ["terminal", "grid", "panes"],
  agents: ["society", "world", "team", "routines", "schedule", "automations"],
  settings: ["preferences", "options", "config", "configuration", "general"],
  apikeys: ["keys", "credentials", "tokens", "providers"],
  memory: ["notes", "knowledge", "obsidian"],
  visualization: ["outputs", "results", "images", "pages"],
  costs: ["tokens", "usage", "money", "billing", "budget"],
  sessions: ["transcripts", "recordings", "history"],
  computers: ["servers", "ssh", "vps", "remote", "machines"],
  "agent-instructions": ["assistant", "soul", "soul md", "character", "persona", "instructions", "memory md"],
  marketplace: ["store", "shop", "install"],
  board: ["stats", "dashboard", "activity"],
  docs: ["documentation", "help", "manual"],
  feedback: ["bug", "report", "issue"],
  appshots: ["screenshot", "capture"],
  "jarvis-actions": ["permissions", "allow", "block", "actions"],
  dictation: ["voice", "speech", "microphone", "mic", "whisper"],
  shortcuts: ["hotkeys", "keybinds", "keyboard", "quick switcher"],
  "voice-shortcuts": ["dictation hotkeys", "dictation keys"],
  "voice-language": ["recognition language"],
  "voice-api-keys": ["speech to text", "stt"],
  dictionary: ["vocabulary", "words"],
  telephony: ["phone", "calls", "twilio"],
  languages: ["language", "locale", "translation"],
};

function entry(
  item: NavItem,
  overrides: Partial<QuickSwitchEntry> & { key?: string } = {},
): QuickSwitchEntry {
  const key = overrides.key ?? item.id;
  return {
    key,
    section: item.id,
    labelKey: item.labelKey,
    fallbackLabel: item.fallbackLabel ?? SECTION_LABELS[item.id],
    icon: item.icon,
    aliases: ALIASES[key] ?? [],
    ...overrides,
  };
}

/** A tab inside a merged row: its own label, the row's icon unless given. */
function tab(
  section: SectionId,
  labelKey: string,
  icon: LucideIcon,
  parentLabelKey?: string,
): QuickSwitchEntry {
  return {
    key: section,
    section,
    labelKey,
    fallbackLabel: SECTION_LABELS[section],
    icon,
    parentLabelKey,
    aliases: ALIASES[section] ?? [],
  };
}

/**
 * Merged rows are replaced by their tabs; every other row passes through.
 * Returned as a list in sidebar order so an empty query reads like the nav.
 */
function expand(item: NavItem): QuickSwitchEntry[] {
  switch (item.id) {
    case "chats":
      return [
        entry(item, {
          key: "voice",
          surface: "voice",
          labelKey: "sidebar.surface_voice",
          fallbackLabel: "Voice",
          icon: VoiceIcon,
          aliases: ALIASES.voice,
        }),
        entry(item, {
          key: "chat",
          surface: "chat",
          labelKey: "sidebar.surface_chat",
          fallbackLabel: "Chat",
          icon: ChatIcon,
          aliases: ALIASES.chat,
        }),
      ];
    case "plugins":
      return [
        tab("plugins", "nav.plugins", PluginIcon, "nav.extensions"),
        tab("skills", "nav.skills", SkillIcon, "nav.extensions"),
        tab("mcps", "nav.mcps", ConnectorIcon, "nav.extensions"),
      ];
    case "clis":
      return [
        entry(item, { labelKey: "nav.clis", fallbackLabel: "CLIs" }),
        tab("cli-test-hub", "nav.cli_test_hub", TestFlaskIcon, "nav.clis"),
      ];
    case "agentic-ide":
      return [
        entry(item),
        tab("agentic-ide-classic", "quick_switch.terminal_grid", PaneGridIcon, "nav.agentic_ide"),
      ];
    case "apikeys":
      return [entry(item), tab("telephony", "nav.telephony", PhoneIcon, "nav.apikeys")];
    case "settings":
      return [entry(item), tab("languages", "nav.languages", LanguageIcon, "nav.settings")];
    case "dictation":
      return [
        entry(item),
        tab("dictionary", "nav.dictionary", DictionaryIcon, "nav.voice"),
        tab("voice-shortcuts", "nav.voice_shortcuts", ShortcutsIcon, "nav.voice"),
        tab("voice-language", "nav.voice_language", LanguageIcon, "nav.voice"),
        tab("voice-api-keys", "nav.voice_api_keys", KeyRound, "nav.voice"),
      ];
    default:
      return [entry(item)];
  }
}

/** Every place the switcher can take you, in sidebar order. */
export const QUICK_SWITCH_ENTRIES: readonly QuickSwitchEntry[] = [
  ...NAV_GROUPS.flat(),
  ...SETTINGS_HUB_ONLY_ITEMS,
  ...NAV_FOOTER_ITEMS,
].flatMap(expand);

// ── Ranking ──────────────────────────────────────────────────────────────

/** Lowercase with accents folded, so an accented label matches its plain spelling. */
export function normalizeQuery(value: string): string {
  return value
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/\{name\}/g, "")
    .toLowerCase()
    .trim();
}

function atPath(tree: LocaleTree, path: string): unknown {
  return path
    .split(".")
    .reduce<unknown>(
      (node, part) => (node && typeof node === "object" ? (node as LocaleTree)[part] : undefined),
      tree,
    );
}

/** Every word this entry answers to, normalized, across all locales. */
export function entryTerms(item: QuickSwitchEntry, activeLabel: string): string[] {
  const localized = LOCALES.map((tree) => atPath(tree, item.labelKey)).filter(
    (value): value is string => typeof value === "string",
  );
  const terms = [activeLabel, item.fallbackLabel, SECTION_LABELS[item.section], ...localized, ...item.aliases];
  return [...new Set(terms.map(normalizeQuery).filter(Boolean))];
}

/** Do the query's letters appear in order, each starting a word ("aide" → Agentic IDE)? */
function initialsMatch(term: string, needle: string): boolean {
  const initials = term
    .split(/[\s\-_.&/]+/)
    .filter(Boolean)
    .map((word) => word[0])
    .join("");
  return initials.length > 1 && initials.startsWith(needle);
}

/**
 * How well one term matches the query: 0 means not at all.
 *
 * Exact beats prefix beats word-start beats initials beats substring — the
 * order Spotlight uses, which is what makes the first row the right one often
 * enough that Enter without looking works.
 */
export function scoreTerm(term: string, needle: string): number {
  if (!needle) return 0;
  if (term === needle) return 100;
  if (term.startsWith(needle)) return 80;
  if (term.split(/[\s\-_.&/]+/).some((word) => word.startsWith(needle))) return 60;
  if (initialsMatch(term, needle)) return 50;
  // Mid-word hits only from four letters on: "ide" must not find "providers".
  if (needle.length >= 4 && term.includes(needle)) return 40;
  return 0;
}

/**
 * Up to this many letters a query only matches what a row is CALLED, from its
 * first letter: "a" lists exactly the rows whose name starts with A. Longer
 * queries also reach a row through its other-language names and synonyms
 * ("einst" finds Settings, "ollama" Local models).
 */
export const SHORT_QUERY = 2;

/** Alphabetical, case- and accent-blind, numbers in natural order. */
export function compareLabels(a: string, b: string): number {
  return a.localeCompare(b, undefined, { sensitivity: "base", numeric: true });
}

/**
 * The order every result group shares: rows whose name STARTS with the query
 * first, then the rest; alphabetical within each. So "a" reads like an index
 * (Aa… first, Az… last) and an exact name is still on top — "Voice" before
 * "Voice Shortcuts" — because the shortest matching name sorts first.
 */
export function compareMatches(
  a: { label: string; prefix: boolean },
  b: { label: string; prefix: boolean },
): number {
  if (a.prefix !== b.prefix) return a.prefix ? -1 : 1;
  return compareLabels(a.label, b.label);
}

/** Does this name start with the query (accents and case folded)? */
export function labelStartsWith(label: string, needle: string): boolean {
  return normalizeQuery(label).startsWith(needle);
}

export interface RankedEntry {
  entry: QuickSwitchEntry;
  label: string;
  /** The name starts with the query — these rows lead the group. */
  prefix: boolean;
}

/**
 * The entries that match, in `compareMatches` order. An empty query matches
 * nothing: the switcher shows its field alone until something is typed.
 * Two rows with the same name (the API Keys page and the voice tab of that
 * name) keep the page first.
 */
export function rankQuickSwitch(
  query: string,
  labelFor: (item: QuickSwitchEntry) => string,
  entries: readonly QuickSwitchEntry[] = QUICK_SWITCH_ENTRIES,
): RankedEntry[] {
  const needle = normalizeQuery(query);
  if (!needle) return [];
  const rows: RankedEntry[] = [];
  for (const item of entries) {
    const label = labelFor(item);
    const prefix = labelStartsWith(label, needle);
    const matches =
      prefix ||
      (needle.length > SHORT_QUERY &&
        entryTerms(item, label).some((term) => scoreTerm(term, needle) > 0));
    if (matches) rows.push({ entry: item, label, prefix });
  }
  return rows.sort(
    (a, b) =>
      compareMatches(a, b) || Number(Boolean(a.entry.parentLabelKey)) - Number(Boolean(b.entry.parentLabelKey)),
  );
}

/**
 * Keep a Settings search hit only when one of its words STARTS with the query.
 *
 * The Settings search matches anywhere in a group's copy, which suits its own
 * sidebar but floods a launcher: "ide" hit six groups through "provide",
 * "side" and "slide". Capped as well, so sections stay on screen.
 */
export function strongSettingsMatches<T extends { label: string; detail?: string }>(
  query: string,
  matches: readonly T[],
  limit = 4,
): T[] {
  const needle = normalizeQuery(query);
  if (!needle) return [];
  return matches
    .filter((match) =>
      needle.length <= SHORT_QUERY
        ? labelStartsWith(match.label, needle)
        : [match.label, match.detail ?? ""].some((text) => scoreTerm(normalizeQuery(text), needle) >= 60),
    )
    .map((match) => ({ match, label: match.label, prefix: labelStartsWith(match.label, needle) }))
    .sort(compareMatches)
    .slice(0, limit)
    .map((row) => row.match);
}

/**
 * Keys of the entries whose label another entry also uses ("API Keys" is both a
 * page and a voice tab). The row then names its area as well, so two rows that
 * look identical never send someone to the wrong place.
 */
export function ambiguousEntryKeys(
  labelFor: (item: QuickSwitchEntry) => string,
  entries: readonly QuickSwitchEntry[] = QUICK_SWITCH_ENTRIES,
): Set<string> {
  const seen = new Map<string, string[]>();
  for (const item of entries) {
    const label = normalizeQuery(labelFor(item));
    seen.set(label, [...(seen.get(label) ?? []), item.key]);
  }
  return new Set([...seen.values()].filter((keys) => keys.length > 1).flat());
}
