/** Inline plugin chips in the composer: `@gmail` in the sent text, a brand pill on screen. */

import type { ToolChoice } from "./toolChoices";

const CHIP_RE = /(@[^\s@]+)/g;

export function choiceTag(row: ToolChoice): string {
  // Keep the agent-card catalog's collision-free tag through chip serialization.
  if ("mentionValue" in row && typeof row.mentionValue === "string") return row.mentionValue;
  const raw = row.brand || row.id.replace(/^(plugin|mcp|cli|skill|core):/i, "");
  return raw.replace(/_/g, "-").toLowerCase();
}

export function choiceToken(row: ToolChoice): string {
  return `@${choiceTag(row)}`;
}

export function choiceLookup(choices: readonly ToolChoice[]): Map<string, ToolChoice> {
  const tags = new Map<string, ToolChoice>();
  for (const row of choices) {
    tags.set(choiceTag(row).toLowerCase(), row);
    if ("mentionValue" in row) continue;
    const rest = row.id.replace(/^(plugin|mcp|cli|skill|core):/i, "").toLowerCase();
    tags.set(rest.replace(/_/g, "-"), row);
    tags.set(rest.replace(/-/g, "_"), row);
    if (row.brand) tags.set(row.brand.toLowerCase().replace(/_/g, "-"), row);
  }
  return tags;
}

/** Split a stored message into text runs and the chips those `@tags` name. */
export function splitMessageChips(
  text: string,
  choices: readonly ToolChoice[],
): Array<{ type: "text"; text: string } | { type: "chip"; row: ToolChoice }> {
  if (!text) {
    return choices.length ? choices.map((row) => ({ type: "chip" as const, row })) : [];
  }
  const tags = choiceLookup(choices);
  const used = new Set<ToolChoice>();
  const parts: Array<{ type: "text"; text: string } | { type: "chip"; row: ToolChoice }> = [];
  const chunks = text.split(CHIP_RE);
  for (const chunk of chunks) {
    if (!chunk) continue;
    const name = chunk.startsWith("@") ? chunk.slice(1).toLowerCase() : "";
    const row = name ? tags.get(name) : undefined;
    if (row) {
      parts.push({ type: "chip", row });
      used.add(row);
    } else {
      parts.push({ type: "text", text: chunk });
    }
  }
  for (const row of choices) {
    if (!used.has(row)) parts.push({ type: "chip", row });
  }
  return parts;
}
