import en from "@/i18n/locales/en.json";
import de from "@/i18n/locales/de.json";
import es from "@/i18n/locales/es.json";
import type { UiLanguage } from "@/i18n";

type LocaleTree = Record<string, unknown>;

const LOCALES: Record<UiLanguage, LocaleTree> = { en, de, es };

/** Each Settings page group owns the copy used to search its controls. */
const SEARCH_GROUPS = [
  { id: "languages", keys: ["language", "languages_group_title"] },
  { id: "app", keys: ["app_settings_group_title", "autostart", "appearance", "jarvis_api"] },
  { id: "permissions", keys: ["nav.permissions"] },
  { id: "screen-context", keys: ["screen_context"] },
  { id: "realtime-voice", keys: ["realtime_voice"] },
  { id: "system-prompt", keys: ["system_prompt"] },
  { id: "wake-word", keys: ["wake_word"] },
  { id: "silence-window", keys: ["silence_window"] },
  { id: "volume", keys: ["volume"] },
  { id: "audio-devices", keys: ["audio_devices"] },
  { id: "music", keys: ["music", "music_group_title"] },
  { id: "keybinds", keys: ["keybinds"] },
  { id: "more", keys: ["rows", "team_proxy", "codex_title", "safety_title"] },
  { id: "overlay-taskbar", keys: ["overlay_style", "bar_size", "overlay_taskbar_group_title"] },
] as const;

const SEARCH_PAGES = [
  { id: "profile", keys: ["profile_view"] },
  { id: "agent-instructions", keys: ["agent_instructions"] },
  { id: "contacts", keys: ["contacts"] },
  { id: "socials", keys: ["socials"] },
  { id: "apikeys", keys: ["apikeys_view", "apikeys_voice", "apikeys_model", "apikeys_cu_model"] },
  { id: "wallpaper", keys: ["home.background_label", "home.background_hint"] },
  { id: "costs", keys: ["costs_view"] },
  { id: "feedback", keys: ["feedback"] },
] as const;

export interface SettingsOptionMatch {
  id: string;
  label: string;
  detail?: string;
}

function normalize(value: string): string {
  return value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
}

function atPath(tree: LocaleTree, path: string): unknown {
  return path.split(".").reduce<unknown>((node, part) =>
    node && typeof node === "object" ? (node as LocaleTree)[part] : undefined, tree);
}

function stringsIn(node: unknown): string[] {
  if (typeof node === "string") return [node];
  if (!node || typeof node !== "object") return [];
  return Object.values(node).flatMap(stringsIn);
}

function matchingCopy(tree: LocaleTree, keys: readonly string[], needle: string): string[] {
  return keys.flatMap((key) => stringsIn(atPath(tree, key)))
    .filter((value) => normalize(value).includes(needle));
}

function shortDetail(matches: string[], label: string): string | undefined {
  const detail = matches.filter((value) => value !== label)
    .sort((a, b) => a.length - b.length)[0];
  return detail && detail.length <= 100 ? detail : undefined;
}

/** Search localized option copy, not just the ten Settings tab names. */
export function searchSettingsOptions(
  language: UiLanguage,
  query: string,
  translate: (key: string) => string,
): SettingsOptionMatch[] {
  const needle = normalize(query.trim());
  if (!needle) return [];
  const settings = atPath(LOCALES[language], "settings_view") as LocaleTree;

  return SEARCH_GROUPS.flatMap(({ id, keys }) => {
    const label = translate(`settings_view.nav.${id.replaceAll("-", "_")}`);
    const matches = matchingCopy(settings, keys, needle);
    if (!normalize(label).includes(needle) && matches.length === 0) return [];
    // Prefer a control label to a long explanation or a saved-state toast.
    return [{ id, label, detail: shortDetail(matches, label) }];
  });
}

/** Search the other Settings pages' localized field copy. */
export function searchSettingsPages(
  language: UiLanguage,
  query: string,
  translate: (key: string) => string,
) {
  const needle = normalize(query.trim());
  if (!needle) return [];
  const locale = LOCALES[language];

  return SEARCH_PAGES.flatMap(({ id, keys }) => {
    const label = translate(`nav.${id === "agent-instructions" ? "agent_instructions" : id}`);
    const matches = matchingCopy(locale, keys, needle);
    if (!normalize(label).includes(needle) && matches.length === 0) return [];
    return [{ id, label, detail: shortDetail(matches, label) }];
  });
}
