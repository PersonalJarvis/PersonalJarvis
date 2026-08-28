/**
 * Tool call → the line a person reads in the reasoning trace.
 *
 * A tool step knows the registry name and the arguments of the call
 * ("wiki-recall" + {query}), nothing more. The trace wants what the Claude
 * desktop app shows: a sentence in the product's own words — "Looking in
 * Wiki · Urlaub 2026", "Creating artifact · Sales deck", "Running skill ·
 * daily-brief" — with the feature's own mark in front of it, or the
 * service's brand logo when the tool belongs to Gmail, Spotify, an MCP
 * server… This module is that pure mapping, so every surface that renders a
 * tool row (chat column, voice lane, history) says the same thing.
 *
 * Families, not tool names, carry the wording: thirty tools fold into a
 * dozen lines a non-developer understands. A tool nobody listed falls back
 * to its brand (lib/toolBrand) or a humanised name — never to nothing.
 */

import { resolveToolBrand, type ToolBrand } from "@/lib/toolBrand";

/** What a tool row is about — picks the icon and the verb. */
export type ToolFamily =
  | "wiki"
  | "wiki_write"
  | "artifact"
  | "skill"
  | "skill_create"
  | "web"
  | "screen"
  | "screen_recall"
  | "control"
  | "navigate"
  | "app"
  | "memory"
  | "profile"
  | "contact"
  | "call"
  | "worker"
  | "shell"
  | "model"
  | "mcp_admin"
  | "settings"
  | "verify"
  | "mcp"
  | "service"
  | "other";

export interface ToolStepView {
  family: ToolFamily;
  /** i18n key under "tool_steps.*" for the verb line; null when the brand label IS the line. */
  labelKey: string | null;
  /** The brand mark when the tool belongs to a known service (or MCP server). */
  brand: ToolBrand | null;
  /** Human line when there is no i18n key (brand label, MCP "Server · tool", humanised name). */
  label: string;
  /** Short runtime detail — the query, title, url, app, command… Untranslated. */
  detail: string;
}

/** Registry name (normalised: lower, "-"/"_" equal) → family. */
const FAMILY_BY_TOOL: Record<string, ToolFamily> = {
  wiki_recall: "wiki",
  wiki_list: "wiki",
  wiki_page_read: "wiki",
  wiki_ingest: "wiki_write",
  create_artifact: "artifact",
  run_skill: "skill",
  create_skill: "skill_create",
  search_web: "web",
  search_backends: "web",
  screenshot: "screen",
  screen_snapshot: "screen",
  awareness_snapshot: "screen",
  read_visible_ui_state: "screen",
  inspect_pointer: "screen",
  awareness_recall: "screen_recall",
  computer_use: "control",
  click: "control",
  click_element: "control",
  type_text: "control",
  scroll: "control",
  drag: "control",
  move_mouse: "control",
  hotkey: "control",
  switch_window: "control",
  wait_for_element: "control",
  wait_for_ui_state: "control",
  reset_orb_position: "control",
  navigate: "navigate",
  open_app: "app",
  app_command: "app",
  remember: "memory",
  update_profile: "profile",
  contact_lookup: "contact",
  contact_upsert: "contact",
  call_contact: "call",
  spawn_worker: "worker",
  spawn_subagents: "worker",
  multi_spawn: "worker",
  dispatch_to_harness: "worker",
  dispatch_with_review: "worker",
  dispatch_to_admin: "worker",
  run_shell: "shell",
  switch_provider: "model",
  manage_mcp_server: "mcp_admin",
  describe_app_settings: "settings",
  whoami: "settings",
  reveal_key_preview: "settings",
  start_preview_server: "verify",
  verify_localhost: "verify",
  verify_via_curl: "verify",
};

/** Families whose line is a verb from i18n (the rest speak through their brand). */
const LABEL_KEY: Partial<Record<ToolFamily, string>> = {
  wiki: "tool_steps.wiki",
  wiki_write: "tool_steps.wiki_write",
  artifact: "tool_steps.artifact",
  skill: "tool_steps.skill",
  skill_create: "tool_steps.skill_create",
  web: "tool_steps.web",
  screen: "tool_steps.screen",
  screen_recall: "tool_steps.screen_recall",
  control: "tool_steps.control",
  navigate: "tool_steps.navigate",
  app: "tool_steps.app",
  memory: "tool_steps.memory",
  profile: "tool_steps.profile",
  contact: "tool_steps.contact",
  call: "tool_steps.call",
  worker: "tool_steps.worker",
  shell: "tool_steps.shell",
  model: "tool_steps.model",
  mcp_admin: "tool_steps.mcp_admin",
  settings: "tool_steps.settings",
  verify: "tool_steps.verify",
};

/** Argument keys tried, in order, for the detail of each family. */
const DETAIL_KEYS: Partial<Record<ToolFamily, string[]>> = {
  wiki: ["query", "slug", "title", "page", "topic"],
  wiki_write: ["title", "slug", "url", "path"],
  artifact: ["title", "name", "kind", "type"],
  skill: ["name", "skill", "skill_name"],
  skill_create: ["name", "title"],
  web: ["query", "q", "url"],
  screen: ["window", "window_title", "region"],
  screen_recall: ["query", "question"],
  control: ["text", "target", "selector", "keys", "title", "direction"],
  navigate: ["url", "target", "section", "path"],
  app: ["app", "name", "command", "app_name"],
  memory: ["key", "text", "fact", "content"],
  profile: ["field", "key"],
  contact: ["name", "query", "contact"],
  call: ["name", "contact", "number"],
  worker: ["task", "utterance", "prompt", "goal", "instruction"],
  shell: ["command", "cmd"],
  model: ["provider", "model", "name"],
  mcp_admin: ["name", "server", "action"],
  settings: ["key", "section"],
  verify: ["url", "port", "path"],
  mcp: ["query", "q", "title", "name", "path", "url", "id"],
  service: ["query", "q", "subject", "title", "name", "playlist", "track", "url"],
  other: ["query", "text", "name", "title", "url"],
};

const DETAIL_MAX = 72;

function normalise(toolName: string): string {
  return toolName.trim().toLowerCase().replace(/[-\s.]+/g, "_");
}

function clip(text: string, max = DETAIL_MAX): string {
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length > max ? `${clean.slice(0, max - 1)}…` : clean;
}

/** First short, readable argument of the call, by the family's preferred keys. */
export function pickToolDetail(family: ToolFamily, args: unknown): string {
  if (!args || typeof args !== "object") return "";
  const record = args as Record<string, unknown>;
  const keys = DETAIL_KEYS[family] ?? DETAIL_KEYS.other ?? [];
  for (const key of keys) {
    const v = record[key];
    if (typeof v === "string" && v.trim()) return clip(v);
    if (typeof v === "number" && Number.isFinite(v)) return String(v);
  }
  // Nothing by name: the first string value that reads like something.
  for (const v of Object.values(record)) {
    if (typeof v === "string" && v.trim() && v.trim().length <= 120) return clip(v);
  }
  return "";
}

/** "Cursor · Word" — the humanised last token(s) of a control tool name. */
function humanise(name: string): string {
  return name
    .split("_")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/**
 * Describe one tool call for the trace.
 *
 * Order of resolution:
 *   1. a listed Jarvis tool → its family's verb + the call's detail;
 *   2. "server/tool" → an MCP call: the server's brand (when bundled) and
 *      "Server · tool" as the line;
 *   3. a tool whose name carries a bundled brand (gmail_search, plugin-spotify)
 *      → the brand's logo and label (the service IS the line);
 *   4. anything else → humanised name, monogram tile.
 */
export function describeToolStep(toolName: string, args?: unknown): ToolStepView {
  const raw = (toolName ?? "").trim();
  const key = normalise(raw);
  const family = FAMILY_BY_TOOL[key];
  if (family) {
    return {
      family,
      labelKey: LABEL_KEY[family] ?? null,
      brand: null,
      label: humanise(key),
      detail: pickToolDetail(family, args),
    };
  }

  const slash = raw.indexOf("/");
  if (slash > 0) {
    const server = raw.slice(0, slash);
    const tool = raw.slice(slash + 1);
    const brand = resolveToolBrand(server);
    const serverLabel = brand.logoUrl ? brand.label : humanise(normalise(server));
    return {
      family: "mcp",
      labelKey: null,
      brand: { ...brand, label: serverLabel },
      label: tool ? `${serverLabel} · ${humanise(normalise(tool))}` : serverLabel,
      detail: pickToolDetail("mcp", args),
    };
  }

  const brand = resolveToolBrand(raw);
  if (brand.logoUrl) {
    return {
      family: "service",
      labelKey: null,
      brand,
      label: brand.label,
      detail: pickToolDetail("service", args),
    };
  }
  return {
    family: "other",
    labelKey: null,
    brand,
    label: brand.label || humanise(key) || "?",
    detail: pickToolDetail("other", args),
  };
}
