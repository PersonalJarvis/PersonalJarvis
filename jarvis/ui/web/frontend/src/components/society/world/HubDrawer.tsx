/**
 * What a click on a hub building opens: the things that hub stands for, as a
 * drawer over the island (one-viewer doctrine — the world keeps living behind
 * it), with a link to the app section. Data comes from the endpoints the
 * sections already use; nothing is invented. The drawer is app chrome, so it
 * wears the theme tokens; only the family swatches echo the buildings.
 */
import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink, X } from "lucide-react";

import { useT } from "@/i18n";
import { useEventStore, type SectionId } from "@/store/events";
import type { KitPlace } from "./islandLayout";

export interface Group {
  /** Locale key suffix under `society.world.` */
  labelKey: string;
  color: string;
  items: string[];
}

export interface HubConfig {
  titleKey: string;
  hintKey: string;
  /**
   * The app section this hub stands for, or null when it stands for none —
   * the Lookout watches the world outside, which has no page in this app. A
   * null section hides the footer link rather than inventing a destination.
   */
  section: SectionId | null;
  /** Absent for hubs that list nothing (the Foundry explains itself). */
  load?: () => Promise<Group[]>;
  bodyKey?: string;
  /** A hub whose contents are a fixed set of hands, not a live registry. */
  groups?: Group[];
}

/**
 * The hands behind the three work halls. They are a FIXED list because that is
 * what they are: the tool names `society/checkpoints.py` maps to each work
 * family. The drawer and the derivation must name the same tools, so the two
 * lists are pinned to each other by `hubGroups.test.ts`.
 */
const COMMS_TOOLS = ["gmail", "slack", "discord", "telegram"];
const COMMS_PEOPLE = ["contact-lookup", "contact-upsert", "call-contact", "society_message_agent"];
const DESKTOP_POINTER = ["click", "click-element", "type-text", "hotkey", "scroll", "drag", "move-mouse"];
const DESKTOP_SCREEN = [
  "open-app",
  "switch-window",
  "screen-snapshot",
  "read-visible-ui-state",
  "wait-for-ui-state",
  "wait-for-element",
  "inspect-pointer",
  "computer-use",
];
const WEB_SEARCH = ["search-web", "search-backends", "verify-via-curl"];
const WEB_BROWSER = ["society_browser"];

const PLUGIN_FAMILIES: ReadonlyArray<{ group: string; key: string; color: string }> = [
  { group: "jarvis.brain", key: "plugin_group_brain", color: "#9b5de5" },
  { group: "jarvis.tool", key: "plugin_group_tool", color: "#2ec4b6" },
  { group: "jarvis.stt", key: "plugin_group_stt", color: "#ff6f61" },
  { group: "jarvis.tts", key: "plugin_group_tts", color: "#ffb703" },
  { group: "jarvis.channel", key: "plugin_group_channel", color: "#06d6a0" },
  { group: "jarvis.realtime", key: "plugin_group_realtime", color: "#4cc9f0" },
  { group: "jarvis.wakeword", key: "plugin_group_wakeword", color: "#c9c2b2" },
  { group: "jarvis.harness", key: "plugin_group_harness", color: "#8b8f9c" },
];

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as T;
}

/** Per hub: strings, section and the loader — shared with the building card. */
export const HUBS: Record<KitPlace, HubConfig> = {
  plugins: {
    titleKey: "drawer_plugins_title",
    hintKey: "drawer_plugins_hint",
    section: "plugins",
    load: async () => {
      const data = await getJson<Record<string, string[]>>("/api/plugins");
      return PLUGIN_FAMILIES.map((f) => ({
        labelKey: f.key,
        color: f.color,
        items: [...(data[f.group] ?? [])].sort(),
      }));
    },
  },
  skills: {
    titleKey: "drawer_skills_title",
    hintKey: "drawer_skills_hint",
    section: "skills",
    load: async () => {
      const data = await getJson<{
        skills: Array<{ name: string; state: string; is_builtin: boolean }>;
      }>("/api/skills");
      const skills = data.skills ?? [];
      const by = (pred: (s: { state: string; is_builtin: boolean }) => boolean) =>
        skills.filter(pred).map((s) => s.name).sort();
      return [
        { labelKey: "skills_group_draft", color: "#ff9f1c", items: by((s) => s.state === "draft") },
        { labelKey: "skills_group_custom", color: "#ffd166", items: by((s) => !s.is_builtin && s.state !== "draft") },
        { labelKey: "skills_group_builtin", color: "#c9c2b2", items: by((s) => s.is_builtin && s.state !== "draft") },
      ];
    },
  },
  mcp: {
    titleKey: "drawer_mcp_title",
    hintKey: "drawer_mcp_hint",
    section: "mcps",
    load: async () => {
      const data = await getJson<{ servers: Array<{ name: string; display?: string; transport?: string }> }>(
        "/api/mcps",
      );
      const servers = data.servers ?? [];
      const label = (s: { name: string; display?: string }) => s.display || s.name;
      return [
        { labelKey: "mcp_group_stdio", color: "#4cc9f0", items: servers.filter((s) => s.transport !== "http").map(label).sort() },
        { labelKey: "mcp_group_http", color: "#9b5de5", items: servers.filter((s) => s.transport === "http").map(label).sort() },
      ];
    },
  },
  cli: {
    titleKey: "drawer_cli_title",
    hintKey: "drawer_cli_hint",
    section: "clis",
    load: async () => {
      const data = await getJson<{
        clis: Array<{ name: string; display_name?: string; installed: boolean; connected: boolean }>;
      }>("/api/clis");
      const clis = data.clis ?? [];
      const label = (c: { name: string; display_name?: string }) => c.display_name || c.name;
      return [
        { labelKey: "cli_group_connected", color: "#2ec4b6", items: clis.filter((c) => c.connected).map(label).sort() },
        { labelKey: "cli_group_installed", color: "#ffd166", items: clis.filter((c) => c.installed && !c.connected).map(label).sort() },
        { labelKey: "cli_group_missing", color: "#c9c2b2", items: clis.filter((c) => !c.installed).map(label).sort() },
      ];
    },
  },
  foundry: {
    titleKey: "drawer_foundry_title",
    hintKey: "drawer_foundry_hint",
    section: "agents",
    bodyKey: "drawer_foundry_body",
  },
  comms: {
    titleKey: "drawer_comms_title",
    hintKey: "drawer_comms_hint",
    section: "contacts",
    groups: [
      { labelKey: "comms_group_mail", color: "#06d6a0", items: COMMS_TOOLS },
      { labelKey: "comms_group_people", color: "#4cc9f0", items: COMMS_PEOPLE },
    ],
  },
  desktop: {
    titleKey: "drawer_desktop_title",
    hintKey: "drawer_desktop_hint",
    section: "settings",
    groups: [
      { labelKey: "desktop_group_pointer", color: "#ff5fa2", items: DESKTOP_POINTER },
      { labelKey: "desktop_group_screen", color: "#ffb703", items: DESKTOP_SCREEN },
    ],
  },
  web: {
    titleKey: "drawer_web_title",
    hintKey: "drawer_web_hint",
    section: null,
    groups: [
      { labelKey: "web_group_search", color: "#4cc9f0", items: WEB_SEARCH },
      { labelKey: "web_group_browser", color: "#2ec4b6", items: WEB_BROWSER },
    ],
  },
  models: {
    titleKey: "drawer_models_title",
    hintKey: "drawer_models_hint",
    section: "local-models",
    bodyKey: "drawer_models_body",
  },
  civic: {
    titleKey: "drawer_civic_title",
    hintKey: "drawer_civic_hint",
    section: "board",
    bodyKey: "drawer_civic_body",
  },
  gallery: {
    titleKey: "drawer_gallery_title",
    hintKey: "drawer_gallery_hint",
    section: "visualization",
    bodyKey: "drawer_gallery_body",
  },
};

export function HubDrawer({ hub, onClose }: { hub: KitPlace; onClose: () => void }) {
  const t = useT();
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const cfg = HUBS[hub];
  const groups = useQuery({
    queryKey: ["society", "hub", hub],
    queryFn: cfg.load ?? (async () => []),
    enabled: Boolean(cfg.load),
    staleTime: 60_000,
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const shown = cfg.groups ?? groups.data;
  const total = shown ? shown.reduce((n, g) => n + g.items.length, 0) : 0;

  return (
    <aside
      className="absolute inset-y-3 right-3 z-30 flex w-[340px] max-w-[85%] flex-col overflow-hidden rounded-lg border border-border bg-popover text-foreground shadow-float"
      role="dialog"
      aria-label={t(`society.world.${cfg.titleKey}`)}
    >
      <header className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <h2 className="font-display text-base font-semibold tracking-tight">{t(`society.world.${cfg.titleKey}`)}</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">{t(`society.world.${cfg.hintKey}`)}</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label={t("society.world.drawer_close")}
          className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <X size={16} />
        </button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {cfg.bodyKey && <p className="text-sm text-foreground">{t(`society.world.${cfg.bodyKey}`)}</p>}
        {cfg.load && groups.isLoading && <p className="text-sm text-muted-foreground">{t("society.world.drawer_loading")}</p>}
        {groups.isError && <p className="text-sm text-destructive">{String(groups.error)}</p>}
        {cfg.load && groups.data && total === 0 && <p className="text-sm text-muted-foreground">{t("society.world.drawer_empty")}</p>}
        {shown
          ?.filter((g) => g.items.length > 0)
          .map((g) => (
            <section key={g.labelKey} className="mb-4">
              <h3 className="mb-1.5 flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: g.color }} aria-hidden />
                {t(`society.world.${g.labelKey}`)}
                <span className="tabular-nums">{g.items.length}</span>
              </h3>
              <ul className="flex flex-wrap gap-1.5">
                {g.items.map((n) => (
                  <li key={n} className="rounded-md bg-secondary px-2 py-1 font-mono text-xs text-foreground">
                    {n}
                  </li>
                ))}
              </ul>
            </section>
          ))}
      </div>
      {cfg.section ? (
        <footer className="border-t border-border px-4 py-3">
          <button
            type="button"
            onClick={() => setActiveSection(cfg.section as SectionId)}
            className="inline-flex h-8 items-center gap-2 rounded-md bg-secondary px-3 text-sm font-medium text-foreground hover:bg-muted"
          >
            <ExternalLink size={14} />
            {t("society.world.drawer_open_section")}
          </button>
        </footer>
      ) : null}
    </aside>
  );
}
