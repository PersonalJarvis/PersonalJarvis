/** Presentation adapter for the agent cards' existing capability-pin protocol. */
import type { ToolChoice, ToolCategory } from "@/components/agentchat/toolChoices";
import { pluginFamily } from "@/lib/pluginFamilies";
import { resolveToolBrand } from "@/lib/toolBrand";
import type { MentionItem } from "./mentionItems";

function category(id: string): ToolCategory {
  const [kind, name = ""] = id.split(":");
  if (kind === "plugin") return "plugins";
  if (kind === "skill") return "skills";
  if (kind === "mcp" || kind === "mcp-server") return "mcp";
  if (kind === "cli") return "cli";
  if (/wiki|memory|remember|recall|contact/.test(name)) return "memory";
  if (/search-web|browse/.test(name)) return "web";
  return "system";
}

function choice(id: string, name: string, description = ""): ToolChoice {
  const brand = resolveToolBrand(name);
  const label = brand.brandId ? brand.label : name;
  return {
    id,
    label,
    description,
    category: category(id),
    group: name.split("/")[0],
    brand: brand.brandId ?? "",
    available: true,
    tool_names: [],
    skill: id.startsWith("skill:") ? name : "",
  };
}

export function mentionChoice(item: MentionItem): ToolChoice & { mentionValue: string } {
  const family = item.kind === "plugin" ? pluginFamily(item.toolName) ?? pluginFamily(item.key.replace(/^plugin:/, "")) : undefined;
  const base = choice(item.key, item.toolName || item.value, item.hint);
  return {
    ...base,
    mentionValue: item.value,
    label: item.label || base.label,
    brand: family?.id || base.brand,
    category: item.kind === "plugin" ? "plugins" : item.kind === "cli" ? "cli" : base.category,
    available: item.connected,
    tool_names: [item.toolName].filter(Boolean),
  };
}

/** Old conversations already carry validated pin IDs in their saved text. */
export function pinnedMessageChoices(text: string): ToolChoice[] {
  const ids = [...text.matchAll(/^\[tools:\s*([^\]\r\n]+)\]\s*$/gm)]
    .flatMap((match) => match[1].split(",").map((id) => id.trim()))
    .filter((id) => /^(plugin|mcp|cli|skill|core):[^\s]+$/.test(id));
  const tags = new Set([...text.matchAll(/(?:^|\s)@([^\s@]+)/g)].map((m) => m[1].toLowerCase()));
  const picked = new Map<string, ToolChoice>();
  for (const id of ids) {
    const name = id.slice(id.indexOf(":") + 1);
    const server = name.split("/")[0];
    const collapsed = id.startsWith("mcp:") && tags.has(server.toLowerCase());
    const key = collapsed ? `mcp-server:${server}` : id;
    picked.set(key, choice(key, collapsed ? server : name));
  }
  return [...picked.values()];
}

/** Both the structured chat receipt and older agent-card messages are supported. */
export function messageChoices(item: { text: string; toolChoices?: ToolChoice[] }): ToolChoice[] {
  return item.toolChoices?.length ? item.toolChoices : pinnedMessageChoices(item.text);
}

/** Remove only tokens whose selections are displayed separately below the text. */
export function withoutChoiceTokens(text: string, choices: ToolChoice[]): string {
  const names = new Set(choices.map((row) => row.id.slice(row.id.indexOf(":") + 1).toLowerCase()));
  return text
    .replace(/(^|\s)@([^\s@]+)/g, (whole, space: string, name: string) =>
      names.has(name.toLowerCase()) ? space : whole,
    )
    .replace(/[ \t]+\n/g, "\n")
    .trim();
}
