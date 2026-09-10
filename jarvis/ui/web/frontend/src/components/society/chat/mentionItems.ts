/**
 * What "@" offers in a society chat: teammates, connected plugins, MCP
 * servers, CLIs, skills and Jarvis' own tools.
 *
 * The picker shows a short tag (`@gmail`, `@github`) rather than the catalog
 * id (`plugin:gmail`). Sending still pins the real capability ids so the
 * agent reaches the right hands. MCP servers collapse to one row while
 * browsing; typing a tool name unfolds the individual tools.
 */

import { PLUGIN_FAMILIES, marketplacePluginId, pluginFamily } from "@/lib/pluginFamilies";

import type { Capability, SocietyAgent } from "../data";
import type { AgentStatus } from "@/lib/agenticIdeApi";

/** "plugin:gmail" → "gmail"; "mcp:github/create_issue" → "github/create_issue". */
function capabilityName(id: string): string {
  return id.replace(/^(plugin|cli|mcp|skill|core):/, "");
}

export type MentionKind = "agent" | "plugin" | "mcp" | "cli" | "skill" | "core" | "coding";
export type MentionGroup = "agents" | "plugins" | "mcp" | "cli" | "skills" | "tools" | "coding";

export interface MentionItem {
  key: string;
  kind: MentionKind;
  group: MentionGroup;
  /** What lands after `@`. */
  value: string;
  label: string;
  hint: string;
  /** Capability ids this tag pins for the turn; empty for agents. */
  pinIds: string[];
  agent?: SocietyAgent;
  codingAgent?: AgentStatus;
  connected: boolean;
  searchText: string;
  /** Individual MCP tools — hidden until the query names them. */
  detail: boolean;
  /** Registry / tool name the brand resolver reads. */
  toolName: string;
}

const GROUP_ORDER: readonly MentionGroup[] = [
  "agents",
  "coding",
  "plugins",
  "mcp",
  "cli",
  "skills",
  "tools",
];

const KIND_GROUP: Record<string, MentionGroup> = {
  plugin: "plugins",
  cli: "cli",
  mcp: "mcp",
  skill: "skills",
  core: "tools",
};

export interface MentionPlugin {
  id: string;
  display_name: string;
  description: string;
  native_tool?: string | null;
}

function kindOf(cap: Capability): MentionKind {
  const prefix = cap.id.split(":")[0];
  if (prefix === "plugin" || prefix === "cli" || prefix === "mcp" || prefix === "skill" || prefix === "core") {
    return prefix;
  }
  if (
    cap.kind === "plugin" ||
    cap.kind === "cli" ||
    cap.kind === "mcp" ||
    cap.kind === "skill" ||
    cap.kind === "core"
  ) {
    return cap.kind;
  }
  return "plugin";
}

function mcpServer(id: string): string {
  const rest = capabilityName(id);
  const slash = rest.indexOf("/");
  return slash === -1 ? rest : rest.slice(0, slash);
}

function takeValue(wanted: string, fallback: string, taken: Set<string>): string {
  const tryOne = (candidate: string): string | null => {
    candidate = candidate.trim().replace(/\s+/g, "-");
    const key = candidate.toLowerCase();
    if (!candidate || taken.has(key)) return null;
    taken.add(key);
    return candidate;
  };
  const value = tryOne(wanted) ?? tryOne(fallback);
  if (value) return value;
  for (let suffix = 2; ; suffix += 1) {
    const unique = tryOne(`${fallback}-${suffix}`);
    if (unique) return unique;
  }
}

function searchBlob(parts: Array<string | undefined | null>): string {
  return parts
    .filter((p): p is string => Boolean(p && p.trim()))
    .join(" ")
    .toLowerCase();
}

function capabilityItem(
  cap: Capability,
  value: string,
  opts: {
    key: string;
    detail: boolean;
    pinIds?: string[];
    label?: string;
    hint?: string;
    toolName?: string;
    connected?: boolean;
  },
): MentionItem {
  const kind = kindOf(cap);
  const aliases = cap.aliases ?? [];
  return {
    key: opts.key,
    kind,
    group: KIND_GROUP[kind] ?? "plugins",
    value,
    label: opts.label ?? cap.label ?? value,
    hint: opts.hint ?? cap.one_liner ?? "",
    pinIds: opts.pinIds ?? [cap.id],
    connected: opts.connected ?? cap.connected,
    searchText: searchBlob([
      value,
      cap.id,
      cap.label,
      cap.one_liner,
      cap.tool_name,
      kind,
      ...aliases,
    ]),
    detail: opts.detail,
    toolName: opts.toolName ?? cap.tool_name ?? value,
  };
}

/**
 * Every taggable row: agents first (they keep their names), then one row per
 * connected marketplace plugin (skills and commands folded in), remaining MCP
 * servers, CLIs, standalone skills and Jarvis tools.
 */
export function buildMentionCatalog(
  agents: readonly SocietyAgent[],
  capabilities: readonly Capability[],
  codingAgents: readonly AgentStatus[] = [],
  plugins: readonly MentionPlugin[] = [],
): MentionItem[] {
  const taken = new Set<string>();
  const items: MentionItem[] = [];

  for (const agent of agents) {
    const value = takeValue(agent.name, agent.agentId, taken);
    items.push({
      key: `agent:${agent.agentId}`,
      kind: "agent",
      group: "agents",
      value,
      label: agent.name,
      hint: agent.title,
      pinIds: [],
      agent,
      connected: true,
      searchText: searchBlob([agent.name, agent.title, agent.agentId]),
      detail: false,
      toolName: "",
    });
  }

  for (const agent of codingAgents) {
    if (agent.kind === "shell") continue;
    const value = takeValue(agent.name, `coding/${agent.name}`, taken);
    items.push({
      key: `coding:${agent.name}`, kind: "coding", group: "coding", value,
      label: agent.display_name, hint: agent.description ?? "",
      pinIds: ["core:coding-session"], codingAgent: agent,
      connected: agent.installed && agent.accepts_prompts !== false, detail: false, toolName: agent.name,
      searchText: searchBlob([value, `coding/${agent.name}`, agent.name, agent.display_name, "coding message terminal IDE"]),
    });
  }

  const families = new Map<string, Capability[]>();
  const mcpByServer = new Map<string, Capability[]>();
  const rest: Capability[] = [];
  const livePlugins = new Map(plugins.map((plugin) => [plugin.id, plugin]));
  const familyIds = new Set([...PLUGIN_FAMILIES.map((plugin) => plugin.id), ...livePlugins.keys()]);
  const familyFor = (cap: Capability): string | undefined => {
    const kind = kindOf(cap);
    const name = capabilityName(cap.id);
    if (kind === "cli" || kind === "core") return undefined;
    if (kind === "skill" && !name.startsWith("plugin-")) return undefined;
    const identity = kind === "mcp" ? mcpServer(cap.id) : kind === "skill" ? name.slice(7) : name;
    // Only an exact owner identity can fold a row, never a tool/skill's prose or suffix.
    const owner = [...familyIds].find((id) => id === identity || id.replace(/_/g, "-") === identity);
    if (owner) return owner;
    const native = plugins.find((plugin) => plugin.native_tool === (cap.tool_name || name));
    if (kind === "plugin" && native) return native.id;
    const seeded = marketplacePluginId(identity);
    const family = seeded ? pluginFamily(seeded) : undefined;
    return family && [family.id, family.tag, "plugin-" + family.id].includes(identity) ? family.id : undefined;
  };
  const seenCapabilities = new Set<string>();
  for (const cap of capabilities) {
    if (seenCapabilities.has(cap.id)) continue;
    seenCapabilities.add(cap.id);
    if (cap.id === "core:coding-session" && codingAgents.length) continue;
    const familyId = familyFor(cap);
    if (familyId) {
      const bucket = families.get(familyId);
      if (bucket) bucket.push(cap);
      else families.set(familyId, [cap]);
      continue;
    }
    const kind = kindOf(cap);
    if (kind === "plugin") {
      // Coding commands and other non-connector tools are not plugins.
      continue;
    }
    if (kind === "mcp") {
      const server = mcpServer(cap.id);
      const bucket = mcpByServer.get(server);
      if (bucket) bucket.push(cap);
      else mcpByServer.set(server, [cap]);
    } else {
      rest.push(cap);
    }
  }

  for (const plugin of plugins) {
    if (!families.has(plugin.id)) families.set(plugin.id, []);
  }
  for (const [familyId, members] of families) {
    const family = pluginFamily(familyId);
    const installed = livePlugins.get(familyId);
    const live = members.filter((cap) => kindOf(cap) !== "skill");
    const connected = live.some((cap) => cap.connected);
    const representative =
      members.find((cap) => kindOf(cap) === "plugin") ?? live[0] ?? members[0] ?? {
        id: `plugin:${familyId}`, kind: "plugin", label: installed?.display_name ?? familyId,
        one_liner: installed?.description ?? "", connected: false, risk_tier: "monitor", tool_name: "",
      };
    const tag = family?.tag ?? familyId.replace(/_/g, "-");
    const value = takeValue(tag, `plugin:${familyId}`, taken);
    const item = capabilityItem(representative, value, {
      key: `plugin:${familyId}`,
      detail: false,
      pinIds: members.map((cap) => cap.id),
      label: installed?.display_name ?? family?.displayName ?? representative.label,
      hint: installed?.description || family?.description || representative.one_liner,
      toolName: familyId,
      connected,
    });
    items.push({
      ...item,
      kind: "plugin",
      group: "plugins",
      searchText: searchBlob([
        item.searchText,
        family?.displayName,
        family?.description,
        family?.tag,
        ...members.flatMap((cap) => [
          cap.id,
          cap.label,
          cap.one_liner,
          cap.tool_name,
          ...(cap.aliases ?? []),
        ]),
      ]),
    });
  }

  for (const cap of rest) {
    const short = capabilityName(cap.id);
    const value = takeValue(short, cap.id, taken);
    items.push(
      capabilityItem(cap, value, {
        key: cap.id,
        detail: false,
      }),
    );
  }

  for (const [server, tools] of mcpByServer) {
    const connected = tools.some((c) => c.connected);
    const pinIds = tools.map((c) => c.id);
    const value = takeValue(server, `mcp:${server}`, taken);
    const representative = tools[0];
    items.push(
      capabilityItem(representative, value, {
        key: `mcp-server:${server}`,
        detail: false,
        pinIds,
        label: server,
        hint: representative.one_liner,
        toolName: server,
        connected,
      }),
    );

    if (tools.length === 1) continue;
    for (const cap of tools) {
      const short = capabilityName(cap.id);
      const toolValue = takeValue(short, cap.id, taken);
      items.push(
        capabilityItem(cap, toolValue, {
          key: cap.id,
          detail: true,
        }),
      );
    }
  }

  return items;
}

function scoreItem(item: MentionItem, q: string): number | null {
  const value = item.value.toLowerCase();
  const label = item.label.toLowerCase();
  if (value.startsWith(q) || label.startsWith(q)) return 0;
  if (value.includes(q) || label.includes(q)) return 1;
  if (item.searchText.includes(q) || item.hint.toLowerCase().includes(q)) return 2;
  return null;
}

/**
 * Empty query: the browse list (no disconnected rows, no individual MCP
 * tools). A query searches everything, disconnected included, ranked.
 */
export function filterMentions(items: readonly MentionItem[], query: string): MentionItem[] {
  const q = query.trim().toLowerCase();
  if (!q) {
    return groupMentions(items.filter((item) => !item.detail && (item.connected || item.kind === "coding")))
      .flatMap((group) => group.items);
  }
  const ranked: { score: number; index: number; item: MentionItem }[] = [];
  items.forEach((item, index) => {
    const score = scoreItem(item, q);
    if (score === null) return;
    ranked.push({ score, index, item });
  });
  ranked.sort((a, b) => a.score - b.score || a.index - b.index);
  return groupMentions(ranked.map((r) => r.item)).flatMap((group) => group.items);
}

export function groupMentions(
  items: readonly MentionItem[],
): { group: MentionGroup; items: MentionItem[] }[] {
  const buckets = new Map<MentionGroup, MentionItem[]>();
  for (const item of items) {
    const bucket = buckets.get(item.group);
    if (bucket) bucket.push(item);
    else buckets.set(item.group, [item]);
  }
  return GROUP_ORDER.filter((group) => (buckets.get(group)?.length ?? 0) > 0).map((group) => ({
    group,
    items: buckets.get(group) ?? [],
  }));
}

const TOKEN_RE = /(?:^|\s)@([^\s@]+)/g;

export function codingMentionsInText(text: string, items: readonly MentionItem[]): MentionItem[] {
  const values = new Set([...text.matchAll(TOKEN_RE)].map((m) => m[1].toLowerCase()));
  return items.filter((item) => item.codingAgent && item.connected && (
    values.has(item.value.toLowerCase()) || values.has(`coding/${item.codingAgent.name}`.toLowerCase())
  ));
}

/** Explicit user selection, not a teammate dispatch or an arbitrary shell command. */
export function codingAssignmentHint(items: readonly MentionItem[], folder: string): string {
  if (!items.length) return "";
  return [
    "[Coding assignment selected by the user]",
    `Coding CLI IDs: ${JSON.stringify(items.map((item) => item.codingAgent!.name))}.`,
    "Acknowledge the selected coding agents by name. Selection alone is not evidence of startup or delivery.",
    folder.trim()
      ? `Project directory: ${JSON.stringify(folder.trim())}.`
      : "Use the project directory specified in the user's message. If it is unclear, ask for it.",
    "Use coding-session to discover and open or explicitly target the correct IDE session, then use assign " +
      "with a self-contained task and user-grounded done_when criteria to supervise it to completion. " +
      "Answer ordinary follow-up questions yourself. These are coding CLIs, not society teammates. " +
      "Follow approval rules; accepted is not completed.",
  ].map((line) => `[coding-agent] ${line}`).join("\n");
}

/**
 * Agents named in `text` and the capability ids those tags pin.
 *
 * Appearance order; a more specific tag (`@github/create_issue`) suppresses
 * the server tag (`@github`) so the two are not both pinned.
 */
export function mentionsInText(
  text: string,
  items: readonly MentionItem[],
): { agents: SocietyAgent[]; pinIds: string[] } {
  const tokens = [...text.matchAll(TOKEN_RE)].map((m) => m[1]);
  if (tokens.length === 0) return { agents: [], pinIds: [] };

  const byValue = new Map<string, MentionItem[]>();
  for (const item of items) {
    const key = item.value.toLowerCase();
    const list = byValue.get(key);
    if (list) list.push(item);
    else byValue.set(key, [item]);
    if (item.codingAgent) byValue.set(`coding/${item.codingAgent.name}`.toLowerCase(), [item]);
  }

  const seen = tokens.map((token) => token.toLowerCase());
  const used = new Set<string>();
  const agents: SocietyAgent[] = [];
  const pinIds: string[] = [];
  for (const token of tokens) {
    const lower = token.toLowerCase();
    if (used.has(lower)) continue;
    // A shorter tag sitting next to a more specific one (`@github` beside
    // `@github/create_issue`) must not also fire.
    if (
      seen.some(
        (other) => other !== lower && (other.startsWith(`${lower}/`) || other.startsWith(`${lower}:`)),
      )
    ) {
      continue;
    }
    used.add(lower);
    const hits = byValue.get(lower);
    if (!hits) continue;
    for (const item of hits) {
      if (item.agent) agents.push(item.agent);
      for (const id of item.pinIds) {
        if (!pinIds.includes(id)) pinIds.push(id);
      }
    }
  }
  return { agents, pinIds };
}

/** The `@` token the caret sits in, or null. */
export function mentionToken(
  text: string,
  caret: number,
): { query: string; start: number } | null {
  const at = Math.max(0, Math.min(caret, text.length));
  const before = text.slice(0, at);
  const m = /(?:^|\s)@([^\s@]*)$/.exec(before);
  if (!m) return null;
  return { query: m[1], start: at - m[1].length - 1 };
}
