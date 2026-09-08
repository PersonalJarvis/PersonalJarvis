/**
 * Marketplace plugins as the user thinks of them: one YouTube, one Gmail —
 * not the skills, MCP tools or commands that sit behind the connection.
 *
 * The seed catalog is the canonical family list. Community plugins that
 * reuse a seed id fold into the same row; unknown `plugin:*` tools (coding
 * commands, device lists) are not a family and must not appear as plugins.
 */

import seedCatalog from "../../../../../marketplace/seed_catalog.json";

export interface PluginFamily {
  id: string;
  displayName: string;
  description: string;
  /** Hyphenated tag that lands after `@`. */
  tag: string;
}

type SeedPlugin = {
  id: string;
  display_name: string;
  description: string;
  native_tool?: string | null;
};

const seedPlugins = seedCatalog.plugins as SeedPlugin[];

export const PLUGIN_FAMILIES: PluginFamily[] = seedPlugins.map((plugin) => ({
  id: plugin.id,
  displayName: plugin.display_name,
  description: plugin.description,
  tag: plugin.id.replace(/_/g, "-"),
}));

const FAMILY_BY_ID = new Map(PLUGIN_FAMILIES.map((family) => [family.id, family]));

interface Needle {
  familyId: string;
  compact: string;
  alias: string;
}

function compact(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function needlesFor(plugin: SeedPlugin): Needle[] {
  const aliases = new Set<string>([
    plugin.id,
    plugin.id.replace(/_/g, "-"),
    `plugin-${plugin.id}`,
    `plugin-${plugin.id.replace(/_/g, "-")}`,
  ]);
  if (plugin.native_tool) aliases.add(plugin.native_tool);
  // Spoken / typed shorthand: "youtube" is YouTube Music, not a second plugin.
  if (plugin.id === "youtube_music") aliases.add("youtube");
  return [...aliases].map((alias) => ({
    familyId: plugin.id,
    compact: compact(alias),
    alias: alias.toLowerCase(),
  }));
}

const NEEDLES: Needle[] = seedPlugins
  .flatMap(needlesFor)
  .sort((a, b) => b.compact.length - a.compact.length || b.alias.length - a.alias.length);

function stripKind(raw: string): string {
  return raw.replace(/^(plugin|mcp|skill|cli|core):/i, "");
}

/**
 * The marketplace plugin this name belongs to, or undefined when it is not
 * a connector (a skill, a coding command, an MCP tool of some other server).
 *
 * Whole aliases and identity segments only: "nonlinear" is not Linear,
 * "drive-jarvis" is not Drive.
 */
export function marketplacePluginId(raw: string): string | undefined {
  const stripped = stripKind(raw.trim());
  if (!stripped) return undefined;
  const lowered = stripped.toLowerCase();
  const compacted = compact(stripped);
  const server = stripped.split(/[/:]/, 1)[0] ?? stripped;

  for (const needle of NEEDLES) {
    if (compact(server) === needle.compact || compacted === needle.compact) return needle.familyId;
  }
  for (const needle of NEEDLES) {
    if (
      lowered === needle.alias ||
      lowered.startsWith(`${needle.alias}/`) ||
      lowered.startsWith(`${needle.alias}-`) ||
      lowered.startsWith(`${needle.alias}_`)
    ) {
      return needle.familyId;
    }
  }
  const tokens = lowered.split(/[^a-z0-9]+/).filter((token) => token && token !== "plugin" && token !== "mcp");
  for (const needle of NEEDLES) {
    if (tokens.includes(needle.alias) || tokens.some((token) => compact(token) === needle.compact)) {
      return needle.familyId;
    }
  }
  return undefined;
}

export function pluginFamily(id: string): PluginFamily | undefined {
  return FAMILY_BY_ID.get(id);
}

export function isPluginOwnedSkill(slug: string): boolean {
  const trimmed = slug.trim();
  if (!trimmed) return false;
  return Boolean(marketplacePluginId(trimmed));
}
