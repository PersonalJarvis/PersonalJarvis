/** Mirrors the composer catalog's Pydantic model; selections send IDs only. */
export type ToolCategory =
  "plugins" | "skills" | "mcp" | "memory" | "web" | "files" | "automation" | "system" | "cli";
export const TOOL_CATEGORIES: ToolCategory[] = [
  "plugins",
  "skills",
  "mcp",
  "memory",
  "web",
  "files",
  "automation",
  "system",
  "cli",
];

export interface ToolChoice {
  id: string;
  label: string;
  description: string;
  category: ToolCategory;
  group: string;
  brand: string;
  available: boolean;
  tool_names: string[];
  skill: string;
}

export interface ToolSearchResult {
  items: ToolChoice[];
  mode: "browse" | "semantic" | "text";
  total: number;
}

export async function searchTools(
  params: Record<string, string>,
  signal: AbortSignal,
): Promise<ToolSearchResult> {
  const res = await fetch(`/api/agent-chat/tools?${new URLSearchParams(params)}`, { signal });
  if (!res.ok) throw new Error(`tool-search:${res.status}`);
  return res.json();
}

/** Old events have no selections. Discard malformed receipts. */
export function readToolChoices(value: unknown): ToolChoice[] {
  if (!Array.isArray(value)) return [];
  return value.filter((row): row is ToolChoice =>
    Boolean(
      row &&
      typeof row.id === "string" &&
      typeof row.label === "string" &&
      TOOL_CATEGORIES.includes(row.category) &&
      typeof row.brand === "string",
    ),
  );
}
